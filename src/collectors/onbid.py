"""T1a 수집기 — 온비드(한국자산관리공사) 공매 낙찰 결과.

    python -m src.collectors.onbid --region 30170 --months 24 --out data/processed/auction_cases.parquet

[주의] 인증키 (설치 단계에서 가장 많이 나는 사고)
    공공데이터포털은 인증키를 **Encoding** 과 **Decoding** 두 형태로 준다.
    반드시 **Decoding 키**를 .env 에 넣어야 한다. Encoding 키를 넣으면
    requests 가 params 를 다시 URL 인코딩하면서 `%2F` 가 `%252F` 로 이중
    인코딩되고, 서버는 SERVICE_KEY_IS_NOT_REGISTERED_ERROR 를 돌려준다.
    키가 정상 등록돼 있어도 이 메시지가 뜬다.

[주의] 데이터 성격
    공매 낙찰가율 분포는 법원경매와 완전히 같지 않다. PoC 학습 데이터로만
    쓰고 리포트 caveats 에 그 사실을 명시한다(process.md §T1a).

3층 구조:
    fetch_page  — 네트워크 (테스트에서 제외)
    parse_page  — 순수 함수 (fixture XML 로 회귀 테스트)
    collect     — fetch + parse + parquet 저장
"""
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

from src.collectors import _http
from src.utils.env import require
from src.utils.errors import AgentError

log = logging.getLogger(__name__)

MODULE = "collectors.onbid"

# 공공데이터포털 계정 하나로 온비드·국토부 실거래가를 같이 쓴다.
SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"

BASE_URL = (
    "http://openapi.onbid.co.kr/openapi/services/KamcoPblsalThingInqireSvc"
    "/getKamcoPbctCltrList"
)
DEFAULT_ROWS_PER_PAGE = 100
OK_RESULT_CODE = "00"

# CTGR_FULL_NM(용도 전체명) → PropertyType. 앞에서 매칭된 것이 이긴다.
_TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("아파트", "APARTMENT"),
    ("오피스텔", "OFFICETEL"),
    ("다세대", "MULTI_HOUSE"),
    ("연립", "MULTI_HOUSE"),
    ("빌라", "MULTI_HOUSE"),
    ("다가구", "DETACHED"),
    ("단독", "DETACHED"),
    ("주택", "DETACHED"),
    ("근린", "COMMERCIAL"),
    ("상가", "COMMERCIAL"),
    ("점포", "COMMERCIAL"),
    ("사무", "COMMERCIAL"),
    ("토지", "LAND"),
    ("대지", "LAND"),
    ("임야", "LAND"),
    ("전답", "LAND"),
)

# 낙찰로 볼 물건 상태 문자열
_SOLD_STATUS = ("낙찰", "매각")


def _text(node: Any, tag: str) -> str:
    found = node.find(tag)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


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


