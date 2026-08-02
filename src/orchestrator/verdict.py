"""신호등 판정 (process.md §6 T7, P1).

이 모듈의 `decide()` 는 고정 로직이다. 임의 변경 금지.

원안(process.md)의 `rec = min(max_bid, band.p90)` 캡은 여기서 제거되었다.
시장 밴드로 사용자의 '감당 가능 상한'을 깎으면 밴드가 참고 정보가 아니라
추천 근거가 되어 P1 을 위반하기 때문이다. 밴드는 판정 신호에만 쓰고
추천 금액에는 개입하지 않는다.
"""
from __future__ import annotations

from typing import Optional

from src.schemas.core import (
    FinancingCeiling,
    PriceBandEstimate,
    RightsAnalysisReport,
    RiskGrade,
    Signal,
)
from src.orchestrator.report import won

# 입찰 비권장 사유를 warnings[0] 로 실어 보낼 때 쓰는 고정 접두사.
# FinalVerdict 스키마는 동결되어 있어 전용 필드를 추가할 수 없으므로,
# UI 는 이 접두사로 사유 문자열을 찾는다.
RED_REASON_PREFIX = "입찰 비권장 사유: "


def decide(
    rights: RightsAnalysisReport,
    band: Optional[PriceBandEstimate],
    financing: FinancingCeiling,
    min_bid_price: int,
) -> tuple[Signal, Optional[int]]:
    """(신호, 추천 상한). RED 이면 추천 상한은 None."""
    # 1) 권리 리스크가 자금과 무관하게 우선한다
    if rights.risk_grade == RiskGrade.D:
        return Signal.RED, None
    # 2) 법정 최저매각가격 미만은 응찰 자체가 불가하다
    if financing.max_bid_price < min_bid_price:
        return Signal.RED, None
    # 3) 자금 대비 시장 밴드 (밴드는 참고 정보이며 추천액을 깎는 데 쓰지 않는다)
    if band is not None:
        if financing.max_bid_price < band.p10:
            return Signal.RED, None
        sig = Signal.YELLOW if financing.max_bid_price < band.p50 else Signal.GREEN
    else:
        sig = Signal.YELLOW          # 밴드 없으면 보수적으로
    # 4) 권리 등급 하향 보정
    if rights.risk_grade == RiskGrade.C and sig == Signal.GREEN:
        sig = Signal.YELLOW
    # 5) 추천액은 감당 가능 상한 그 자체다.
    return sig, financing.max_bid_price


def red_reason(
    rights: RightsAnalysisReport,
    band: Optional[PriceBandEstimate],
    financing: FinancingCeiling,
    min_bid_price: int,
) -> Optional[str]:
    """RED 판정의 사유 문장. RED 가 아니면 None.

    `decide()` 의 RED 분기와 같은 순서로 검사한다(분기 순서가 곧 사유 우선순위).
    문장 안의 수치는 전부 스키마 값에서 온다(P3).
    """
    if rights.risk_grade == RiskGrade.D:
        return (
            "권리 인수 리스크가 D등급입니다. 유치권 등 특수권리 또는 중대한 "
            "불확실성이 확인되어, 인수 금액을 확정할 수 없습니다. 입찰을 권하지 않습니다."
        )
    if financing.max_bid_price < min_bid_price:
        return (
            f"감당 가능 상한({won(financing.max_bid_price)})이 법정 최저매각가격"
            f"({won(min_bid_price)})에 미치지 못합니다. 이 회차에는 응찰 자체가 불가능합니다."
        )
    if band is not None and financing.max_bid_price < band.p10:
        return (
            f"감당 가능 상한({won(financing.max_bid_price)})이 참고 구간의 하단"
            f"({won(band.p10)})에도 미치지 못합니다. 무리한 자금 계획이 필요한 물건입니다."
        )
    return None
