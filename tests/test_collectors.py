"""수집기 파서 회귀 테스트.

수집기는 fetch(네트워크)와 parse(순수함수)를 분리해 두었다. 인증키가 없어도
parse 는 녹화된 응답으로 완전히 검증할 수 있다 — 그래서 여기서 검증한다.
네트워크를 타는 fetch/collect 는 @pytest.mark.network 로 분리하며
pyproject 의 addopts 가 기본 제외한다.
"""
from __future__ import annotations

import pytest

from src.collectors import molit_trades, onbid
from src.utils.io import read_json, read_text

ONBID_XML = "tests/fixtures/api/onbid_sample.xml"
ONBID_EXPECTED = "tests/fixtures/api/onbid_sample.expected.json"


def test_onbid_parse_matches_recorded_expectation():
    """녹화된 응답 → 기대 출력. 응답 스키마가 바뀌면 여기서 먼저 깨진다."""
    assert onbid.parse_page(read_text(ONBID_XML)) == read_json(ONBID_EXPECTED)


def test_onbid_parse_output_feeds_price_band_schema():
    """파서 출력이 A2 학습 프레임이 요구하는 컬럼을 실제로 채우는지 본다.

    수집과 소비가 따로 개발되면 컬럼 이름이 어긋난 채로 양쪽 다 통과하는
    사고가 난다. 두 계약을 여기서 맞대어 본다.
    """
    from src.agents.price_band.data import AUCTION_CASE_COLUMNS

    rows = onbid.parse_page(read_text(ONBID_XML))
    assert rows, "픽스처가 비어 있다"

    required = {"case_id", "property_type", "appraisal_price",
                "min_bid_price", "failed_count", "sold_price", "sold_date"}
    for row in rows:
        missing = required - set(row)
        assert not missing, f"학습에 필요한 컬럼 누락: {missing}"
        assert row["appraisal_price"] > 0
        assert row["sold_price"] > 0
        assert 0 <= row["failed_count"] < 20
        # 낙찰가율이 상식 범위를 벗어나면 파싱이 틀린 것이다
        ratio = row["sold_price"] / row["appraisal_price"]
        assert 0.1 < ratio < 3.0, f"{row['case_id']}: 낙찰가율 {ratio:.2f}"
    assert required <= set(AUCTION_CASE_COLUMNS), "파서 출력과 A2 데이터 계약이 어긋난다"


def test_onbid_parse_is_pure_and_deterministic():
    """같은 입력 두 번 → 같은 출력. 파서에 상태가 없어야 한다."""
    raw = read_text(ONBID_XML)
    assert onbid.parse_page(raw) == onbid.parse_page(raw)


def test_onbid_parse_survives_empty_and_error_responses():
    """인증키 오류·빈 결과에서 예외 대신 빈 리스트를 돌려줘야 한다.

    가장 흔한 설정 실패(Encoding 키를 쓰면 나는 오류)가 크래시가 되면
    사용자가 원인을 못 찾는다.
    """
    empty = "<response><body><items></items></body></response>"
    err = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader>"
        "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    assert onbid.parse_page(empty) == []
    assert onbid.parse_page(err) == []


def test_onbid_property_type_mapping_is_closed():
    """매핑 결과는 반드시 스키마의 PropertyType 이거나 None 이어야 한다."""
    from src.schemas.core import PropertyType

    valid = {p.value for p in PropertyType}
    samples = ["아파트", "다세대", "연립주택", "오피스텔", "단독주택",
               "근린생활시설", "토지", "알수없는분류", ""]
    for s in samples:
        mapped = onbid.map_property_type(s)
        assert mapped is None or mapped in valid, f"{s} -> {mapped}"


def test_molit_month_sequence_is_deterministic_and_bounded():
    """실거래가 API 는 월 단위 조회다. 개월 수만큼 정확히, 최신에서 과거로.

    연도 경계(1월 → 전년 12월)를 넘는지가 핵심이다. 여기서 틀리면 조회월이
    비거나 중복돼 수집량이 조용히 줄어든다.
    """
    from datetime import date

    months = molit_trades.month_sequence(date(2026, 3, 15), 5)
    assert months == ["202603", "202602", "202601", "202512", "202511"]
    assert len(set(months)) == 5, "중복 조회월"

    # 시계를 읽지 않는다: 같은 기준일이면 언제 호출해도 같은 결과
    assert months == molit_trades.month_sequence(date(2026, 3, 15), 5)

    single = molit_trades.month_sequence(date(2026, 1, 31), 1)
    assert single == ["202601"]
    assert all(len(m) == 6 and m.isdigit() for m in months)


def test_molit_parse_handles_empty_response():
    assert molit_trades.parse_page(
        "<response><body><items></items></body></response>"
    ) == []


@pytest.mark.network
def test_onbid_fetch_requires_key():
    """실제 API 호출. 인증키가 있을 때만 수동 실행한다.

        scripts\\test.cmd -m network
    """
    from src.utils.env import require

    raw = onbid.fetch_page(require("DATA_GO_KR_SERVICE_KEY"), "30170", 1)
    assert onbid.parse_page(raw)
