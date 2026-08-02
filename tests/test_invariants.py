"""골든값 없는 불변식 테스트.

여기 있는 어떤 테스트도 "정답 숫자"를 요구하지 않는다. 전부 관계·부호·
단조성·항등식만 본다. 그래서 fixture 기대값을 우리가 직접 만들었다는
자기채점 순환의 영향을 받지 않는다.
"""
from __future__ import annotations

import pytest

from src.schemas.core import BindingConstraint, RightType, RiskGrade, Signal
from tests.conftest import (
    MONEY_FIELDS,
    build_cost_fn,
    loan_terms_from,
    pipeline_parts,
)

SPECIAL_RIGHTS = {
    RightType.LIEN,
    RightType.STATUTORY_SUPERFICIES,
    RightType.GRAVE_BASE,
    RightType.TRANSFER_PROV_REG,
    RightType.INJUNCTION,
    RightType.LAND_SEPARATE_REGISTRY,
}


@pytest.fixture(scope="module")
def parts_all(request):
    """(case_name, user_name) -> (rights, band, financing, tco) 전 조합 캐시."""
    from src.utils.config import load_config
    from tests.conftest import CASE_NAMES, USER_NAMES, _load_case, _load_user

    cfg = load_config()
    out = {}
    for cn in CASE_NAMES:
        for un in USER_NAMES:
            out[(cn, un)] = (
                _load_case(cn),
                _load_user(un),
                pipeline_parts(_load_case(cn), _load_user(un), cfg),
                cfg,
            )
    return out


# ══════════════════════════════════════════════════════════════
# A. 자금상한(A3) — 서비스의 핵심 출력
# ══════════════════════════════════════════════════════════════
def test_i01_ceiling_is_maximal(parts_all):
    """I1 상한 최대성: f(max_bid) >= 0 > f(max_bid + 1단위).

    이분탐색의 off-by-one, 수렴 실패, 내림 방향 오류를 잡는다.
    """
    from src.agents.financing.ltv import loan_at

    for key, (case, user, (rights, _b, fin, _t), cfg) in parts_all.items():
        if fin.max_bid_price == 0:
            continue
        cost_fn = build_cost_fn(case, user, rights, cfg)
        terms = loan_terms_from(fin, case, cfg)

        def f(bid: int) -> int:
            loan, _ = loan_at(bid, terms)
            return (user.cash_available + loan
                    - cost_fn(bid).total_upfront_excl_bid - bid)

        unit = cfg.bid_round_unit
        assert f(fin.max_bid_price) >= 0, f"{key}: 상한에서 자금이 부족하다"
        assert f(fin.max_bid_price + unit) < 0, f"{key}: 상한을 더 올릴 수 있다"


