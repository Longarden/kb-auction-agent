"""A5 진입점. process.md §T6.

    run(case, user, financing, cfg) -> PolicyMatchResult

자격 판정은 eligibility.py 의 룰이 전부 담당한다. LLM 은 summary_note 서술에만
허용되지만(P3) **오늘의 기본 경로는 결정적 템플릿**이다. 인증키가 없고, 템플릿이
같은 입력에 같은 문장을 내며, 숫자를 생성하지 않고 필드값만 치환하기 때문이다.
LLM 을 붙일 경우에도 반드시 src.llm.client 를 경유하고 숫자는 생성시키지 않는다.

summary_note 는 사용자 노출 문자열이므로 guard.assert_clean 를 통과시킨다.
"""
from __future__ import annotations

import logging

from src.agents.policy_match import eligibility
from src.schemas.core import (
    FinancingCeiling,
    PolicyMatchResult,
    ProductMatch,
    PropertyCase,
    UserProfile,
)
from src.utils.config import Config
from src.utils.guard import assert_clean

log = logging.getLogger(__name__)

SUMMARY_TEMPLATE = (
    "검토한 상품 {total}건 중 {applicable}건이 입력하신 조건(보유 주택 수·연소득·"
    "나이·자금 용도·감정가)에 부합합니다. "
    "이 중 {auction_unknown}건은 경락잔금 용도로 쓸 수 있는지 확인되지 않아 취급 기관 "
    "확인이 필요하고, {auction_blocked}건은 경락잔금 용도로 쓸 수 없는 상품입니다. "
    "자금조달 상한 계산({binding} 기준)상 대출 가능액은 {max_loan}원이므로 "
    "상품 한도가 이보다 작으면 부족분은 현금으로 메워야 합니다. "
    "모든 상품의 조건·금리·한도는 확인 전 예시값이며 실제 취급 조건은 기관에서 확인해야 합니다."
)


def build_summary(
    matches: list[ProductMatch], financing: FinancingCeiling
) -> str:
    """결정적 템플릿. 숫자는 전부 필드값 치환이며 새로 만들어내지 않는다."""
    return SUMMARY_TEMPLATE.format(
        total=len(matches),
        applicable=sum(1 for m in matches if m.applicable),
        auction_unknown=sum(
            1 for m in matches if m.applicable and eligibility.needs_auction_check(m)
        ),
        auction_blocked=sum(1 for m in matches if m.applicable_to_auction is False),
        binding=financing.binding_constraint.value,
        max_loan=f"{int(financing.max_loan):,}",
    )


def run(
    case: PropertyCase,
    user: UserProfile,
    financing: FinancingCeiling,
    cfg: Config,
    policies_path: str = eligibility.POLICIES_PATH,
) -> PolicyMatchResult:
    products = eligibility.load_products(policies_path)
    matches = eligibility.match_all(products, case, user)
    summary = assert_clean(
        build_summary(matches, financing), where="A5.summary_note", check_band=False
    )
    return PolicyMatchResult(
        case_id=case.case_id, products=matches, summary_note=summary
    )
