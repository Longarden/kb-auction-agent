"""A3 자금조달 상한 진입점 — 순수 결정론.

이 패키지는 src.agents.tco 를 import 하지 않는다. 비용함수는 cost_fn
파라미터로 주입받는다(순환 의존 차단).
"""
from __future__ import annotations

import logging
import math
from typing import Callable

from src.agents.financing.dsr import max_loan_by_dsr, monthly_payment, monthly_terms
from src.agents.financing.ltv import LoanTerms, loan_at, ltv_term
from src.agents.financing.solver import solve_max_bid
from src.schemas.core import (
    BindingConstraint,
    FinancingCeiling,
    PropertyCase,
    PropertyType,
    RightsAnalysisReport,
    UserProfile,
)
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 방공제(소액임차 최우선변제) 대상이 아닌 물건 유형
NON_RESIDENTIAL_TYPES = (PropertyType.COMMERCIAL, PropertyType.LAND)

MSG_ROOM_OVER_LTV = "방공제가 LTV 한도를 초과하여 대출 가능액 0"


def run(
    case: PropertyCase,
    user: UserProfile,
    rights: RightsAnalysisReport,
    cost_fn: Callable[[int], object],
    cfg: Config,
) -> FinancingCeiling:
    """감당 가능한 최대 입찰가와 그 제약 요인을 산출한다."""
    calc_trace: list[str] = [
        f"config version={cfg.version} verified={cfg.verified}"
    ]
    calc_trace.extend(cfg.notes)

    # 1) 적용 LTV
    applied_ltv, ltv_trace = cfg.lookup_ltv(
        case.property_type.value, case.regulation_zone.value, user.owned_house_count
    )
    calc_trace.extend(ltv_trace)
    loan_forbidden = applied_ltv == 0
    if loan_forbidden:
        calc_trace.append(
            "적용 LTV 0 → 규제상 대출 불가(LOAN_FORBIDDEN). 현금만으로 계산한다."
        )

    # 2) 방공제
    is_residential = case.property_type not in NON_RESIDENTIAL_TYPES
    room_deduction, room_trace = cfg.lookup_room_deduction(
        case.region_code, is_residential
    )
    calc_trace.extend(room_trace)

    # 3) DSR
    dsr_amount, dsr_trace = max_loan_by_dsr(user, cfg)
    calc_trace.extend(dsr_trace)
    if loan_forbidden:
        dsr_amount = 0

    product_cap = cfg.product_cap
    calc_trace.append(
        "상품 한도: 없음(무한대)"
        if math.isinf(product_cap)
        else f"상품 한도 {int(product_cap):,}원"
    )

    terms = LoanTerms(
        applied_ltv=applied_ltv,
        room_deduction=room_deduction,
        dsr_limit_amount=dsr_amount,
        product_cap=product_cap,
        appraisal_price=case.appraisal_price,
        loan_forbidden=loan_forbidden,
    )

    # 4) 이분탐색
    max_bid, cash_blocked, solver_trace = solve_max_bid(
        case, user, terms, cost_fn, cfg
    )
    calc_trace.extend(solver_trace)

    # 5) 제약 요인
    max_loan, argmin_constraint = loan_at(max_bid, terms)
    raw_ltv_term = ltv_term(max_bid, terms)

    if loan_forbidden:
        binding = BindingConstraint.LOAN_FORBIDDEN
    elif cash_blocked or max_bid == 0:
        binding = BindingConstraint.CASH
    else:
        binding = argmin_constraint
        if max_loan == 0 and raw_ltv_term < 0:
            binding = BindingConstraint.LTV
            calc_trace.append(MSG_ROOM_OVER_LTV)
    calc_trace.append(f"구속 제약 = {binding.value}")

    # 6) max_bid 시점 LTV 한도(음수 금지)
    max_loan_by_ltv = int(max(0, raw_ltv_term))
    calc_trace.append(
        f"입찰상한 {max_bid:,}원 시점 — LTV 한도 {max_loan_by_ltv:,}원 / "
        f"DSR 한도 {dsr_amount:,}원 → 실행 대출 {max_loan:,}원"
    )

    # 7) 월 상환액
    monthly_rate, months = monthly_terms(user, cfg)
    payment = monthly_payment(max_loan, monthly_rate, months)
    calc_trace.append(
        f"대출 {max_loan:,}원의 월 원리금 {payment:,}원 "
        f"(월이율 {monthly_rate:.6f}, {months}개월)"
    )

    logger.debug(
        "financing case=%s max_bid=%s binding=%s",
        case.case_id,
        max_bid,
        binding.value,
    )

    return FinancingCeiling(
        case_id=case.case_id,
        applied_ltv=applied_ltv,
        room_deduction=room_deduction,
        max_loan_by_ltv=max_loan_by_ltv,
        max_loan_by_dsr=dsr_amount,
        max_loan=max_loan,
        binding_constraint=binding,
        max_bid_price=max_bid,
        monthly_payment_at_max=payment,
        assumed_rate=cfg.assumed_rate + cfg.stress_rate_addon,
        calc_trace=calc_trace,
        config_version=cfg.version,
    )
