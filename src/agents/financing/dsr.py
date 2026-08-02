"""A3 DSR 한도 계산 — 순수 결정론.

원리금균등상환 기준 연간 상환여력에서 역산한 대출 상한을 구한다.
금리·한도는 전부 Config 에서 온다.
"""
from __future__ import annotations

import logging

from src.schemas.core import UserProfile
from src.utils.config import Config

logger = logging.getLogger(__name__)

MONTHS_PER_YEAR = 12


def monthly_terms(user: UserProfile, cfg: Config) -> tuple[float, int]:
    """(월 이자율, 총 상환 개월수). 스트레스 가산금리를 포함한다."""
    monthly_rate = (cfg.assumed_rate + cfg.stress_rate_addon) / MONTHS_PER_YEAR
    months = int(user.target_loan_years) * MONTHS_PER_YEAR
    return monthly_rate, months


def monthly_payment(principal: int, monthly_rate: float, months: int) -> int:
    """원리금균등 월 상환액."""
    if principal <= 0 or months <= 0:
        return 0
    if monthly_rate == 0:
        return int(principal / months)
    growth = (1 + monthly_rate) ** months
    return int(principal * monthly_rate * growth / (growth - 1))


def max_loan_by_dsr(user: UserProfile, cfg: Config) -> tuple[int, list[str]]:
    """(DSR 상한 대출액, 계산 추적 로그)."""
    trace: list[str] = []

    allowed_annual = user.annual_income * cfg.dsr_limit
    available_annual = max(0, allowed_annual - user.existing_annual_debt_payment)
    available_monthly = available_annual / MONTHS_PER_YEAR
    trace.append(
        f"DSR 한도 {cfg.dsr_limit}: 연소득 {user.annual_income:,}원 × "
        f"{cfg.dsr_limit} = {int(allowed_annual):,}원 - 기존 연 원리금 "
        f"{user.existing_annual_debt_payment:,}원 = 가용 연 원리금 "
        f"{int(available_annual):,}원 (월 {int(available_monthly):,}원)"
    )

    monthly_rate, months = monthly_terms(user, cfg)
    trace.append(
        f"적용 금리(연) {cfg.assumed_rate} + 스트레스 가산 {cfg.stress_rate_addon} "
        f"→ 월이율 {monthly_rate:.6f}, 상환 {months}개월"
    )

    if monthly_rate == 0:
        limit = available_monthly * months
    else:
        growth = (1 + monthly_rate) ** months
        limit = available_monthly * (growth - 1) / (monthly_rate * growth)

    limit_int = int(max(0, limit))
    trace.append(f"DSR 기준 최대 대출 가능액 {limit_int:,}원")
    return limit_int, trace
