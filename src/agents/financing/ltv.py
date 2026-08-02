"""A3 LTV·상품한도 결합 대출 가능액 — 순수 결정론.

loan(bid) = max(0, min(LTV항, DSR항, 상품한도))
  LTV항 = 적용LTV × min(bid, 감정가) - 방공제
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.schemas.core import BindingConstraint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoanTerms:
    """입찰가와 무관하게 고정된 대출 조건."""

    applied_ltv: float
    room_deduction: int
    dsr_limit_amount: int
    product_cap: float
    appraisal_price: int
    loan_forbidden: bool = False


def ltv_term(bid: int, terms: LoanTerms) -> float:
    """방공제 차감 후 LTV 기준 대출 가능액(음수일 수 있음)."""
    collateral = min(bid, terms.appraisal_price)
    return terms.applied_ltv * collateral - terms.room_deduction


def loan_at(bid: int, terms: LoanTerms) -> tuple[int, BindingConstraint]:
    """(대출 가능액, 최소값을 만든 제약). 대출 금지면 (0, LOAN_FORBIDDEN)."""
    if terms.loan_forbidden:
        return 0, BindingConstraint.LOAN_FORBIDDEN

    candidates: list[tuple[BindingConstraint, float]] = [
        (BindingConstraint.LTV, ltv_term(bid, terms)),
        (BindingConstraint.DSR, float(terms.dsr_limit_amount)),
        (BindingConstraint.PRODUCT_CAP, terms.product_cap),
    ]
    binding, value = min(candidates, key=lambda item: item[1])
    return int(max(0, value)), binding
