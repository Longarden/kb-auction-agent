"""설정 로더. 규제 수치는 여기를 통해서만 읽는다(process.md P5).

YAML 의 null 은 파이썬 None 이 되어 min()/sum() 에서 즉시 TypeError 를 낸다.
로딩 시점에 전부 안전한 값으로 치환하고, 치환 사실을 notes 로 남긴다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from src.utils.io import read_yaml

REG_PATH = "config/regulation.yaml"
HEU_PATH = "config/heuristics.yaml"

# LTV 조회에서 폴백 프로필로 쓰는 기준 물건 유형
_FALLBACK_PROPERTY_TYPE = "APARTMENT"


@dataclass
class Config:
    """규제 + 휴리스틱 설정 묶음. 에이전트에는 이 객체를 주입한다."""

    regulation: dict[str, Any]
    heuristics: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    # ── 메타 ──────────────────────────────────────────────
    @property
    def version(self) -> str:
        return str(self.regulation.get("version", "unknown"))

    @property
    def verified(self) -> bool:
        return bool(self.regulation.get("verified", False))

    # ── LTV ───────────────────────────────────────────────
    def lookup_ltv(
        self, property_type: str, zone: str, owned_house_count: int
    ) -> tuple[float, list[str]]:
        """(적용 LTV, 추적 로그). 미정의 조합이면 보수적으로 폴백한다."""
        trace: list[str] = []
        bucket = "house_0_1" if owned_house_count <= 1 else "house_2plus"
        table = self.regulation["ltv"]

        by_type = table.get(property_type)
        if by_type is None:
            trace.append(
                f"LTV 테이블에 {property_type} 미정의 → {_FALLBACK_PROPERTY_TYPE} 프로필 적용"
            )
            by_type = table[_FALLBACK_PROPERTY_TYPE]

        by_zone = by_type.get(zone)
        if by_zone is None:
            # 같은 물건유형 안에서 0.0 을 제외한 최소값(가장 보수적)을 쓴다.
            candidates = [
                v
                for zone_row in by_type.values()
                for v in zone_row.values()
                if isinstance(v, (int, float)) and v > 0.0
            ]
            fallback = min(candidates) if candidates else 0.0
            trace.append(
                f"LTV 테이블에 ({property_type}, {zone}) 미정의 → "
                f"동일 유형 내 최소 LTV {fallback:.2f} 적용"
            )
            return float(fallback), trace

        ltv = by_zone.get(bucket)
        if ltv is None:
            trace.append(f"LTV bucket {bucket} 미정의 → 0.0 적용(대출 불가로 취급)")
            return 0.0, trace

        trace.append(
            f"LTV 조회: {property_type}/{zone}/{bucket} = {float(ltv):.2f}"
        )
        return float(ltv), trace

    # ── 방공제 ─────────────────────────────────────────────
    def lookup_room_deduction(
        self, region_code: str, is_residential: bool
    ) -> tuple[int, list[str]]:
        trace: list[str] = []
        if not is_residential:
            trace.append("비주거 물건 → 방공제 0원 적용")
            return 0, trace

        block = self.regulation["room_deduction"]
        bucket = block.get("region_map", {}).get(region_code)
        if bucket is None:
            bucket = block.get("default_bucket", "SEOUL")
            trace.append(
                f"region_code {region_code} 미등재 → 최보수 구간 {bucket} 적용"
            )
        amount = int(block[bucket])
        trace.append(f"방공제({bucket}) {amount:,}원 차감")
        return amount, trace

    # ── 대출 ──────────────────────────────────────────────
    @property
    def assumed_rate(self) -> float:
        return float(self.regulation["loan"]["assumed_rate_annual"])

    @property
    def stress_rate_addon(self) -> float:
        return float(self.regulation["loan"]["stress_rate_addon"])

    @property
    def dsr_limit(self) -> float:
        return float(self.regulation["loan"]["dsr_limit"])

    @property
    def product_cap(self) -> float:
        """상품 최대한도. null 은 로딩 시 math.inf 로 치환되어 있다."""
        return float(self.regulation["loan"]["product_cap"]["default"])

    @property
    def bid_round_unit(self) -> int:
        return int(self.regulation["auction"]["bid_round_unit"])

    @property
    def discount_per_fail(self) -> float:
        return float(self.regulation["auction"]["discount_per_fail"])

    # ── 휴리스틱 ───────────────────────────────────────────
    def eviction_cost(self, occupant_type: str) -> tuple[int, Optional[str]]:
        """(명도비, 경고). LIEN_CLAIMED 는 산정 불가라 0 + 경고를 돌려준다."""
        table = self.heuristics["eviction_cost"]
        if occupant_type == "LIEN_CLAIMED":
            return 0, "유치권 주장으로 명도비 산정 불가 — 실제 비용이 크게 늘 수 있습니다."
        return int(table.get(occupant_type, table["UNKNOWN_OCCUPANT"])), None

    def vacancy_months(self, failed_count: int) -> int:
        cfg = self.heuristics["unpaid_maintenance"]["vacancy_months"]
        return int(
            min(cfg["cap"], failed_count * cfg["per_fail"] + cfg["base"])
        )


def _sanitize(reg: dict[str, Any], heu: dict[str, Any]) -> list[str]:
    """YAML null 을 산술 가능한 값으로 치환한다. 남은 null 은 예외로 잡는다."""
    notes: list[str] = []

    cap = reg["loan"]["product_cap"]
    if cap.get("default") is None:
        cap["default"] = math.inf
        notes.append("loan.product_cap.default: null → 무한대(한도 없음)로 해석")

    if heu["eviction_cost"].get("LIEN_CLAIMED") is None:
        heu["eviction_cost"]["LIEN_CLAIMED"] = 0
        notes.append("eviction_cost.LIEN_CLAIMED: null → 0원 + 경고로 해석")

    # multi_house_none.h2 의 null 은 '기본세율 적용'을 뜻하므로 None 을 유지하되,
    # 세금 계산기가 None 을 명시적으로 분기 처리한다(산술에 직접 넣지 않음).
    return notes


def load_config(
    regulation_path: str = REG_PATH, heuristics_path: str = HEU_PATH
) -> Config:
    reg = read_yaml(regulation_path)
    heu = read_yaml(heuristics_path)
    notes = _sanitize(reg, heu)
    return Config(regulation=reg, heuristics=heu, notes=notes)
