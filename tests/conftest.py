"""공용 픽스처. 골든값(정답 숫자)에 의존하지 않는 것이 원칙이다.

fixture 기대값 JSON을 만든 사람과 구현한 사람이 같으면 스냅샷 테스트는
impl(x) == impl(x) 라는 항진명제가 된다. 그래서 이 테스트 스위트의 중심은
스냅샷이 아니라 '관계·부호·단조성·항등식'을 보는 불변식이다.
"""
from __future__ import annotations

from functools import partial

import pytest

from src.schemas.core import PropertyCase, UserProfile
from src.utils.config import load_config
from src.utils.io import REPO_ROOT, read_json

CASE_NAMES = ["case_001_clean", "case_002_tenant", "case_003_lien", "case_004_noise"]
USER_NAMES = ["user_young", "user_invest", "user_tight"]


@pytest.fixture(scope="session")
def cfg():
    return load_config()


def _load_case(name: str) -> PropertyCase:
    return PropertyCase.model_validate(read_json(f"data/samples/{name}/case.json"))


def _load_user(name: str) -> UserProfile:
    raw = read_json("data/samples/users.json")[name]
    return UserProfile.model_validate({k: v for k, v in raw.items() if k != "label"})


@pytest.fixture(scope="session")
def cases() -> dict[str, PropertyCase]:
    return {n: _load_case(n) for n in CASE_NAMES}


@pytest.fixture(scope="session")
def users() -> dict[str, UserProfile]:
    return {n: _load_user(n) for n in USER_NAMES}


@pytest.fixture(params=CASE_NAMES)
def case(request) -> PropertyCase:
    return _load_case(request.param)


@pytest.fixture(params=USER_NAMES)
def user(request) -> UserProfile:
    return _load_user(request.param)


@pytest.fixture(scope="session")
def combos(cases, users) -> list[tuple[str, PropertyCase, str, UserProfile]]:
    return [
        (cn, cases[cn], un, users[un]) for cn in CASE_NAMES for un in USER_NAMES
    ]


def build_cost_fn(case, user, rights, cfg):
    from src.agents.tco import cost_function

    return partial(cost_function, case=case, user=user, rights=rights, cfg=cfg)


def loan_terms_from(fin, case, cfg):
    """FinancingCeiling 결과에서 대출 조건 객체를 복원한다.

    불변식 테스트가 loan(bid) 를 임의의 입찰가에서 다시 평가해야 하는데,
    A3 는 최종 결과만 돌려주므로 여기서 조건을 재구성한다.
    """
    from src.agents.financing.ltv import LoanTerms
    from src.schemas.core import BindingConstraint

    return LoanTerms(
        applied_ltv=fin.applied_ltv,
        room_deduction=fin.room_deduction,
        dsr_limit_amount=fin.max_loan_by_dsr,
        product_cap=cfg.product_cap,
        appraisal_price=case.appraisal_price,
        loan_forbidden=(fin.binding_constraint == BindingConstraint.LOAN_FORBIDDEN),
    )


def pipeline_parts(case, user, cfg):
    """오케스트레이터를 거치지 않고 중간 산출물을 전부 얻는다."""
    from src.agents.financing import run as fin_run
    from src.agents.price_band import run as band_run
    from src.agents.rights_analysis import run as rights_run
    from src.agents.tco import cost_function

    rights = rights_run(case, cfg)
    band = band_run(case, cfg, as_of=case.sale_date)
    cost_fn = build_cost_fn(case, user, rights, cfg)
    financing = fin_run(case, user, rights, cost_fn, cfg)
    bid = max(financing.max_bid_price, case.min_bid_price)
    tco = cost_function(bid, case, user, rights, cfg)
    return rights, band, financing, tco


MONEY_FIELDS = (
    "acquisition_tax",
    "local_edu_tax",
    "special_rural_tax",
    "legal_and_bond_fee",
    "eviction_cost",
    "unpaid_maintenance",
    "repair_reserve",
    "assumed_rights_cost",
    "total_upfront_excl_bid",
    "effective_acquisition_price",
)

__all__ = [
    "CASE_NAMES",
    "USER_NAMES",
    "MONEY_FIELDS",
    "REPO_ROOT",
    "build_cost_fn",
    "loan_terms_from",
    "pipeline_parts",
]
