"""권리분석 판정 규칙. 최종 판단은 언제나 여기서 나온다(LLM 아님).

핵심 불변식 세 가지.
  P4 보수성 : 모르면 인수로 가정한다. 0원(가장 낙관적인 값)은 기본값이 될 수 없다.
  결정성    : 같은 입력이면 PYTHONHASHSEED 와 무관하게 같은 출력이 나와야 한다.
              그래서 후보는 set 이 아니라 list 로 다루고, 정렬 키를 완전히 명시한다.
  구멍 없음 : risk_grade 는 first-match-wins 사다리에 반드시 else 를 둔다.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from src.agents.rights_analysis.schemas import (
    RawExtraction,
    RawRightRow,
    RawSpecialRight,
    RawTenantRow,
)
from src.schemas.core import (
    AssumedRight,
    BaseRight,
    Confidence,
    PropertyCase,
    RightType,
    RiskGrade,
    TenantInfo,
)

# ── 상수 ────────────────────────────────────────────────────
#: 말소기준권리 후보와 동일 일자 우선순위(process.md §2.2(b): 같은 날이면 저당권이 우선).
RIGHT_PRIORITY: dict[RightType, int] = {
    RightType.MORTGAGE: 0,
    RightType.COLLATERAL_PROV_REG: 1,
    RightType.SEIZURE: 2,
    RightType.AUCTION_START: 3,
    RightType.JEONSE_RIGHT: 4,
}

#: 존재만으로 D 등급을 확정하는 특수권리.
SPECIAL_RIGHT_TYPES: frozenset[RightType] = frozenset(
    {
        RightType.LIEN,
        RightType.STATUTORY_SUPERFICIES,
        RightType.GRAVE_BASE,
        RightType.TRANSFER_PROV_REG,
        RightType.INJUNCTION,
        RightType.LAND_SEPARATE_REGISTRY,
    }
)

#: 감정가 대비 인수금액 임계치. 여기 한 곳에서만 정의한다(등급 판정에서 두 번 쓰인다).
COST_RATIO_THRESHOLD = 0.10

#: UNCERTAIN 항목이 이만큼 쌓이면 등급을 C 로 내린다.
UNCERTAIN_COUNT_THRESHOLD = 2

_EVICTION_ORDER = {"LOW": 0, "MID": 1, "HIGH": 2}

_UNKNOWN_TOKENS = ("성명불상", "불상", "미상")

_RIGHT_LABEL = {
    RightType.LIEN: "유치권",
    RightType.STATUTORY_SUPERFICIES: "법정지상권",
    RightType.GRAVE_BASE: "분묘기지권",
    RightType.TRANSFER_PROV_REG: "소유권이전청구권 가등기",
    RightType.INJUNCTION: "가처분",
    RightType.LAND_SEPARATE_REGISTRY: "토지별도등기·대지권 미등기",
    RightType.MORTGAGE: "(근)저당권",
    RightType.SEIZURE: "압류·가압류",
    RightType.COLLATERAL_PROV_REG: "담보가등기",
    RightType.AUCTION_START: "경매개시결정등기",
    RightType.JEONSE_RIGHT: "전세권",
    RightType.TENANT_DEPOSIT: "임차보증금 인수",
}


def _as_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _as_right_type(value: Optional[str]) -> Optional[RightType]:
    if not value:
        return None
    try:
        return RightType(value)
    except ValueError:
        return None


def _harder(a: str, b: str) -> str:
    return a if _EVICTION_ORDER[a] >= _EVICTION_ORDER[b] else b


# ── 말소기준권리 ────────────────────────────────────────────
def determine_base_right(
    extraction: RawExtraction,
) -> tuple[Optional[BaseRight], list[str], list[str]]:
    """(말소기준권리, 경고, 근거). 등기부 계산과 법원 기재를 교차 검증한다."""
    warnings: list[str] = []
    evidence: list[str] = []

    candidates: list[tuple[date, int, str, RightType]] = []
    for row in extraction.rights:
        right_type = _as_right_type(row.right_type)
        registered = _as_date(row.registered_date)
        if right_type is None or registered is None:
            continue
        if right_type not in RIGHT_PRIORITY:
            continue
        # list 로 모아 (일자, 권리 우선순위, 권리자명) 전순서로 정렬한다.
        # set 을 쓰면 동률에서 해시 시드에 따라 결과가 흔들린다.
        candidates.append((registered, RIGHT_PRIORITY[right_type], row.holder or "", right_type))

    computed: Optional[BaseRight] = None
    if candidates:
        chosen = min(candidates, key=lambda c: (c[0], c[1], c[2]))
        computed = BaseRight(
            right_type=chosen[3], registered_date=chosen[0], holder=chosen[2] or "미상"
        )
        evidence.append(
            f"등기부 최선순위 계산: {chosen[3].value} {chosen[0].isoformat()} ({chosen[2] or '미상'})"
        )

    hint = extraction.base_right_hint
    hint_date = _as_date(hint.registered_date) if hint else None
    hint_type = _as_right_type(hint.right_type) if hint else None

    if hint_date and hint_type:
        # 법원이 직접 적어 준 최선순위 설정을 우선한다.
        holder = computed.holder if (computed and computed.registered_date == hint_date) else "미상"
        base = BaseRight(right_type=hint_type, registered_date=hint_date, holder=holder)
        evidence.append(hint.evidence or "매각물건명세서 최선순위 설정란 채택")
        if computed and (
            computed.registered_date != hint_date or computed.right_type != hint_type
        ):
            warnings.append(
                "매각물건명세서의 최선순위 설정("
                f"{hint_date.isoformat()} {_RIGHT_LABEL.get(hint_type, hint_type.value)})과 "
                "등기부 계산 결과("
                f"{computed.registered_date.isoformat()} "
                f"{_RIGHT_LABEL.get(computed.right_type, computed.right_type.value)})가 다릅니다. "
                "등기부 원본 확인이 필요합니다."
            )
        return base, warnings, evidence

    if computed is not None:
        return computed, warnings, evidence

    warnings.append(
        "말소기준권리 식별 불가 — 등기사항전부증명서와 매각물건명세서 원본 확인이 필요합니다."
    )
    return None, warnings, evidence


# ── 임차인 ──────────────────────────────────────────────────
def build_tenant(
    row: RawTenantRow,
    base_right: Optional[BaseRight],
    case: PropertyCase,
    fallback_ratio: float,
) -> tuple[TenantInfo, list[str]]:
    warnings: list[str] = []
    move_in = _as_date(row.move_in_date)
    fixed = _as_date(row.fixed_date)
    name = row.name_masked or "성명불상"

    # 대항력: 전입일이 말소기준권리보다 '엄격히' 빨라야 한다. 같은 날이면 저당권 우선.
    if move_in is None or base_right is None:
        opposing: Optional[bool] = None
    else:
        opposing = move_in < base_right.registered_date

    deposit = row.deposit
    confidence = Confidence.CONFIRMED
    if deposit is None:
        deposit = int(round(case.appraisal_price * fallback_ratio))
        confidence = Confidence.UNCERTAIN
        warnings.append(
            f"임차인 {name}의 보증금이 확인되지 않아 감정가의 "
            f"{fallback_ratio:.0%}({deposit:,}원)로 보수 추정했습니다."
        )

    dividend = row.dividend_demanded
    if dividend is None:
        # 배당요구 여부 불명 -> '하지 않음'(전액 인수)으로 가정한다(P4).
        dividend_effective = False
        confidence = Confidence.UNCERTAIN
        warnings.append(
            f"임차인 {name}의 배당요구 여부가 확인되지 않아 '없음'(전액 인수)으로 가정했습니다."
        )
    else:
        dividend_effective = dividend

    if opposing is None:
        assumed = deposit
        confidence = Confidence.UNCERTAIN
        warnings.append(
            f"임차인 {name}의 대항력을 판단할 수 없어 보증금 전액 인수로 가정했습니다."
        )
    elif not opposing:
        assumed = 0
    elif not dividend_effective:
        assumed = deposit
    else:
        assumed = deposit
        confidence = Confidence.UNCERTAIN
        warnings.append(
            "배당 후 미회수분만 실제 인수 — 배당표 시뮬레이션은 v2 예정"
        )

    tenant = TenantInfo(
        name_masked=name,
        move_in_date=move_in,
        fixed_date=fixed,
        deposit=row.deposit if row.deposit is not None else deposit,
        opposing_power=opposing,
        dividend_demanded=dividend,
        expected_assumed_deposit=assumed,
        confidence=confidence,
    )
    return tenant, warnings


# ── 특수권리 ────────────────────────────────────────────────
def build_assumed_right(
    row: RawSpecialRight, case: PropertyCase, unknown_ratio: float
) -> tuple[Optional[AssumedRight], list[str]]:
    warnings: list[str] = []
    right_type = _as_right_type(row.right_type)
    if right_type is None:
        return None, warnings
    label = _RIGHT_LABEL.get(right_type, right_type.value)

    if row.claimed_amount is not None:
        cost = int(row.claimed_amount)
        confidence = Confidence.LIKELY
        description = f"{label} 신고 — 신고 금액 {cost:,}원이 매수인에게 인수될 수 있습니다."
    else:
        # 금액 불명을 0 으로 두면 가장 위험한 물건이 가장 싸 보인다(P4 위반).
        cost = int(case.appraisal_price * unknown_ratio)
        confidence = Confidence.UNCERTAIN
        description = (
            f"{label} 존재 — 인수 금액이 확인되지 않아 감정가의 "
            f"{unknown_ratio:.0%}({cost:,}원)로 보수 추정했습니다."
        )
        warnings.append(
            f"{label}의 인수 금액이 불명이라 감정가의 {unknown_ratio:.0%}로 추정했습니다. "
            "실제 금액은 크게 달라질 수 있습니다."
        )

    return (
        AssumedRight(
            right_type=right_type,
            description=description,
            estimated_cost=cost,
            confidence=confidence,
            evidence=row.evidence or "매각물건명세서",
        ),
        warnings,
    )


# ── 명도 난이도 ─────────────────────────────────────────────
def determine_eviction_difficulty(tenants: list[TenantInfo]) -> str:
    """여러 조건이 겹치면 더 어려운 쪽을 택한다(HIGH > MID > LOW)."""
    if not tenants:
        return "LOW"
    level = "LOW"
    for tenant in tenants:
        if any(token in tenant.name_masked for token in _UNKNOWN_TOKENS) or tenant.move_in_date is None:
            level = _harder(level, "HIGH")
        elif tenant.dividend_demanded is True and tenant.expected_assumed_deposit == 0:
            level = _harder(level, "LOW")
        else:
            level = _harder(level, "MID")
    if len(tenants) >= 2:
        level = _harder(level, "HIGH")
    return level


# ── 위험등급 ────────────────────────────────────────────────
def determine_risk_grade(
    *,
    assumed_rights: list[AssumedRight],
    tenants: list[TenantInfo],
    base_right: Optional[BaseRight],
    total_assumed_cost: int,
    appraisal_price: int,
    eviction_difficulty: str,
) -> RiskGrade:
    """D -> C -> B -> A 순 first-match-wins. 마지막 else 로 구멍을 막는다."""
    has_special = any(r.right_type in SPECIAL_RIGHT_TYPES for r in assumed_rights)
    uncertain_count = len(
        [r for r in assumed_rights if r.confidence == Confidence.UNCERTAIN]
    ) + len(
        [
            t
            for t in tenants
            if t.confidence == Confidence.UNCERTAIN and t.expected_assumed_deposit > 0
        ]
    )
    threshold = appraisal_price * COST_RATIO_THRESHOLD

    if has_special:
        return RiskGrade.D
    if base_right is None and tenants:
        return RiskGrade.D
    if total_assumed_cost >= threshold:
        return RiskGrade.C
    if uncertain_count >= UNCERTAIN_COUNT_THRESHOLD:
        return RiskGrade.C
    if total_assumed_cost == 0 and eviction_difficulty == "LOW" and not has_special:
        return RiskGrade.A
    if total_assumed_cost < threshold and eviction_difficulty != "HIGH":
        return RiskGrade.B
    return RiskGrade.C  # 보수적 폴백