def test_i02_f_is_monotone_decreasing(parts_all):
    """I2 f 단조감소: 이분탐색의 전제. 세율 점프/비용 비단조를 잡는다."""
    from src.agents.financing.ltv import loan_at

    for key, (case, user, (rights, _b, fin, _t), cfg) in parts_all.items():
        cost_fn = build_cost_fn(case, user, rights, cfg)
        terms = loan_terms_from(fin, case, cfg)

        def f(bid: int) -> int:
            loan, _ = loan_at(bid, terms)
            return (user.cash_available + loan
                    - cost_fn(bid).total_upfront_excl_bid - bid)

        lo = case.min_bid_price
        hi = case.appraisal_price * 2
        step = max(1, (hi - lo) // 40)
        prev = f(lo)
        for bid in range(lo + step, hi, step):
            cur = f(bid)
            assert cur <= prev, f"{key}: f가 bid={bid}에서 증가했다"
            prev = cur


def test_i03_within_budget(parts_all):
    """I3 예산 상계: 없는 돈으로 입찰을 권하지 않는다."""
    for key, (_c, user, (_r, _b, fin, _t), _cfg) in parts_all.items():
        assert fin.max_bid_price <= user.cash_available + fin.max_loan, key


def test_i04_non_negative(parts_all):
    """I4 비음수: ltv*0 - room_deduction 이 음수로 새어나가는 것을 막는다."""
    for key, (_c, _u, (_r, _b, fin, _t), _cfg) in parts_all.items():
        assert fin.max_bid_price >= 0, key
        assert fin.max_loan >= 0, key
        assert fin.max_loan_by_ltv >= 0, key
        assert fin.max_loan_by_dsr >= 0, key
        assert fin.monthly_payment_at_max >= 0, key


def test_i05_loan_forbidden_consistency(parts_all):
    """I5 대출불가 일관성: LOAN_FORBIDDEN 이면 대출은 반드시 0이다."""
    for key, (_c, _u, (_r, _b, fin, _t), _cfg) in parts_all.items():
        if fin.binding_constraint == BindingConstraint.LOAN_FORBIDDEN:
            assert fin.max_loan == 0, key
            assert fin.applied_ltv == 0.0, key


def test_i07_dsr_roundtrip(parts_all):
    """I7 DSR 왕복: 역산한 원금의 연 원리금이 한도를 넘지 않는다."""
    from src.agents.financing.dsr import monthly_payment, monthly_terms

    for key, (_c, user, (_r, _b, fin, _t), cfg) in parts_all.items():
        r, n = monthly_terms(user, cfg)
        annual = monthly_payment(fin.max_loan_by_dsr, r, n) * 12
        allowed = user.annual_income * cfg.dsr_limit - user.existing_annual_debt_payment
        assert annual <= max(0.0, allowed) + 12, f"{key}: DSR 한도 초과 {annual}"


def _dsr(user, cfg) -> int:
    from src.agents.financing.dsr import max_loan_by_dsr

    return max_loan_by_dsr(user, cfg)[0]


def test_i08_dsr_comparative_statics():
    """I8 DSR 비교정역학: 파라미터가 뒤바뀌면 반드시 걸린다."""
    from src.schemas.core import Purpose, UserProfile
    from src.utils.config import load_config

    cfg = load_config()

    def U(**kw):
        base = dict(annual_income=50_000_000, cash_available=0,
                    existing_annual_debt_payment=0, owned_house_count=0,
                    purpose=Purpose.OWNER_OCCUPY, age=30, target_loan_years=30)
        base.update(kw)
        return UserProfile(**base)

    base = _dsr(U(), cfg)
    assert _dsr(U(annual_income=80_000_000), cfg) > base, "소득↑ → 한도↑"
    assert _dsr(U(existing_annual_debt_payment=10_000_000), cfg) < base, "기존부채↑ → 한도↓"
    assert _dsr(U(target_loan_years=10), cfg) < base, "기간↓ → 한도↓"


def test_i09_loan_saturates_at_appraisal(parts_all):
    """I9 담보 상한: 감정가를 넘는 입찰가에는 대출이 더 늘지 않는다."""
    from src.agents.financing.ltv import loan_at

    for key, (case, _u, (_r, _b, fin, _t), cfg) in parts_all.items():
        terms = loan_terms_from(fin, case, cfg)
        at_appraisal, _ = loan_at(case.appraisal_price, terms)
        above, _ = loan_at(case.appraisal_price * 2, terms)
        assert above == at_appraisal, f"{key}: 감정가 초과분까지 담보로 잡았다"


def test_i10_unaffordable_is_zero_not_negative(parts_all):
    """I10 감당불가 처리: 최저가조차 못 내면 0 + CASH 로 정직하게 표시한다."""
    for key, (case, _u, (_r, _b, fin, _t), _cfg) in parts_all.items():
        if fin.max_bid_price == 0:
            assert fin.binding_constraint in (
                BindingConstraint.CASH, BindingConstraint.LOAN_FORBIDDEN
            ), key
        else:
            assert fin.max_bid_price >= case.min_bid_price, \
                f"{key}: 법정 최저매각가격 미만을 상한으로 제시했다"


# ══════════════════════════════════════════════════════════════
# B. 총소유비용(A4)
# ══════════════════════════════════════════════════════════════
def test_i12_tco_total_matches_items(parts_all):
    """I12 합계 정합: 항목 누락·이중계상을 잡는다."""
    for key, (_c, _u, (_r, _b, _f, tco), _cfg) in parts_all.items():
        items = (tco.acquisition_tax + tco.local_edu_tax + tco.special_rural_tax
                 + tco.legal_and_bond_fee + tco.eviction_cost
                 + tco.unpaid_maintenance + tco.repair_reserve
                 + tco.assumed_rights_cost)
        assert items == tco.total_upfront_excl_bid, key


def test_i13_breakdown_sums_and_labels_unique(parts_all):
    """I13 breakdown 정합: UI 워터폴이 합계와 어긋나는 고전 버그를 잡는다."""
    for key, (_c, _u, (_r, _b, _f, tco), _cfg) in parts_all.items():
        assert sum(b["amount"] for b in tco.breakdown) == tco.total_upfront_excl_bid, key
        labels = [b["label"] for b in tco.breakdown]
        assert len(labels) == len(set(labels)), f"{key}: 중복 라벨 {labels}"
        assert len(labels) == 8, f"{key}: 항목 수 {len(labels)}"


def test_i14_effective_price_definition(parts_all):
    """I14 실질취득가 정의: 이 서비스의 핵심 지표다."""
    for key, (_c, _u, (_r, _b, _f, tco), _cfg) in parts_all.items():
        assert tco.effective_acquisition_price == (
            tco.bid_price + tco.total_upfront_excl_bid
        ), key


def test_i15_assumed_cost_pass_through(parts_all):
    """I15 인수비용 pass-through: A4가 재계산하면 이중계상이 된다."""
    for key, (_c, _u, (rights, _b, _f, tco), _cfg) in parts_all.items():
        assert tco.assumed_rights_cost == rights.total_assumed_cost, key


def test_i16_tax_continuity_at_tier_boundaries(cfg, cases, users):
    """I16 취득세 경계 연속성: 6억·9억에서 점프가 없어야 f 단조성이 성립한다."""
    from src.agents.tco.tax import acquisition_tax_rate

    case = cases["case_001_clean"].model_copy(update={"building_area_m2": 120.0})
    user = users["user_young"]
    for boundary in (600_000_000, 900_000_000):
        lo_rate, _ = acquisition_tax_rate(boundary, case, user, cfg)
        hi_rate, _ = acquisition_tax_rate(boundary + 1, case, user, cfg)
        lo = int(boundary * lo_rate)
        hi = int((boundary + 1) * hi_rate)
        assert abs(hi - lo) <= 1, f"{boundary}에서 세액 점프 {lo} -> {hi}"


def test_i18_tax_monotone(cfg, cases, users):
    """I18 취득세 단조: 슬라이딩 구간 부호 반전을 잡는다."""
    from src.agents.tco.tax import acquisition_tax_rate

    case = cases["case_001_clean"].model_copy(update={"building_area_m2": 120.0})
    user = users["user_young"]
    prev = -1
    for bid in range(100_000_000, 1_200_000_000, 25_000_000):
        rate, _ = acquisition_tax_rate(bid, case, user, cfg)
        tax = int(bid * rate)
        assert tax >= prev, f"bid={bid}에서 취득세가 감소했다"
        prev = tax


def test_i20_area_none_does_not_raise(cfg, cases, users):
    """I20 면적 방어: None * 3000 TypeError 를 막는다."""
    from src.agents.tco import cost_function
    from src.agents.rights_analysis import run as rights_run

    case = cases["case_001_clean"].model_copy(update={"building_area_m2": None})
    rights = rights_run(cases["case_001_clean"], cfg)
    tco = cost_function(150_000_000, case, users["user_young"], rights, cfg)
    assert tco.unpaid_maintenance == 0


def test_i21_all_money_non_negative(parts_all):
    """I21 전 항목 비음수: 음수 비용이 총액을 깎는 사고를 막는다."""
    for key, (_c, _u, (_r, _b, _f, tco), _cfg) in parts_all.items():
        for field in MONEY_FIELDS:
            assert getattr(tco, field) >= 0, f"{key}.{field}"
        for b in tco.breakdown:
            assert b["amount"] >= 0, f"{key}.{b['label']}"


# ══════════════════════════════════════════════════════════════
# C. 권리분석(A1) — 보수성(P4)이 코드로 강제되는지
# ══════════════════════════════════════════════════════════════
def test_i22_assumed_cost_matches_parts(parts_all):
    """I22 인수총액 정합: 스키마 주석은 정의일 뿐 강제가 아니다."""
    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        total = (sum(r.estimated_cost for r in rights.assumed_rights)
                 + sum(t.expected_assumed_deposit for t in rights.tenants))
        assert total == rights.total_assumed_cost, key


def test_i23_opposing_power_strict_boundary(cfg, cases):
    """I23 대항력 strict 경계: 같은 날이면 저당권 우선.

    부등호 한 글자(< 대신 <=)에 보증금 수천만원이 걸린다. process.md §2.2(b)가
    "반드시 strict inequality로 구현"이라고 못박은 지점이다.
    """
    from datetime import date

    from src.agents.rights_analysis.rules import build_tenant
    from src.agents.rights_analysis.schemas import RawTenantRow
    from src.schemas.core import BaseRight, RightType

    case = cases["case_002_tenant"]
    base = BaseRight(
        right_type=RightType.MORTGAGE,
        registered_date=date(2022, 9, 5),
        holder="○○은행",
    )

    def opposing(move_in: str | None, base_right=base):
        row = RawTenantRow(
            name_masked="김○○",
            move_in_date=move_in,
            deposit=50_000_000,
            dividend_demanded=False,
        )
        tenant, _ = build_tenant(row, base_right, case, 0.6)
        return tenant.opposing_power

    assert opposing("2022-09-04") is True, "하루 빠르면 대항력 있음"
    assert opposing("2022-09-05") is False, "동일 날짜는 저당권 우선 → 대항력 없음"
    assert opposing("2022-09-06") is False
    assert opposing(None) is None, "전입일 불명이면 판단 불가"
    assert opposing("2022-09-04", None) is None, "말소기준권리 불명이면 판단 불가"


def test_i24_no_opposing_power_means_no_assumption(parts_all):
    """I24 무대항력 → 인수 0: 소멸하는 권리를 인수로 계상하지 않는다."""
    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        for t in rights.tenants:
            if t.opposing_power is False:
                assert t.expected_assumed_deposit == 0, f"{key}: {t.name_masked}"


def test_i25_conservative_assumption_enforced(parts_all):
    """I25 보수성 강제(P4): 이 서비스가 실패하는 방식 그 자체를 막는다.

    대항력이 확실히 없지 않고 배당요구가 확실히 있지도 않으면
    보증금은 전액 인수로 잡고 UNCERTAIN 을 붙여야 한다.
    """
    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        for t in rights.tenants:
            unclear = t.opposing_power is not False
            if unclear:
                assert t.expected_assumed_deposit > 0, \
                    f"{key}: {t.name_masked} 불확실한데 인수액 0 (낙관 가정)"


def test_i26_unknown_fields_force_uncertain(parts_all):
    """I26 불명 → UNCERTAIN: 불확실을 CONFIRMED 로 표기하면 즉시 리젝 사유다."""
    from src.schemas.core import Confidence

    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        for t in rights.tenants:
            if t.move_in_date is None or t.deposit is None:
                assert t.confidence == Confidence.UNCERTAIN, f"{key}: {t.name_masked}"


def test_i27_special_rights_force_grade_d(parts_all):
    """I27 특수권리 → D: 등급표 분기 누락을 잡는다."""
    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        if any(r.right_type in SPECIAL_RIGHTS for r in rights.assumed_rights):
            assert rights.risk_grade == RiskGrade.D, key


def test_i28_keyword_always_leaves_a_trace(cfg, cases):
    """I28 키워드 → 반드시 흔적: 골든값 없는 진짜 recall 테스트.

    문서에 특수권리 키워드가 있으면 assumed_rights 나 warnings 중
    어딘가에는 반드시 나타나야 한다. 조용히 사라지면 안 된다.
    """
    from src.agents.rights_analysis import run as rights_run
    from src.utils.io import read_text

    keywords = {
        "유치권": RightType.LIEN,
        "법정지상권": RightType.STATUTORY_SUPERFICIES,
        "분묘기지권": RightType.GRAVE_BASE,
        "토지별도등기": RightType.LAND_SEPARATE_REGISTRY,
    }
    for name, case in cases.items():
        text = read_text(case.documents.sale_spec_path)
        rights = rights_run(case, cfg)
        blob = " ".join(
            [r.description + r.evidence for r in rights.assumed_rights]
            + rights.warnings
        )
        for kw, rt in keywords.items():
            # '토지별도등기 없음' 같은 부정문은 제외
            if kw in text and f"{kw} 없음" not in text:
                found = any(r.right_type == rt for r in rights.assumed_rights) or kw in blob
                assert found, f"{name}: 문서에 '{kw}' 가 있는데 결과에 흔적이 없다"


def test_i29_grade_is_monotone_in_assumed_cost(cfg, cases):
    """I29 등급 단조성: 인수금액만 늘렸는데 등급이 좋아지면 안 된다."""
    from datetime import date

    from src.agents.rights_analysis.rules import determine_risk_grade
    from src.schemas.core import BaseRight, RightType

    order = {RiskGrade.A: 0, RiskGrade.B: 1, RiskGrade.C: 2, RiskGrade.D: 3}
    appraisal = 200_000_000
    base = BaseRight(
        right_type=RightType.MORTGAGE,
        registered_date=date(2022, 1, 1),
        holder="○○은행",
    )
    prev = -1
    for cost in (0, 1_000_000, 19_000_000, 20_000_000, 100_000_000):
        g = determine_risk_grade(
            assumed_rights=[],
            tenants=[],
            base_right=base,
            total_assumed_cost=cost,
            appraisal_price=appraisal,
            eviction_difficulty="LOW",
        )
        assert order[g] >= prev, f"인수금액 {cost}에서 등급이 완화됐다"
        prev = order[g]


def test_i31_tenant_names_are_masked(parts_all):
    """I31 마스킹(P8): evidence 가 원문 인용이라 실명이 새는 경로를 잡는다."""
    import re

    pattern = re.compile(r"^([가-힣]○+|성명불상|미상)$")
    for key, (_c, _u, (rights, _b, _f, _t), _cfg) in parts_all.items():
        for t in rights.tenants:
            assert pattern.match(t.name_masked), f"{key}: 마스킹 안 됨 {t.name_masked!r}"
        # 3글자 연속 한글 이름이 evidence 로 새지 않았는지
        blob = " ".join(r.evidence for r in rights.assumed_rights)
        assert not re.search(r"(?<![가-힣])[가-힣]{3}(?=은|는|이|가|의)\s*임차인", blob), key


# ══════════════════════════════════════════════════════════════
# D. 오케스트레이션·리포트
# ══════════════════════════════════════════════════════════════
def test_i32_i33_recommendation_bounds(cfg, cases, users):
    """I32/I33 추천 상·하한.

    상한은 원안에도 있었지만 하한은 없었다. 하한이 없으면 법정 최저매각가격
    미만 금액을 '노랑(시도 가능)'으로 추천하는 사고가 난다.
    """
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            rec = v.recommended_max_bid
            if rec is None:
                assert v.signal == Signal.RED, f"{cn}x{un}: rec None 인데 RED 아님"
                continue
            assert rec >= case.min_bid_price, f"{cn}x{un}: 최저매각가격 미만 추천"
            assert v.key_numbers["max_bid"] >= rec, f"{cn}x{un}: 상한 초과 추천"


def test_i34_grade_d_forces_red(cfg, cases, users):
    """I34 D등급 전파: 권리 리스크는 자금과 무관하게 우선한다."""
    from src.agents.rights_analysis import run as rights_run
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        if rights_run(case, cfg).risk_grade != RiskGrade.D:
            continue
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            assert v.signal == Signal.RED and v.recommended_max_bid is None, f"{cn}x{un}"


def test_i35_green_requires_low_risk(cfg, cases, users):
    """I35 GREEN 제약: C등급 하향 보정 누락을 잡는다."""
    from src.agents.rights_analysis import run as rights_run
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        grade = rights_run(case, cfg).risk_grade
        for un, user in users.items():
            if orch_run(case, user, cfg).signal == Signal.GREEN:
                assert grade in (RiskGrade.A, RiskGrade.B), f"{cn}x{un}: {grade} 인데 GREEN"


def test_i36_key_numbers_complete_and_faithful(cfg, cases, users):
    """I36 key_numbers 완전·동일성: 리포트 숫자가 필드값과 어긋나면 P3 위반이다."""
    from src.orchestrator import run as orch_run

    required = {"p50", "max_bid", "assumed_cost", "effective_price_at_max",
                "monthly_payment"}
    for cn, case in cases.items():
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            assert required <= set(v.key_numbers), f"{cn}x{un}: {set(v.key_numbers)}"
            rights, band, fin, tco = pipeline_parts(case, user, cfg)
            assert v.key_numbers["max_bid"] == fin.max_bid_price
            assert v.key_numbers["assumed_cost"] == rights.total_assumed_cost
            assert v.key_numbers["monthly_payment"] == fin.monthly_payment_at_max
            if band is not None:
                assert v.key_numbers["p50"] == band.p50


def test_i38_disclaimer_is_rendered(cfg, cases, users):
    """I38 면책 렌더 확인: 필드 기본값이 아니라 실제 렌더 결과에서 본다."""
    from src.schemas.core import DISCLAIMER
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            assert DISCLAIMER in v.report_markdown, f"{cn}x{un}: 리포트에 면책 없음"


def test_i40_band_ordering(cfg, cases):
    """I40 밴드 정렬·정의: 분위수 교차 보정 누락과 비율 필드 불일치를 잡는다."""
    from src.agents.price_band import run as band_run

    for cn, case in cases.items():
        b = band_run(case, cfg, as_of=case.sale_date)
        assert 0 < b.p10 <= b.p50 <= b.p90, f"{cn}: {b.p10}/{b.p50}/{b.p90}"
        assert abs(b.appraisal_ratio_p50 - b.p50 / case.appraisal_price) < 1e-9, cn
        assert b.caveats, f"{cn}: caveats 가 비어 있다"


def test_i41_band_never_breaks_pipeline(cfg, cases):
    """I41 폴백 불파괴: 모델 파일이 없어도 A2 는 절대 예외를 던지지 않는다."""
    from src.agents.price_band import run as band_run

    for cn, case in cases.items():
        b = band_run(case, cfg, as_of=case.sale_date)
        assert b.method in ("MODEL", "HEURISTIC")
        if b.sample_size == 0:
            joined = " ".join(b.caveats)
            assert "0건" in joined or "관측" in joined, f"{cn}: 표본 0건 고지 누락"


def test_i49_all_combinations_smoke(cfg, cases, users):
    """I49 전 조합 스모크: UI 없이 데모 실패 원인의 대부분을 잡는다."""
    from src.orchestrator import run as orch_run

    for cn, case in cases.items():
        for un, user in users.items():
            v = orch_run(case, user, cfg)
            assert v.case_id == case.case_id
            assert v.signal in (Signal.GREEN, Signal.YELLOW, Signal.RED)
            assert v.report_markdown.strip()


def test_i50_schema_roundtrip(parts_all):
    """I50 스키마 왕복: Optional/Enum 직렬화 손실을 잡는다."""
    for key, (_c, _u, parts, _cfg) in parts_all.items():
        for obj in parts:
            if obj is None:
                continue
            restored = type(obj).model_validate_json(obj.model_dump_json())
            assert restored == obj, f"{key}: {type(obj).__name__} 왕복 실패"
