"""사용자 노출 문구 가드 (process.md P1, §7.4).

단순 서브스트링 검사는 한국어에서 거의 무력하다. "대소문자 무시"는 한글에
적용되지 않고, 실제 회피면은 띄어쓰기와 어미 변화다. 실측 결과 회피 9종 중
8종이 원안 검사를 통과했다. 그래서 아래 3단으로 막는다.

  1) 정규화: NFKC + 모든 공백/개행 제거 + 소문자화
  2) 부정 검사: 정규화된 서브스트링 집합 + 어간 정규식
  3) 긍정 단언: 낙찰가 밴드를 언급한 블록에는 반드시 "참고" 수식어가 있어야 한다
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# 정규화(공백 제거) 후 비교하는 금지 표현
BANNED_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "낙찰확률",
        "낙찰될확률",
        "낙찰가능성",
        "낙찰될가능성",
        "이길수있는",
        "낙찰보장",
        "무조건낙찰",
        "승리입찰가",
        "이금액이면낙찰",
        "winningbidprobability",
    }
)

# 표기 변형을 잡는 어간 패턴 (정규화된 문자열에 적용)
BANNED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"낙찰[가-힣]{0,3}(확률|가능성)"),
    re.compile(r"(확률|가능성)[은는이가]?\d+%"),
    re.compile(r"이금액이면[가-힣]*(됩니다|낙찰)"),
)

# 밴드를 언급했는지 판별하는 패턴
BAND_MENTION = re.compile(r"(밴드|p10|p50|p90|예상낙찰가|낙찰가구간)")
# 밴드 언급 시 반드시 함께 있어야 하는 수식어
BAND_MODIFIERS: tuple[str, ...] = ("참고정보", "참고용", "참고지표", "참고구간")


class ForbiddenPhraseError(ValueError):
    """사용자 노출 문자열에 금지 표현이 포함됨."""


def normalize(text: str) -> str:
    """공백·전각·대소문자 차이를 제거해 회피면을 없앤다."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", "", text)
    return text.lower()


def find_violations(text: str) -> list[str]:
    """금지 표현 목록을 돌려준다. 비어 있으면 통과."""
    norm = normalize(text)
    hits: list[str] = []
    for phrase in sorted(BANNED_SUBSTRINGS):
        if phrase in norm:
            hits.append(f"금지 표현 '{phrase}'")
    for pattern in BANNED_PATTERNS:
        m = pattern.search(norm)
        if m:
            hits.append(f"금지 패턴 '{pattern.pattern}' (매칭: {m.group()})")
    return hits


def find_band_modifier_violations(text: str) -> list[str]:
    """밴드를 언급했는데 '참고' 수식어가 없는 블록을 찾는다."""
    hits: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        norm = normalize(block)
        if BAND_MENTION.search(norm) and not any(
            mod in norm for mod in BAND_MODIFIERS
        ):
            preview = block.strip().replace("\n", " ")[:60]
            hits.append(f"밴드 언급에 '참고 정보' 수식어 누락: {preview}")
    return hits


def assert_clean(text: str, *, where: str = "text", check_band: bool = True) -> str:
    """위반 시 예외. 통과하면 원문을 그대로 돌려준다."""
    hits = find_violations(text)
    if check_band:
        hits += find_band_modifier_violations(text)
    if hits:
        raise ForbiddenPhraseError(f"[{where}] " + " / ".join(hits))
    return text


def iter_strings(obj: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """중첩 구조(pydantic 모델/dict/list)에서 모든 문자열을 경로와 함께 뽑아낸다."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if isinstance(obj, str):
        yield path or "<root>", obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from iter_strings(v, f"{path}[{i}]")


def assert_object_clean(obj: Any, *, where: str = "object") -> None:
    """FinalVerdict 같은 중첩 객체 전체를 재귀 검사한다.

    금지 표현은 모든 문자열에 대해 검사하고, '참고 정보' 수식어 단언은
    문단 구조가 있는 리포트 본문에만 적용한다(짧은 라벨에는 무의미하므로).
    """
    for field_path, value in iter_strings(obj, where):
        check_band = field_path.endswith("report_markdown")
        assert_clean(value, where=field_path, check_band=check_band)