def _to_date(raw: str) -> Optional[str]:
    """'2026-03-14 10:00:00' / '20260314' / '2026.03.14' → 'YYYY-MM-DD'."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 8:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def map_property_type(category_name: str) -> Optional[str]:
    for keyword, ptype in _TYPE_RULES:
        if keyword in (category_name or ""):
            return ptype
    return None


def fetch_page(
    service_key: str,
    region_code: str,
    page: int = 1,
    rows: int = DEFAULT_ROWS_PER_PAGE,
    **extra: Any,
) -> str:
    """[네트워크] 한 페이지 원문 XML. service_key 는 반드시 Decoding 키."""
    params: dict[str, Any] = {
        "serviceKey": service_key,
        "numOfRows": rows,
        "pageNo": page,
        "SIDO_CD": region_code,
    }
    params.update(extra)
    return _http.get_text(BASE_URL, params=params)


def parse_page(raw_xml: str) -> list[dict[str, Any]]:
    """[순수] 응답 XML → 행 dict 리스트. 네트워크를 타지 않는다.

    낙찰가(SCSBD_AMT)가 없는 진행 중 물건은 학습에 쓸 수 없으므로 버린다.
    region_code 는 이 API 응답에 법정동코드가 없어 채우지 않는다(collect 가 채운다).
    """
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError as exc:
        raise AgentError(
            module=MODULE,
            reason=f"응답 XML 파싱 실패: {exc}. 인증키 오류 시 HTML 오류 페이지가 오기도 합니다.",
            recoverable=True,
        ) from exc

    code = _text(root, "./header/resultCode") or _text(root, "./cmmMsgHeader/returnReasonCode")
    message = _text(root, "./header/resultMsg") or _text(
        root, "./cmmMsgHeader/returnAuthMsg"
    )
    if code and code not in {OK_RESULT_CODE, "0"}:
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
        status = _text(item, "PBCT_CLTR_STAT_NM")
        sold_price = _to_int(_text(item, "SCSBD_AMT"))
        appraisal = _to_int(_text(item, "APSL_ASES_AVG_AMT"))
        if sold_price <= 0 or appraisal <= 0:
            continue
        if status and not any(k in status for k in _SOLD_STATUS):
            continue

        plan_no = _text(item, "PLNM_NO")
        item_no = _text(item, "CLTR_NO") or _text(item, "CLTR_MNMT_NO")
        rows.append(
            {
                "case_id": f"ONBID-{plan_no}-{item_no}",
                "source": "ONBID",
                "address": _text(item, "LDNM_ADRS") or _text(item, "NMRD_ADRS"),
                "property_type": map_property_type(_text(item, "CTGR_FULL_NM")),
                "appraisal_price": appraisal,
                "min_bid_price": _to_int(_text(item, "MIN_BID_PRC")),
                "failed_count": _to_int(_text(item, "USCBD_CNT")),
                "sold_price": sold_price,
                "sold_date": _to_date(
                    _text(item, "PBCT_CLS_DTM") or _text(item, "SCSBD_DTM")
                ),
                "building_area_m2": _to_float(_text(item, "BULD_AREA")),
            }
        )
    return rows


def collect(
    region_code: str,
    months: int = 24,
    out: str = "data/processed/auction_cases.parquet",
    max_pages: int = 50,
    service_key: Optional[str] = None,
) -> Path:
    """[네트워크] 전 페이지 수집 → parquet 저장. 저장 경로를 돌려준다."""
    from src.agents.price_band import data as band_data

    key = service_key or require(SERVICE_KEY_ENV)
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        page_rows = parse_page(fetch_page(key, region_code, page))
        if not page_rows:
            break
        rows.extend(page_rows)
        log.info("온비드 %s page=%d 누적 %d건", region_code, page, len(rows))

    import pandas as pd

    frame = pd.DataFrame(rows)
    if len(frame) > 0:
        # 이 API 응답에는 법정동코드가 없다. 조회 조건으로 쓴 지역코드를 그대로 채운다.
        frame["region_code"] = region_code
        frame = frame[frame["property_type"].notna()]
        if months and "sold_date" in frame.columns:
            cutoff = pd.Timestamp(
                pd.to_datetime(frame["sold_date"], errors="coerce").max()
            ) - pd.DateOffset(months=months)
            frame = frame[pd.to_datetime(frame["sold_date"], errors="coerce") >= cutoff]

    target = band_data.write_frame(frame, out, band_data.AUCTION_CASE_DTYPES)
    log.info("저장 완료 %s (%d건)", target, len(frame))
    return target


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="온비드 공매 낙찰 결과 수집")
    parser.add_argument("--region", required=True, help="지역코드(예: 30170)")
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--out", default="data/processed/auction_cases.parquet")
    parser.add_argument("--max-pages", type=int, default=50)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    collect(
        region_code=args.region,
        months=args.months,
        out=args.out,
        max_pages=args.max_pages,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
