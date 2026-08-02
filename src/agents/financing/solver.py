"""A3 입찰 상한 이분탐색 — 순수 결정론.

f(bid) = 보유현금 + loan(bid) - cost_fn(bid).total_upfront_excl_bid - bid

f 는 단조감소한다: f' = loan' - C' - 1 이고 loan' <= 적용LTV < 1,
C' = 비용비율(현재 설정 기준 약 0.017~0.025) >= 0 이므로 f' < 0 이다.
따라서 f(bid) >= 0 을 만족하는 최대 bid 가 유일하게 존재한다.
"""
from __future__ import annotations

import logging
from typing import Callable

from src.agents.financing.ltv import LoanTerms, loan_at
from src.schemas.core import PropertyCase, UserProfile
from src.utils.config import Config

logger = logging.getLogger(__name__)

MSG_BELOW_MIN_BID = "최저매각가격조차 감당할 수 없습니다"


def _upper_bound(case: PropertyCase, user: UserProfile, cfg: Config) -> int:
    """f(upper) < 0 이 보장되는 탐색 상한.

    loan(bid) <= 적용LTV × 감정가 <= 감정가 이고 비용은 음수가 아니므로
    f(bid) <= 현금 + 감정가 - bid 이다. 따라서 bid 가 (현금 + 감정가)의
    2배면 f < 0 이 반드시 성립한다.
    """
    return (user.cash_available + case.appraisal_price + cfg.bid_round_unit) * 2


def solve_max_bid(
    case: PropertyCase,
    user: UserProfile,
    terms: LoanTerms,
    cost_fn: Callable[[int], object],
    cfg: Config,
) -> tuple[int, bool, list[str]]:
    """(입찰 상한, 현금부족 여부, 계산 추적 로그)."""
    assert terms.applied_ltv < 1, (
        f"적용 LTV 가 {terms.applied_ltv} 로 1 이상이다. "
        "이 경우 f(bid) 가 단조감소하지 않아 이분탐색이 잘못된 값을 낸다. "
        "config/regulation.yaml 의 ltv 테이블을 확인하라."
    )

    trace: list[str] = []
    unit = cfg.bid_round_unit

    def f(bid: int) -> int:
        loan, _ = loan_at(bid, terms)
        upfront = cost_fn(bid).total_upfront_excl_bid
        return user.cash_available + loan - upfront - bid

    lo = int(case.min_bid_price)
    f_lo = f(lo)
    trace.append(
        f"최저매각가격 {lo:,}원에서 여유자금 f = {f_lo:,}원 "
        f"(현금 {user.cash_available:,} + 대출 - 초기비용 - 입찰가)"
    )
    if f_lo < 0:
        trace.append(MSG_BELOW_MIN_BID)
        return 0, True, trace

    hi = _upper_bound(case, user, cfg)
    trace.append(f"이분탐색 구간 [{lo:,}, {hi:,}]원, 수렴 단위 {unit:,}원")

    # 1원 단위까지 수렴시킨 뒤 라운딩 단위로 내린다.
    # 탐색을 라운딩 단위(10만원)에서 멈추면 참 해와 lo 사이에 라운딩 경계가
    # 끼어 결과가 한 단위(10만원) 낮게 나올 수 있으므로 정확히 수렴시킨다.
    steps = 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if f(mid) >= 0:
            lo = mid
        else:
            hi = mid
        steps += 1

    floored = (lo // unit) * unit
    trace.append(
        f"이분탐색 {steps}회 수렴: 상한 {lo:,}원 → {unit:,}원 단위 내림 "
        f"{floored:,}원"
    )

    if 0 < floored < case.min_bid_price:
        trace.append(
            f"내림 결과 {floored:,}원이 최저매각가격 {case.min_bid_price:,}원 "
            "미만 → 기일입찰은 최저매각가격 이상만 응찰 가능하므로 0원 처리"
        )
        return 0, True, trace

    return floored, False, trace
