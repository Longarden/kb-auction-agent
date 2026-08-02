"""A4 세금 계산기 — 순수 결정론적 규칙 엔진.

규제 수치는 전부 Config 를 통해서만 읽는다(process.md P5). 이 모듈에는
규제성 숫자 리터럴이 존재하지 않는다.

정수화 규약: 모든 세액은 int() 절사(버림)로 통일한다. 반올림과 절사를
섞으면 동일 입력에 대해 1원 단위 드리프트가 생겨 스냅샷 재현성이 깨진다.
"""
from __future__ import annotations

import logging

from src.schemas.core import PropertyCase, PropertyType, RegulationZone, UserProfile
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 주거용으로 보지 않는 물건 유형(취득세·농특세·방공제 판정 공통)
NON_HOUSING_TYPES = (PropertyType.COMMERCIAL, PropertyType.LAND)

# 다주택 중과 테이블에서 조정 대상으로 취급하는 규제지역
SURCHARGE_ZONES = (RegulationZone.ADJUSTMENT, RegulationZone.SPECULATION)

# 다주택 중과 테이블 키
_KEY_TWO_HOUSES = "h2"
_KEY_THREE_PLUS = "h3plus"


def is_housing(case: PropertyCase) -> bool:
    """주택(주거용)으로 취급하는지 여부."""
    return case.property_type not in NON_HOUSING_TYPES


def acquisition_tax_rate(
    bid: int, case: PropertyCase, user: UserProfile, cfg: Config
) -> tuple[float, str]:
    """(적용 세율, 근거 문자열)."""
    block = cfg.regulation["acquisition_tax"]

    if not is_housing(case):
        rate = float(block["commercial_rate"])
        return rate, f"비주거({case.property_type.value}) 취득세율 {rate:.4f} 적용"

    house = block["house"]
    prefix = ""

    # 1) 다주택 중과 우선 판정. null 값은 '기본세율 적용'을 뜻하므로 산술하지 않는다.
    if user.owned_house_count >= 2:
        if case.regulation_zone in SURCHARGE_ZONES:
            table = house["multi_house_adjust"]
            zone_label = f"{case.regulation_zone.value}(조정/투기)"
        else:
            table = house["multi_house_none"]
            zone_label = f"{case.regulation_zone.value}(비규제)"
        key = (
            _KEY_TWO_HOUSES
            if user.owned_house_count == 2
            else _KEY_THREE_PLUS
        )
        surcharge = table.get(key)
        if surcharge is not None:
            rate = float(surcharge)
            return (
                rate,
                f"{user.owned_house_count}주택자 {zone_label} 중과세율 "
                f"{rate:.4f}({key}) 적용",
            )
        prefix = (
            f"{user.owned_house_count}주택자 {zone_label} 중과세율 미설정(null) "
            f"→ 기본세율 적용; "
        )

    # 2) 기본 누진 구간
    tier_1_max = int(house["tier_1_max"])
    tier_2_max = int(house["tier_2_max"])

    if bid <= tier_1_max:
        rate = float(house["tier_1_rate"])
        return rate, f"{prefix}{tier_1_max:,}원 이하 구간 세율 {rate:.4f} 적용"

    if bid > tier_2_max:
        rate = float(house["tier_3_rate"])
        return rate, f"{prefix}{tier_2_max:,}원 초과 구간 세율 {rate:.4f} 적용"

    slide_coef = float(house["slide_coef"])
    slide_div = float(house["slide_div"])
    slide_const = float(house["slide_const"])
    slide_pct_divisor = float(house["slide_pct_divisor"])
    rate = (bid * slide_coef / slide_div - slide_const) / slide_pct_divisor
    return (
        rate,
        f"{prefix}{tier_1_max:,}~{tier_2_max:,}원 슬라이딩 구간 "
        f"세율 {rate:.6f} 적용",
    )


def local_edu_tax(bid: int, main_rate: float, is_housing_flag: bool, cfg: Config) -> int:
    """지방교육세. 주택은 본세율 연동, 비주거는 별도 정률."""
    block = cfg.regulation["acquisition_tax"]
    if is_housing_flag:
        ratio = float(block["local_edu_tax_of_main_rate"])
        return int(bid * main_rate * ratio)
    return int(bid * float(block["commercial_local_edu_rate"]))


def special_rural_tax(
    bid: int, case: PropertyCase, is_housing_flag: bool, cfg: Config
) -> tuple[int, str]:
    """(농어촌특별세, 근거). 주택 전용면적 기준 이하면 비과세.

    면적 불명(None)이면 보수적으로 과세한다(P4: 불확실 → 비용 과소평가 금지).
    """
    block = cfg.regulation["acquisition_tax"]
    rate = float(block["special_rural_tax_rate"])
    exempt_area = float(block["special_rural_exempt_area_m2"])

    if is_housing_flag:
        area = case.building_area_m2
        if area is not None and area <= exempt_area:
            return 0, f"주택 전용 {area:g}m² ≤ {exempt_area:g}m² → 농특세 비과세"
        if area is None:
            return (
                int(bid * rate),
                f"면적 불명 → 보수적으로 농특세 {rate:.4f} 과세",
            )
        return (
            int(bid * rate),
            f"주택 전용 {area:g}m² > {exempt_area:g}m² → 농특세 {rate:.4f} 과세",
        )
    return int(bid * rate), f"비주거 → 농특세 {rate:.4f} 과세"
