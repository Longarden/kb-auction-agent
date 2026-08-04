"""A4 총소유비용(TCO) — 순수 결정론적 비용 함수.

입찰가 bid 를 넣으면 낙찰가 외 초기비용을 항목별로 산출한다.
A3(자금조달)는 이 함수를 partial 로 부분 적용받아 이분탐색에 쓴다.
"""
from __future__ import annotations

import logging

from src.agents.tco.tax import (
    acquisition_tax_rate,
    is_housing,
    local_edu_tax,
    special_rural_tax,
)
from src.schemas.core import (
    PropertyCase,
    PropertyType,
    RightsAnalysisReport,
    RightType,
    TCOReport,
    UserProfile,
)
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 미납관리비를 부담하는 주거용 집합건물 유형.
# 단독/다가구·상가·토지는 공용관리비 구조가 달라 0 으로 둔다.
COLLECTIVE_RESIDENTIAL_TYPES = (
    PropertyType.APARTMENT,
    PropertyType.OFFICETEL,
    PropertyType.MULTI_HOUSE,
)

# 점유 유형 상수(heuristics.eviction_cost 의 키)
OCCUPANT_LIEN_CLAIMED = "LIEN_CLAIMED"
OCCUPANT_UNKNOWN = "UNKNOWN_OCCUPANT"
OCCUPANT_TENANT_PARTIAL = "TENANT_PARTIAL"
OCCUPANT_OWNER = "OWNER_OCCUPIED"
OCCUPANT_TENANT_FULL_DIVIDEND = "TENANT_FULL_DIVIDEND"

_EVICTION_HIGH = "HIGH"
_EVICTION_MID = "MID"


def derive_occupant_type(rights: RightsAnalysisReport) -> tuple[str, str]:
    """(점유 유형, 근거). RightsAnalysisReport 에는 점유 유형 필드가 없으므로
    아래 결정적 우선순위가 A1↔A4 사이의 계약이다(heuristics.yaml 주석과 동일).
    """
    if any(r.right_type == RightType.LIEN for r in rights.assumed_rights):
        return OCCUPANT_LIEN_CLAIMED, "인수권리에 유치권 존재"
    if rights.eviction_difficulty == _EVICTION_HIGH:
        return OCCUPANT_UNKNOWN, "명도난이도 HIGH"
    if rights.eviction_difficulty == _EVICTION_MID:
        return OCCUPANT_TENANT_PARTIAL, "명도난이도 MID"
    if not rights.tenants:
        return OCCUPANT_OWNER, "임차인 없음(소유자 점유 추정)"
    return OCCUPANT_TENANT_FULL_DIVIDEND, "임차인 존재·명도난이도 LOW"


def _unpaid_maintenance(case: PropertyCase, cfg: Config) -> tuple[int, str]:
    """(미납관리비 추정, 근거)."""
    if case.property_type not in COLLECTIVE_RESIDENTIAL_TYPES:
        return 0, f"{case.property_type.value} → 공용관리비 인수 추정 제외"

    area = case.building_area_m2
    if not area:
        return 0, "전용면적 미상 → 관리비 추정 불가(0원 처리)"

    block = cfg.heuristics["unpaid_maintenance"]
    unit_fee = float(block["monthly_fee_per_m2"])
    common_ratio = float(block["common_area_ratio"])
    months = cfg.vacancy_months(case.failed_count)
    amount = int(unit_fee * area * months * common_ratio)
    return (
        amount,
        f"{unit_fee:,.0f}원/m² × {area:g}m² × {months}개월 × "
        f"공용비율 {common_ratio}",
    )


def cost_function(
    bid: int,
    case: PropertyCase,
    user: UserProfile,
    rights: RightsAnalysisReport,
    cfg: Config,
) -> TCOReport:
    """입찰가 bid 기준 낙찰가 외 초기비용 리포트."""
    bid = int(bid)
    housing = is_housing(case)

    # 1) 취득 관련 세금
    main_rate, tax_note = acquisition_tax_rate(bid, case, user, cfg)
    acq_tax = int(bid * main_rate)
    edu_tax = local_edu_tax(bid, main_rate, housing, cfg)
    rural_tax, rural_note = special_rural_tax(bid, case, housing, cfg)

    if housing:
        edu_note = (
            f"본세율 {main_rate:.4f} × "
            f"{float(cfg.regulation['acquisition_tax']['local_edu_tax_of_main_rate'])}"
        )
    else:
        edu_note = (
            "비주거 정률 "
            f"{float(cfg.regulation['acquisition_tax']['commercial_local_edu_rate'])}"
        )

    # 2) 법무비 + 채권할인
    legal_ratio = float(cfg.heuristics["legal_bond_fee_ratio"])
    legal_fee = int(bid * legal_ratio)

    # 3) 명도비
    occupant_type, occupant_reason = derive_occupant_type(rights)
    eviction, eviction_warning = cfg.eviction_cost(occupant_type)
    eviction = int(eviction)
    eviction_note = f"{occupant_type}({occupant_reason})"
    if eviction_warning:
        eviction_note = f"{eviction_note} — {eviction_warning}"
        logger.warning("case=%s %s", case.case_id, eviction_warning)

    # 4) 미납관리비
    maintenance, maintenance_note = _unpaid_maintenance(case, cfg)

    # 5) 수선충당
    repair_ratio = float(cfg.heuristics["repair_reserve_ratio"])
    repair = int(bid * repair_ratio)

    # 6) 인수권리 비용 — A1 값을 그대로 전달(재계산 금지: 이중계상 방지)
    assumed_cost = int(rights.total_assumed_cost)

    breakdown = [
        {"label": "취득세", "amount": acq_tax, "note": tax_note},
        {"label": "지방교육세", "amount": edu_tax, "note": edu_note},
        {"label": "농어촌특별세", "amount": rural_tax, "note": rural_note},
        {
            "label": "법무비·채권할인",
            "amount": legal_fee,
            "note": f"낙찰가 × {legal_ratio}",
        },
        {"label": "명도비", "amount": eviction, "note": eviction_note},
        {"label": "미납관리비", "amount": maintenance, "note": maintenance_note},
        {
            "label": "수선충당금",
            "amount": repair,
            "note": f"낙찰가 × {repair_ratio}",
        },
        {
            "label": "인수권리 비용",
            "amount": assumed_cost,
            "note": "A1 권리분석 total_assumed_cost 전달값(재계산 없음)",
        },
    ]

    total = (
        acq_tax
        + edu_tax
        + rural_tax
        + legal_fee
        + eviction
        + maintenance
        + repair
        + assumed_cost
    )
    breakdown_sum = sum(int(item["amount"]) for item in breakdown)
    assert breakdown_sum == total, (
        f"breakdown 합계({breakdown_sum:,})와 total_upfront_excl_bid"
        f"({total:,})가 불일치한다"
    )

    return TCOReport(
        case_id=case.case_id,
        bid_price=bid,
        acquisition_tax=acq_tax,
        local_edu_tax=edu_tax,
        special_rural_tax=rural_tax,
        legal_and_bond_fee=legal_fee,
        eviction_cost=eviction,
        unpaid_maintenance=maintenance,
        repair_reserve=repair,
        assumed_rights_cost=assumed_cost,
        total_upfront_excl_bid=total,
        effective_acquisition_price=bid + total,
        breakdown=breakdown,
    )
