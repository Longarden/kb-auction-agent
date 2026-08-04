"""A5 자격 판정 — 100% 룰. LLM 을 쓰지 않는다(process.md §T6, P3).

자격 판정에 LLM 을 쓰면 같은 입력이 다른 답을 내고 근거를 감사할 수 없다.
모든 조건은 data/policies.yaml 의 conditions 키와 1:1 로 대응하는 함수로
평가하고, 통과/불통과를 사람이 읽을 한국어 문장으로 남긴다.

`applicable_to_auction` 이 None 인 상품 처리(설계 결정):
    "경락잔금 용도 가능 여부 확인 필요"는 **실패한 요건이 아니다**. 사용자가
    조건을 못 맞춘 게 아니라 우리가 상품 약관을 확인하지 못한 것이다. 그래서
    requirements_unmet 에 넣지 않고(넣으면 applicable=False 가 되어 자격이
    있는 사용자에게 없다고 말하게 된다) rate_note 앞에 고정 접두사를 붙인다.
    UI 는 이 접두사만 보고 '확인 필요' 배지를 띄우면 된다.
"""
from __future__ import annotations

from typing import Any, Optional

from src.schemas.core import (
    FinancingCeiling,
    ProductMatch,
    PropertyCase,
    UserProfile,
)
from src.utils.io import read_yaml

POLICIES_PATH = "data/policies.yaml"

# UI 가 배지를 키잉하는 고정 접두사. 문자열을 바꾸면 UI 도 함께 바꿔야 한다.
AUCTION_UNKNOWN_PREFIX = "[확인 필요] 경락잔금 용도 가능 여부 미확인 — "
# verified: false 상품에 붙는 고정 접미사.
UNVERIFIED_SUFFIX = " (조건·금리 미확인 — 취급 기관에서 확인 필요)"

AUCTION_NOT_ALLOWED = "경락잔금(경매 낙찰자금) 용도로는 이용할 수 없는 상품입니다."
AUCTION_ALLOWED = "경락잔금(경매 낙찰자금) 용도로 이용 가능한 상품입니다."

# conditions 에서 자격 게이트가 아닌 키(상품 한도 표시에만 쓴다).
_NON_GATE_KEYS = frozenset({"max_amount"})

SUPPORTED_CONDITION_KEYS = frozenset(
    {
        "houseless_only",
        "max_annual_income",
        "max_house_price",
        "min_age",
        "max_age",
        "purposes",
        "max_amount",
        "owned_house_max",
    }
)


class PolicyDefinitionError(ValueError):
    """policies.yaml 에 룰 엔진이 모르는 조건 키가 있음."""


def load_products(path: str = POLICIES_PATH) -> list[dict[str, Any]]:
    doc = read_yaml(path)
    products = list(doc.get("products", []))
    for product in products:
        unknown = set(product.get("conditions") or {}) - SUPPORTED_CONDITION_KEYS
        if unknown:
            raise PolicyDefinitionError(
                f"{product.get('product_id')}: 미지원 조건 키 {sorted(unknown)}"
            )
    return products


def _won(value: int) -> str:
    return f"{int(value):,}원"


def _check_conditions(
    conditions: dict[str, Any], case: PropertyCase, user: UserProfile
) -> tuple[list[str], list[str]]:
    """(충족 요건, 미충족 요건). 한 조건이 한 문장을 만든다."""
    met: list[str] = []
    unmet: list[str] = []

    for key, value in conditions.items():
        if key in _NON_GATE_KEYS or value is None:
            continue

        if key == "houseless_only" and value:
            text = f"무주택 요건(보유 주택 {user.owned_house_count}건)"
            (met if user.owned_house_count == 0 else unmet).append(text)

        elif key == "owned_house_max":
            text = f"보유 주택 {value}건 이하(현재 {user.owned_house_count}건)"
            (met if user.owned_house_count <= int(value) else unmet).append(text)

        elif key == "max_annual_income":
            text = f"연소득 {_won(value)} 이하(현재 {_won(user.annual_income)})"
            (met if user.annual_income <= int(value) else unmet).append(text)

        elif key == "max_house_price":
            text = (
                f"주택가격 {_won(value)} 이하"
                f"(감정가 {_won(case.appraisal_price)} 기준)"
            )
            (met if case.appraisal_price <= int(value) else unmet).append(text)

        elif key == "min_age":
            text = f"만 {value}세 이상(현재 {user.age}세)"
            (met if user.age >= int(value) else unmet).append(text)

        elif key == "max_age":
            text = f"만 {value}세 이하(현재 {user.age}세)"
            (met if user.age <= int(value) else unmet).append(text)

        elif key == "purposes":
            allowed = [str(v) for v in value]
            text = f"자금 용도 {'/'.join(allowed)} 전용(선택 용도 {user.purpose.value})"
            (met if user.purpose.value in allowed else unmet).append(text)

    return met, unmet


def evaluate(
    product: dict[str, Any], case: PropertyCase, user: UserProfile
) -> ProductMatch:
    """상품 1건 자격 판정."""
    conditions: dict[str, Any] = product.get("conditions") or {}
    met, unmet = _check_conditions(conditions, case, user)

    to_auction: Optional[bool] = product.get("applicable_to_auction")
    rate_note = str(product.get("rate_note", ""))

    if to_auction is True:
        met.append(AUCTION_ALLOWED)
    elif to_auction is False:
        # 이건 진짜 실패 요건이다. 상품 자체가 이 목적에 쓰이지 못한다.
        unmet.append(AUCTION_NOT_ALLOWED)
    else:
        rate_note = AUCTION_UNKNOWN_PREFIX + rate_note

    if not product.get("verified", False):
        rate_note = rate_note + UNVERIFIED_SUFFIX

    checked = product.get("checked_date")

    return ProductMatch(
        product_id=str(product["product_id"]),
        name=str(product["name"]),
        category=str(product["category"]),
        applicable=not unmet,
        applicable_to_auction=to_auction,
        max_amount=conditions.get("max_amount"),
        rate_note=rate_note,
        requirements_met=met,
        requirements_unmet=unmet,
        source_url=str(product.get("source_url") or ""),
        checked_date=checked,
    )


def match_all(
    products: list[dict[str, Any]], case: PropertyCase, user: UserProfile
) -> list[ProductMatch]:
    """applicable=True 를 앞으로 올린다(UI 가 상단에 노출). 그 외 순서는 yaml 순서 유지."""
    matches = [evaluate(p, case, user) for p in products]
    return sorted(matches, key=lambda m: not m.applicable)


def needs_auction_check(match: ProductMatch) -> bool:
    """UI 배지용. 경락잔금 용도 확인이 필요한 카드인지."""
    return match.applicable_to_auction is None


def within_financing(match: ProductMatch, financing: FinancingCeiling) -> bool:
    """상품 한도가 A3 산정 대출 필요액을 덮는지. 한도 미표기(None)면 판단하지 않고 True."""
    if match.max_amount is None:
        return True
    return int(match.max_amount) >= int(financing.max_loan)
