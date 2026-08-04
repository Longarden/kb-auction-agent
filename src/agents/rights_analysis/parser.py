"""매각물건명세서·등기사항전부증명서 규칙 파서. 순수 함수, LLM 없음.

설계 의도: 샘플 한 건이 아니라 대법원 양식의 '구조'에 맞춰 쓴다.
법원마다 날짜 표기(2022.09.05. / 2022년 9월 5일 / 2022-09-05), 금액 표기
(60,000,000원 / 금6,000만원 / 6천만원), 표 구분자(파이프 / 공백 정렬)가 다르므로
표기 정규화 → 섹션 분할 → 표 헤더 기반 열 매핑 순으로 처리한다.

파일 읽기는 전부 src/utils/io 를 통한다(raw open 금지).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

from src.schemas.core import RightType
from src.utils.errors import AgentError
from src.utils.io import read_text

MODULE = "rights_analysis"

# ── 문서 로딩 ────────────────────────────────────────────────
def load_document(path: str | Path) -> str:
    """.pdf 면 pdfplumber 로, 그 외에는 io.read_text 로 읽는다."""
    if str(path).lower().endswith(".pdf"):
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - 선택 의존성
            raise AgentError(
                MODULE,
                "PDF 문서를 읽으려면 pdfplumber 가 필요합니다. "
                "`pip install -e .[pdf]` 로 설치하거나 텍스트 추출본(.txt)을 사용하세요.",
                recoverable=False,
            ) from exc
        from src.utils.io import resolve

        pages: list[str] = []
        with pdfplumber.open(resolve(path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        raw = "\n".join(pages)
        return unicodedata.normalize("NFKC", raw.replace("\r\n", "\n"))
    return read_text(path)


# ── 표기 정규화 ─────────────────────────────────────────────
def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _collapse(s: str) -> str:
    """공백을 한 칸으로 줄인다(문장 비교용)."""
    return re.sub(r"\s+", " ", _nfkc(s)).strip()


def _squash(s: str) -> str:
    """공백을 전부 제거한다(키워드/헤더 판정용). 한국어는 띄어쓰기가 회피면이다."""
    return re.sub(r"\s+", "", _nfkc(s))


# ── 날짜 ────────────────────────────────────────────────────
_DATE_RE = re.compile(
    r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*[.일]?"
)
_DATE_COMPACT_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def parse_korean_date(s: Optional[str]) -> Optional[date]:
    """2022.09.05. / 2022.9.5 / 2022-09-05 / 2022년 9월 5일 / 20220905 를 모두 받는다.

    io.read_text 가 NFKC 를 적용하므로 전각(２０２２．０９．０５)은 이미 반각이다.
    """
    if not s:
        return None
    text = _nfkc(s)
    m = _DATE_RE.search(text) or _DATE_COMPACT_RE.search(text)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


# ── 금액 ────────────────────────────────────────────────────
_SMALL_UNITS = (("천", 1000), ("백", 100), ("십", 10))
_BIG_UNITS = (("억", 10**8), ("만", 10**4))
_AMOUNT_TOKEN = re.compile(r"금?(\d[\d억만천백십]*)원")
_AMOUNT_BARE = re.compile(r"^금?(\d[\d억만천백십]*)원?$")


def _parse_small(token: str) -> Optional[int]:
    """'6천', '1천5백', '6000' 처럼 만 단위 미만 덩어리를 푼다."""
    if not token:
        return None
    total = 0
    rest = token
    matched = False
    for unit, mult in _SMALL_UNITS:
        m = re.match(r"^(\d*)" + unit, rest)
        if m:
            total += (int(m.group(1)) if m.group(1) else 1) * mult
            rest = rest[m.end():]
            matched = True
    if rest:
        if not rest.isdigit():
            return None
        total += int(rest)
        matched = True
    return total if matched else None


def _parse_number_token(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    total = 0
    rest = token
    for unit, mult in _BIG_UNITS:
        idx = rest.find(unit)
        if idx >= 0:
            value = _parse_small(rest[:idx])
            if value is None:
                return None
            total += value * mult
            rest = rest[idx + 1:]
    if rest:
        value = _parse_small(rest)
        if value is None:
            return None
        total += value
    return total or None


def parse_korean_amount(s: Optional[str]) -> Optional[int]:
    """60,000,000원 / 금60,000,000원 / 6,000만원 / 금6,000만원 / 6천만원 / 60000000."""
    if not s:
        return None
    text = _squash(s).replace(",", "")
    if not text:
        return None
    m = _AMOUNT_TOKEN.search(text)
    if m:
        return _parse_number_token(m.group(1))
    m = _AMOUNT_BARE.match(text)
    if m:
        return _parse_number_token(m.group(1))
    return None


def _find_max_amount(text: str) -> Optional[int]:
    """문장 안의 모든 '…원' 금액 중 최댓값(보수적으로 큰 쪽을 택한다 — P4)."""
    squashed = _squash(text).replace(",", "")
    values = [
        v for v in (_parse_number_token(m.group(1)) for m in _AMOUNT_TOKEN.finditer(squashed))
        if v
    ]
    return max(values) if values else None


# ── 성명 마스킹 (P8) ─────────────────────────────────────────
# 마스킹 문자는 법원/변환기마다 다르다(○ ㅇ * X ×).
_MASK_CHARS = "○ㅇoO0*Xx×"
_MASKED_NAME_RE = re.compile(rf"^[가-힣][{_MASK_CHARS}]+$")
# 셀 '안'에서 이름을 찾을 때는 마스킹 문자 집합을 좁힌다. o/O/0 까지 넣으면
# '3층0' 같은 토막이 이름으로 잡혀 레코드가 잘못 쪼개진다.
_NAME_IN_CELL = re.compile(r"[가-힣][○ㅇ●*×Xx]{1,3}")
_ROLE_PREFIXED_NAME = re.compile(r"(?:임차인|점유자|소유자|채무자)\s*([가-힣]{2,4})$")
_PLAIN_NAME_RE = re.compile(r"^[가-힣]{2,4}$")

#: 성명 정규화용(‘미상’도 성명불상으로 본다)
UNKNOWN_OCCUPANT_TOKENS = ("성명불상", "불상", "미상")
#: 레코드 시작 판정용. ‘미상’은 확정일자·차임 칸에도 흔해서 제외한다.
_RECORD_START_UNKNOWN = ("성명불상", "불상")


def mask_korean_name(name: str) -> str:
    """미마스킹 실명이면 첫 글자만 남기고 ○ 로 가린다. 이미 가려졌으면 그대로."""
    token = _collapse(name)
    if not token:
        return "성명불상"
    if _squash(token) in UNKNOWN_OCCUPANT_TOKENS:
        return "성명불상"
    if _MASKED_NAME_RE.match(token):
        return token[0] + "○" * (len(token) - 1)
    if _PLAIN_NAME_RE.match(token) and token not in _ROLE_WORDS:
        return token[0] + "○" * (len(token) - 1)
    return token


# 점유자 표에서 성명 칸으로 오해하기 쉬운 역할어. 레코드 시작 판정에서 제외한다.
_ROLE_WORDS = frozenset(
    {
        "임차인", "소유자", "점유자", "채무자", "전부", "일부", "미상", "없음", "있음",
        "주거", "상가", "점포", "현황조사", "권리신고", "임대차", "확정일자",
        "전입신고", "배당요구", "성명불상", "불상", "보증금", "차임", "본건",
    }
)


# ── 섹션 분할 ───────────────────────────────────────────────
SEC_PREAMBLE = "PREAMBLE"
SEC_TENANT = "TENANT_TABLE"
SEC_NOT_EXTINGUISHED = "NOT_EXTINGUISHED"
SEC_SUPERFICIES = "SUPERFICIES"
SEC_REMARKS = "REMARKS"
SEC_NOTE = "NOTE"
SEC_BOILERPLATE = "BOILERPLATE"
SEC_FOOTER = "FOOTER"

SECTION_LABELS = {
    SEC_NOT_EXTINGUISHED: "매각으로 소멸되지 아니하는 권리",
    SEC_SUPERFICIES: "지상권의 개요",
    SEC_REMARKS: "비고란",
    SEC_NOTE: "<비고>",
}

_PAGE_NOISE_RE = re.compile(r"^-*\d+-*$")
_SEPARATOR_RE = re.compile(r"^[-=_~]{3,}$")
_FOOTNOTE_RE = re.compile(r"^주\d+[:：]")
_NO_ITEM_RE = re.compile(r"^(해당사항없음|해당없음|없음|조사된임차내역없음|임차인없음)\.?$")


def _is_page_noise(squashed: str) -> bool:
    if not squashed:
        return False
    if _PAGE_NOISE_RE.match(squashed):
        return True
    if squashed.startswith("사건번호"):
        return True
    return False


def _section_of(squashed: str) -> Optional[str]:
    if "소멸되지아니하는것" in squashed or "소멸되지아니하는권리" in squashed:
        return SEC_NOT_EXTINGUISHED
    if "지상권의개요" in squashed:
        return SEC_SUPERFICIES
    if squashed.startswith("비고란"):
        return SEC_REMARKS
    if squashed.startswith("<비고>"):
        return SEC_NOTE
    if squashed.startswith("※"):
        return SEC_BOILERPLATE
    if _FOOTNOTE_RE.match(squashed):
        return SEC_FOOTER
    return None


#: 점유자 표 헤더의 최소 열 수. 산문 문단(1열)과 표 머리글을 가르는 기준.
_TENANT_HEADER_MIN_CELLS = 4
#: 헤더가 접혀 있을 때 이어 붙여 볼 최대 줄 수.
_TENANT_HEADER_LOOKAHEAD = 2


def _tenant_header_span(lines: list[str], index: int) -> Optional[int]:
    """lines[index] 가 점유자 표 헤더의 첫 줄이면 헤더가 끝나는 줄 번호를 돌려준다.

    실제 명세서는 헤더가 길어 두 줄로 접히는 일이 잦다. 한 줄만 보고 판정하면
    표 전체를 놓치므로 최대 3줄 창으로 본다. 다만 앞의 안내 문단
    ('부동산의 점유자와 점유의 권원 …')도 같은 낱말을 담고 있으므로,
    열이 4개 이상인 '표 머리글'만 헤더로 인정한다.
    """
    head = _squash(lines[index])
    if "점유자" not in head and "성명" not in head:
        return None
    if len(_split_cells(lines[index])) < _TENANT_HEADER_MIN_CELLS:
        return None
    for extra in range(_TENANT_HEADER_LOOKAHEAD + 1):
        end = index + extra
        window = _squash(" ".join(lines[index: end + 1]))
        if "배당요구" in window:
            return end
    return None


def _split_sections(text: str) -> list[tuple[str, list[str]]]:
    """(섹션명, 본문 줄들) 목록. 헤더 줄 자체는 본문에 넣지 않는다."""
    lines = _nfkc(text).split("\n")
    sections: list[tuple[str, list[str]]] = [(SEC_PREAMBLE, [])]
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        squashed = _squash(raw_line)
        if _is_page_noise(squashed):
            i += 1
            continue
        header_end = _tenant_header_span(lines, i)
        if header_end is not None:
            sections.append((SEC_TENANT, []))
            i = header_end + 1
            continue
        name = _section_of(squashed)
        if name is not None:
            sections.append((name, []))
            i += 1
            continue
        sections[-1][1].append(raw_line)
        i += 1
    return sections


def _section_text(sections: list[tuple[str, list[str]]], name: str) -> str:
    parts = ["\n".join(lines) for sec, lines in sections if sec == name]
    return "\n".join(parts)


def _section_lines(sections: list[tuple[str, list[str]]], name: str) -> list[str]:
    out: list[str] = []
    for sec, lines in sections:
        if sec == name:
            out.extend(lines)
    return out


# ── 문장 분할 ───────────────────────────────────────────────
_SENTENCE_SPLIT = re.compile(r"(?<=[가-힣])\.\s*")


def _sentences(text: str) -> list[str]:
    joined = re.sub(r"\n+", "\n", _nfkc(text)).strip()
    if not joined:
        return []
    out: list[str] = []
    for chunk in _SENTENCE_SPLIT.split(joined.replace("\n", " ")):
        s = _collapse(chunk)
        if s and not _NO_ITEM_RE.match(_squash(s)):
            out.append(s if s.endswith(".") else s + ".")
    return out


# ── 표 셀 분해 ──────────────────────────────────────────────
def _split_cells(line: str) -> list[str]:
    """파이프 구분표와 공백 정렬표를 모두 받는다."""
    text = _nfkc(line)
    parts = text.split("|") if "|" in text else re.split(r"\s{2,}", text)
    cells = [_collapse(p) for p in parts]
    while cells and not cells[0]:
        cells.pop(0)
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _name_candidate(cell: str) -> Optional[str]:
    """셀에서 사람 이름으로 볼 수 있는 토큰을 뽑는다. 없으면 None.

    '김○○' 뿐 아니라 '임차인 김○○', '임차인 김철수' 처럼 역할어가 붙은 표기도 잡는다.
    """
    squashed = _squash(cell)
    if not squashed:
        return None
    m = _NAME_IN_CELL.search(squashed)
    if m:
        return m.group()
    if _PLAIN_NAME_RE.match(squashed) and squashed not in _ROLE_WORDS:
        return squashed
    m = _ROLE_PREFIXED_NAME.search(_collapse(cell))
    if m and m.group(1) not in _ROLE_WORDS:
        return m.group(1)
    return None


def _looks_like_record_start(line: str) -> bool:
    """레코드 첫 줄인가.

    성명이 항상 첫 칸에 오지는 않는다(순번 열이 앞에 붙는 양식이 있다).
    그래서 앞 두 칸까지 성명 후보를 찾는다. 뒤쪽 칸까지 보면 확정일자·배당요구
    칸의 값이 성명으로 오인되어 한 레코드가 여러 개로 쪼개진다.
    """
    cells = _split_cells(line)
    if not cells:
        return False
    if _squash(cells[0]) in _RECORD_START_UNKNOWN:
        return True
    return any(_name_candidate(c) is not None for c in cells[:2])


# ── 등기목적 -> RightType ────────────────────────────────────
# 긴 표현을 먼저 본다. '소유권이전청구권가등기' 가 '소유권이전' 에 먹히면 안 된다.
_RIGHT_KEYWORDS: tuple[tuple[str, Optional[RightType]], ...] = (
    ("소유권이전청구권가등기", RightType.TRANSFER_PROV_REG),
    ("소유권이전청구권", RightType.TRANSFER_PROV_REG),
    ("담보가등기", RightType.COLLATERAL_PROV_REG),
    ("근저당권설정", RightType.MORTGAGE),
    ("저당권설정", RightType.MORTGAGE),
    ("근저당권", RightType.MORTGAGE),
    ("저당권", RightType.MORTGAGE),
    ("가압류", RightType.SEIZURE),
    ("압류", RightType.SEIZURE),
    ("임의경매개시결정", RightType.AUCTION_START),
    ("강제경매개시결정", RightType.AUCTION_START),
    ("경매개시결정", RightType.AUCTION_START),
    ("전세권설정", RightType.JEONSE_RIGHT),
    ("전세권", RightType.JEONSE_RIGHT),
    ("가처분", RightType.INJUNCTION),
    ("토지별도등기", RightType.LAND_SEPARATE_REGISTRY),
    ("소유권이전", None),   # 명시적으로 무시(말소기준 판단과 무관)
)


def map_right_type(text: str) -> Optional[RightType]:
    squashed = _squash(text)
    for keyword, right_type in _RIGHT_KEYWORDS:
        if keyword in squashed:
            return right_type
    return None


# ── 특수권리 키워드 ─────────────────────────────────────────
_SPECIAL_KEYWORDS: tuple[tuple[str, RightType], ...] = (
    ("유치권", RightType.LIEN),
    ("법정지상권", RightType.STATUTORY_SUPERFICIES),
    ("분묘기지권", RightType.GRAVE_BASE),
    ("소유권이전청구권가등기", RightType.TRANSFER_PROV_REG),
    ("가처분", RightType.INJUNCTION),
    ("대지권미등기", RightType.LAND_SEPARATE_REGISTRY),
    ("대지권없음", RightType.LAND_SEPARATE_REGISTRY),
    ("토지별도등기", RightType.LAND_SEPARATE_REGISTRY),
)

# '토지별도등기 없음' 처럼 키워드 직후에 부정어가 오면 적발로 보지 않는다.
_NEGATION_WINDOW = re.compile(r"(없음|없다|없습니다|존재하지않|해당사항없음)")


def _is_negated(squashed_sentence: str, keyword: str) -> bool:
    idx = squashed_sentence.find(keyword)
    if idx < 0:
        return True
    tail = squashed_sentence[idx + len(keyword): idx + len(keyword) + 12]
    return bool(_NEGATION_WINDOW.search(tail))


# ── 배당요구 판정 ───────────────────────────────────────────
def parse_dividend_demanded(cell: Optional[str]) -> Optional[bool]:
    """없음/하지아니 -> False, 있음/배당요구함/일자 -> True, 미상/공란 -> None."""
    if cell is None:
        return None
    squashed = _squash(cell)
    if not squashed:
        return None
    if "하지아니" in squashed or "안함" in squashed or "미제출" in squashed:
        return False
    if "없음" in squashed:
        return False
    if "있음" in squashed or "배당요구함" in squashed or "신고함" in squashed:
        return True
    if squashed in UNKNOWN_OCCUPANT_TOKENS or "미상" in squashed:
        return None
    if parse_korean_date(cell) is not None:
        return True
    return None


def _parse_optional_date_cell(cell: Optional[str]) -> Optional[date]:
    if cell is None:
        return None
    if "미상" in _squash(cell):
        return None
    return parse_korean_date(cell)


def _parse_optional_amount_cell(cell: Optional[str]) -> Optional[int]:
    if cell is None:
        return None
    squashed = _squash(cell)
    if not squashed or "미상" in squashed or "없음" in squashed:
        return None
    return parse_korean_amount(cell) or _find_max_amount(cell)


# ── 점유자 표 ───────────────────────────────────────────────
def _header_column_map(header_line: str) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for i, cell in enumerate(_split_cells(header_line)):
        squashed = _squash(cell)
        if "성명" in squashed:
            mapping.setdefault("name", i)
        elif "보증금" in squashed:
            mapping.setdefault("deposit", i)
        elif "전입" in squashed or "사업자등록" in squashed:
            mapping.setdefault("move_in", i)
        elif "확정일자" in squashed:
            mapping.setdefault("fixed", i)
        elif "배당요구" in squashed:
            mapping.setdefault("dividend", i)
    return mapping


def _find_tenant_header(text: str) -> Optional[str]:
    """열 매핑에 쓸 헤더. 접혀 있으면 이어 붙여 한 줄로 돌려준다."""
    lines = _nfkc(text).split("\n")
    for i in range(len(lines)):
        end = _tenant_header_span(lines, i)
        if end is not None:
            return "  ".join(l.strip() for l in lines[i: end + 1])
    return None


def _group_tenant_records(lines: list[str], expected_cells: Optional[int] = None) -> list[list[str]]:
    """구분선/빈 줄/새 성명 등장 지점에서 레코드를 끊는다.

    한 임차인의 정보가 여러 물리적 줄에 걸치는 양식이 흔하다. 성명처럼 보이는
    토큰만 보고 끊으면 그런 레코드가 두 개로 쪼개진다. 그래서 표 헤더의 열 수를
    알고 있으면 '현재 레코드가 아직 열 수를 못 채웠으면 끊지 않는다'는 제약을 건다.
    """
    records: list[list[str]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            records.append(current)
            current = []

    def is_complete() -> bool:
        if expected_cells is None:
            return True
        return len(_split_cells("  ".join(current))) >= expected_cells

    for raw in lines:
        stripped = raw.strip()
        squashed = _squash(stripped)
        if not stripped:
            flush()
            continue
        if _SEPARATOR_RE.match(squashed) or _is_page_noise(squashed):
            flush()
            continue
        if _NO_ITEM_RE.match(squashed):
            flush()
            continue
        if current and is_complete() and _looks_like_record_start(stripped):
            flush()
        current.append(stripped)
    flush()
    return [r for r in records if _looks_like_record_start(r[0])]


_LABEL_MOVE_IN = re.compile(r"(전입신고|전입일|사업자등록)[^0-9]{0,8}([0-9][^|]{0,20})")
_LABEL_FIXED = re.compile(r"확정일자[^0-9가-힣]{0,4}([^|]{0,20})")
_LABEL_DEPOSIT = re.compile(r"보증금[^0-9금]{0,4}([^|]{0,24})")
_LABEL_DIVIDEND = re.compile(r"배당요구[^|]{0,20}")


def _parse_tenant_record(record_text: str, column_map: dict[str, int]) -> dict:
    cells = _split_cells(record_text)
    # 성명은 첫 칸이 아닐 수 있다(순번 열). 앞 두 칸에서 후보를 찾는다.
    name_cell: Optional[str] = next(
        (_name_candidate(c) for c in cells[:2] if _name_candidate(c)), None
    )
    if name_cell is None and cells:
        name_cell = cells[0]
    move_in_cell = fixed_cell = deposit_cell = dividend_cell = None
    deposit_from_text: Optional[int] = None

    if column_map and len(cells) == max(column_map.values()) + 1:
        # 1순위: 표 헤더 기반 위치 매핑(가장 일반적이고 정확).
        def at(key: str) -> Optional[str]:
            idx = column_map.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else None

        name_cell = at("name") or name_cell
        move_in_cell, fixed_cell = at("move_in"), at("fixed")
        deposit_cell, dividend_cell = at("deposit"), at("dividend")
    else:
        # 2순위: 라벨 앵커. 3순위: 공식 양식의 뒤쪽 3열 고정 순서(전입·확정·배당요구).
        m = _LABEL_MOVE_IN.search(record_text)
        if m:
            move_in_cell = m.group(2)
        m = _LABEL_FIXED.search(record_text)
        if m:
            fixed_cell = m.group(1)
        m = _LABEL_DEPOSIT.search(record_text)
        if m:
            deposit_cell = m.group(1)
        m = _LABEL_DIVIDEND.search(record_text)
        if m:
            dividend_cell = m.group(0)
        if len(cells) >= 4:
            # 공식 양식의 마지막 세 열은 전입신고일자·확정일자·배당요구여부로 고정이다.
            dividend_cell = dividend_cell or cells[-1]
            fixed_cell = fixed_cell or cells[-2]
            move_in_cell = move_in_cell or cells[-3]
        if deposit_cell is None:
            # 라벨도 위치도 못 잡으면 레코드 전체에서 가장 큰 금액을 보증금으로 본다.
            deposit_from_text = _find_max_amount(record_text)

    return {
        "name_masked": mask_korean_name(name_cell or ""),
        "move_in_date": _iso(_parse_optional_date_cell(move_in_cell)),
        "fixed_date": _iso(_parse_optional_date_cell(fixed_cell)),
        "deposit": _parse_optional_amount_cell(deposit_cell) or deposit_from_text,
        "dividend_demanded": parse_dividend_demanded(dividend_cell),
        "evidence": _collapse(record_text)[:200],
    }


# ── 등기부 ──────────────────────────────────────────────────
def _parse_registry(registry_text: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    log: list[str] = []
    for raw in _nfkc(registry_text).split("\n"):
        cells = _split_cells(raw)
        if len(cells) < 2:
            continue
        right_type: Optional[RightType] = None
        type_idx = -1
        for i, cell in enumerate(cells):
            mapped = map_right_type(cell)
            if mapped is not None:
                right_type, type_idx = mapped, i
                break
        if right_type is None:
            continue
        date_idx = next(
            (i for i, c in enumerate(cells) if i != type_idx and parse_korean_date(c)), None
        )
        if date_idx is None:
            # 접수일자가 없는 줄은 표 머리글이거나 비고 문구다(예: '토지별도등기 없음').
            continue
        registered = parse_korean_date(cells[date_idx])
        holder = ""
        for cell in cells[date_idx + 1:]:
            if parse_korean_amount(cell) is not None:
                continue
            if _squash(cell):
                holder = cell
                break
        if not holder:
            holder = "미상"
        rows.append(
            {
                "right_type": right_type.value,
                "registered_date": _iso(registered),
                "holder": mask_korean_name(holder) if _PLAIN_NAME_RE.match(_squash(holder)) else holder,
            }
        )
        log.append(f"등기부: {right_type.value} {_iso(registered)} ({holder})")
    return rows, log


# ── 특수권리 ────────────────────────────────────────────────
def _scan_special_rights(sections: list[tuple[str, list[str]]]) -> tuple[list[dict], list[str]]:
    """소멸되지 않는 권리 / 지상권 개요 / 비고란 / <비고> 만 훑는다.

    '※ 최선순위 설정일자보다 …' 안내 문단은 별도 섹션으로 떼어 두었으므로
    보일러플레이트가 오탐을 만들지 않는다.
    """
    hits: dict[str, dict] = {}
    log: list[str] = []
    for section in (SEC_NOT_EXTINGUISHED, SEC_SUPERFICIES, SEC_REMARKS, SEC_NOTE):
        body = _section_text(sections, section)
        label = SECTION_LABELS[section]
        for sentence in _sentences(body):
            squashed = _squash(sentence)
            for keyword, right_type in _SPECIAL_KEYWORDS:
                if keyword not in squashed or _is_negated(squashed, keyword):
                    continue
                amount = _find_max_amount(sentence)
                evidence = f"매각물건명세서 {label}: '{sentence}'"
                key = right_type.value
                prev = hits.get(key)
                if prev is None or (prev["claimed_amount"] is None and amount is not None):
                    hits[key] = {
                        "right_type": key,
                        "claimed_amount": amount,
                        "evidence": evidence,
                    }
                    log.append(f"특수권리 적발: {key} ({label})")
                break  # 한 문장에서 권리 하나만 잡는다(중복 계상 방지)
    return list(hits.values()), log


# ── 최선순위 설정 ───────────────────────────────────────────
_BASE_HINT_RE = re.compile(r"최선순위\s*설정[^0-9가-힣]*(.+)")


def _parse_base_right_hint(preamble: str) -> tuple[Optional[dict], list[str]]:
    for raw in _nfkc(preamble).split("\n"):
        if "최선순위" not in _squash(raw):
            continue
        m = _BASE_HINT_RE.search(_collapse(raw))
        if not m:
            continue
        tail = m.group(1)
        parsed_date = parse_korean_date(tail)
        right_type = map_right_type(tail)
        if parsed_date is None and right_type is None:
            continue
        hint = {
            "right_type": right_type.value if right_type else None,
            "registered_date": _iso(parsed_date),
            "evidence": f"매각물건명세서 최선순위 설정: '{_collapse(raw)}'",
        }
        return hint, [f"최선순위 설정란: {hint['registered_date']} {hint['right_type']}"]
    return None, []


# ── 진입점 ──────────────────────────────────────────────────
def extract_rule(sale_spec_text: str, registry_text: str) -> tuple[dict, list[str]]:
    """(RawExtraction 모양의 dict, 근거 로그)."""
    sections = _split_sections(sale_spec_text)
    evidence: list[str] = []

    hint, hint_log = _parse_base_right_hint(_section_text(sections, SEC_PREAMBLE))
    evidence.extend(hint_log)

    rights, registry_log = _parse_registry(registry_text)
    evidence.extend(registry_log)

    header = _find_tenant_header(sale_spec_text)
    column_map = _header_column_map(header) if header else {}
    expected_cells = len(_split_cells(header)) if header else None
    tenants: list[dict] = []
    for record in _group_tenant_records(_section_lines(sections, SEC_TENANT), expected_cells):
        tenants.append(_parse_tenant_record("  ".join(record), column_map))
        evidence.append(f"점유자 레코드: {tenants[-1]['name_masked']}")
    if not tenants:
        evidence.append("점유자 표에서 임차 내역을 찾지 못했습니다(소유자 점유로 취급).")

    special_rights, special_log = _scan_special_rights(sections)
    evidence.extend(special_log)

    data = {
        "base_right_hint": hint,
        "rights": rights,
        "tenants": tenants,
        "special_rights": special_rights,
        "notes": [],
    }
    return data, evidence
