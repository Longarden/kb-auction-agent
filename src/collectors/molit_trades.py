"""T1b 수집기 — 국토교통부 아파트 매매 실거래가.

    python -m src.collectors.molit_trades --region 30170 --months 24 --out data/processed/trades.parquet

[주의] 인증키 (onbid.py 와 같은 계정·같은 키)
    공공데이터포털은 인증키를 **Encoding** 과 **Decoding** 두 형태로 준다.
    반드시 **Decoding 키**를 .env 의 DATA_GO_KR_SERVICE_KEY 에 넣어야 한다.
    Encoding 키를 넣으면 requests 가 params 를 다시 URL 인코딩해 `%2F` 가
    `%252F` 로 이중 인코딩되고, 서버가 SERVICE_KEY_IS_NOT_REGISTERED_ERROR 를
    돌려준다. 키가 정상 등록돼 있어도 이 메시지가 뜬다.

이 API 는 (지역코드, 계약월) 단위 조회라 months 만큼 월별로 반복 호출한다.

3층 구조: fetch_page[네트워크] / parse_page[순수] / collect[fetch+parse+저장]
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

from src.collectors import _http
from src.collectors.onbid import SERVICE_KEY_ENV
from src.utils.env import require
from src.utils.errors import AgentError

log = logging.getLogger(__name__)

MODULE = "collectors.molit_trades"

BASE_URL = (
    "http://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
)
DEFAULT_ROWS_PER_PAGE = 1000
OK_RESULT_CODE = "000"

# 신규 영문 태그 → 구 한글 태그. 응답 스펙이 두 형태로 돌아다녀 둘 다 읽는다.
_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "deal_amount": ("dealAmount", "거래금액"),
    "area_m2": ("excluUseAr", "전용면적"),
    "deal_year": ("dealYear", "년"),
    "deal_month": ("dealMonth", "월"),
    "deal_day": ("dealDay", "일"),
    "build_year": ("buildYear", "건축년도"),
    "floor": ("floor", "층"),
    "umd_nm": ("umdNm", "법정동"),
    "apt_nm": ("aptNm", "아파트"),
}


def _text(node: Any, tags: tuple[str, ...]) -> str:
    for tag in tags:
        found = node.find(tag)
        if found is not None and found.text is not None:
            return found.text.strip()
    return ""


def _to_int(raw: str) -> int:
    digits = re.sub(r"[^\d-]", "", raw or "")
    if digits in {"", "-"}:
        return 0
    return int(digits)


def _to_float(raw: str) -> Optional[float]:
    cleaned = re.sub(r"[^\d.\-]", "", raw or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def month_sequence(as_of: date, months: int) -> list[str]:
    """as_of 에서 과거로 months 개월 'YYYYMM' 목록(최신 → 과거).

    시계를 읽지 않는다. 기준일은 반드시 호출자가 준다.
    """
    out: list[str] = []
    year, month = as_of.year, as_of.month
    for _ in range(max(months, 1)):
        out.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def fetch_page(
    service_key: str,
    region_code: str,
    deal_ym: str,
    page: int = 1,
    rows: int = DEFAULT_ROWS_PER_PAGE,
) -> str:
    """[네트워크] 한 페이지 원문 XML. region_code 는 법정동코드 앞 5자리."""
    params = {
        "serviceKey": service_key,
        "LAWD_CD": region_code,
        "DEAL_YMD": deal_ym,
        "numOfRows": rows,
        "pageNo": page,
    }
    return _http.get_text(BASE_URL, params=params)


def parse_page(raw_xml: str) -> list[dict[str, Any]]:
    """[순수] 응답 XML → 행 dict 리스트. 거래금액 단위는 만원이라 원으로 환산한다."""
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError as exc:
        raise AgentError(
            module=MODULE,
            reason=f"응답 XML 파싱 실패: {exc}. 인증키 오류 시 HTML 오류 페이지가 오기도 합니다.",
            recoverable=True,
        ) from exc

    code = _text(root, ("./header/resultCode",)) or _text(
        root, ("./cmmMsgHeader/returnReasonCode",)
    )
    message = _text(root, ("./header/resultMsg",)) or _text(
        root, ("./cmmMsgHeader/returnAuthMsg",)
    )
    if code and code not in {OK_RESULT_CODE, "00", "0"}:
        hint = ""
        if "SERVICE_KEY_IS_NOT_REGISTERED" in message.upper():
            hint = (
                " — Encoding 키를 쓰면 requests 가 %2F 를 이중 인코딩해 이 오류가 납니다. "
                "포털의 Decoding 키를 .env 에 넣으세요."
            )
        raise AgentError(
            module=MODULE,
            reason=f"API 오류 resultCode={code} resultMsg={message}{hint}",
            recoverable=True,
        )

    rows: list[dict[str, Any]] = []
    for item in root.iter("item"):
        # 거래금액은 만원 단위 문자열("38,500")로 온다.
        amount_manwon = _to_int(_text(item, _TAG_ALIASES["deal_amount"]))
        area = _to_float(_text(item, _TAG_ALIASES["area_m2"]))
        year = _to_int(_text(item, _TAG_ALIASES["deal_year"]))
        month = _to_int(_text(item, _TAG_ALIASES["deal_month"]))
        day = _to_int(_text(item, _TAG_ALIASES["deal_day"]))
        if amount_manwon <= 0 or not area or year <= 0:
            continue
        rows.append(
            {
                "deal_amount": amount_manwon * 10_000,
                "area_m2": area,
                "deal_date": f"{year:04d}-{month:02d}-{max(day, 1):02d}",
                "build_year": _to_float(_text(item, _TAG_ALIASES["build_year"])),
                "floor": _to_float(_text(item, _TAG_ALIASES["floor"])),
                "dong": _text(item, _TAG_ALIASES["umd_nm"]),
                "complex_name": _text(item, _TAG_ALIASES["apt_nm"]),
            }
        )
    return rows


def collect(
    region_code: str,
    as_of: date,
    months: int = 24,
    out: str = "data/processed/trades.parquet",
    service_key: Optional[str] = None,
) -> Path:
    """[네트워크] 월별 반복 수집 → parquet 저장. as_of 는 호출자가 준다."""
    from src.agents.price_band import data as band_data

    key = service_key or require(SERVICE_KEY_ENV)
    rows: list[dict[str, Any]] = []
    for deal_ym in month_sequence(as_of, months):
        month_rows = parse_page(fetch_page(key, region_code, deal_ym))
        rows.extend(month_rows)
        log.info("실거래 %s %s %d건 (누적 %d)", region_code, deal_ym, len(month_rows), len(rows))

    import pandas as pd

    frame = pd.DataFrame(rows)
    if len(frame) > 0:
        frame["region_code"] = region_code

    target = band_data.write_frame(frame, out, band_data.TRADE_DTYPES)
    log.info("저장 완료 %s (%d건)", target, len(frame))
    return target


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="국토부 아파트 매매 실거래가 수집")
    parser.add_argument("--region", required=True, help="법정동코드 앞 5자리(예: 30170)")
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument(
        "--as-of",
        required=True,
        help="수집 기준일 YYYY-MM-DD (시계를 읽지 않으므로 반드시 지정)",
    )
    parser.add_argument("--out", default="data/processed/trades.parquet")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    collect(
        region_code=args.region,
        as_of=date.fromisoformat(args.as_of),
        months=args.months,
        out=args.out,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
