"""A2 피처 정의. process.md §T3 '피처 목록(허용)' 을 그대로 구현한다.

타깃 누수 방지가 이 모듈의 존재 이유다. 금지 피처는 상수로 고정하고
train.py 가 학습 직전에 프레임을 검사한다(문서에만 적어두면 지켜지지 않는다).

지역 인코딩은 **빈도(frequency) 인코딩**이다. 타깃 인코딩은 sold_price 에서
파생되므로 금지 피처에 해당한다.

pandas/numpy 는 함수 안에서만 import 한다.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from src.schemas.core import PropertyCase, PropertyType

# ── 타깃 누수 피처(절대 학습 프레임에 넣지 않는다) ──────────────
# sold_date 시점 이후에만 알 수 있는 값, 낙찰 결과 그 자체에서 파생된 값.
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "sold_price",
        "sold_date",
        "bidder_count",
        "bid_count",
        "appraisal_ratio",
        "sold_ratio",
        "winning_bid",
        "final_price",
        "sale_result",
        "settlement_date",
        "target",
    }
)

TARGET_COLUMN = "appraisal_ratio"

BASE_FEATURE_COLUMNS: tuple[str, ...] = (
    "property_type_code",
    "building_area_m2",
    "built_year",
    "floor",
    "region_freq",
    "log_appraisal_price",
    "min_bid_ratio",
    "failed_count",
)
# 실거래(T1b)가 있을 때만 붙는 시장 피처.
MARKET_FEATURE_COLUMNS: tuple[str, ...] = ("appraisal_over_trade_median",)
FEATURE_COLUMNS: tuple[str, ...] = BASE_FEATURE_COLUMNS + MARKET_FEATURE_COLUMNS

TRADE_LOOKBACK_MONTHS = 6

PROPERTY_TYPE_CODES: dict[str, int] = {
    pt.value: idx for idx, pt in enumerate(PropertyType)
}


class LeakageError(ValueError):
    """학습 프레임에 타깃 누수 피처가 들어 있음."""


def assert_no_forbidden(columns: Iterable[str]) -> None:
    """금지 피처가 하나라도 있으면 즉시 실패한다."""
    hits = sorted(set(columns) & FORBIDDEN_FEATURES)
    if hits:
        raise LeakageError(
            "타깃 누수 피처가 학습 프레임에 포함되었습니다: " + ", ".join(hits)
        )


def region_frequency(frame: Any) -> dict[str, float]:
    """region_code 빈도 인코딩 테이블(학습 데이터에서만 만든다)."""
    if len(frame) == 0:
        return {}
    counts = frame["region_code"].astype("string").value_counts()
    total = float(counts.sum())
    return {str(k): float(v) / total for k, v in counts.items()}


def trade_median_unit_price(
    trades: Any, as_of: date, months: int = TRADE_LOOKBACK_MONTHS
) -> dict[str, float]:
    """region_code → 최근 N개월 실거래 중위 단가(원/m²). 없으면 빈 dict."""
    import pandas as pd

    if trades is None or len(trades) == 0:
        return {}
    end = pd.Timestamp(as_of)
    start = end - pd.DateOffset(months=months)
    recent = trades[(trades["deal_date"] >= start) & (trades["deal_date"] <= end)]
    recent = recent[recent["area_m2"] > 0]
    if len(recent) == 0:
        return {}
    unit_price = recent["deal_amount"] / recent["area_m2"]
    grouped = unit_price.groupby(recent["region_code"].astype("string")).median()
    return {str(k): float(v) for k, v in grouped.items()}


def _market_ratio(
    appraisal_price: float, area_m2: float | None, median_unit: float | None
) -> float:
    """감정가 단가 / 실거래 중위 단가. 정보가 없으면 NaN(LightGBM 이 결측으로 처리)."""
    if not median_unit or not area_m2 or area_m2 <= 0 or median_unit <= 0:
        return float("nan")
    return (appraisal_price / area_m2) / median_unit


def build_features(
    frame: Any,
    *,
    region_freq: dict[str, float],
    trade_median: dict[str, float] | None = None,
) -> Any:
    """학습·추론 공통 피처 프레임. 컬럼 순서는 FEATURE_COLUMNS 고정."""
    import numpy as np
    import pandas as pd

    trade_median = trade_median or {}
    region = frame["region_code"].astype("string")
    appraisal = pd.to_numeric(frame["appraisal_price"], errors="coerce").astype("float64")
    area = pd.to_numeric(frame["building_area_m2"], errors="coerce").astype("float64")
    min_bid = pd.to_numeric(frame["min_bid_price"], errors="coerce").astype("float64")

    out = pd.DataFrame(index=frame.index)
    out["property_type_code"] = (
        frame["property_type"].astype("string").map(PROPERTY_TYPE_CODES).astype("float64")
    )
    out["building_area_m2"] = area
    out["built_year"] = (
        pd.to_numeric(frame["built_year"], errors="coerce").astype("float64")
        if "built_year" in frame.columns
        else np.nan
    )
    out["floor"] = (
        pd.to_numeric(frame["floor"], errors="coerce").astype("float64")
        if "floor" in frame.columns
        else np.nan
    )
    out["region_freq"] = region.map(region_freq).astype("float64").fillna(0.0)
    out["log_appraisal_price"] = np.log(appraisal.clip(lower=1.0))
    out["min_bid_ratio"] = (min_bid / appraisal.replace(0.0, np.nan)).astype("float64")
    out["failed_count"] = (
        pd.to_numeric(frame["failed_count"], errors="coerce").fillna(0).astype("float64")
    )

    medians = region.map(trade_median).astype("float64")
    out["appraisal_over_trade_median"] = [
        _market_ratio(a, ar, m)
        for a, ar, m in zip(appraisal.tolist(), area.tolist(), medians.tolist())
    ]

    result = out[list(FEATURE_COLUMNS)]
    # 산출된 피처 행렬에는 금지 피처가 절대 없어야 한다(문서가 아니라 런타임에서 막는다).
    assert_no_forbidden(result.columns)
    return result


def case_to_frame(case: PropertyCase) -> Any:
    """단건 추론용 1행 프레임(수집 스키마와 같은 컬럼명)."""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "case_id": case.case_id,
                "region_code": case.region_code,
                "property_type": case.property_type.value,
                "appraisal_price": int(case.appraisal_price),
                "min_bid_price": int(case.min_bid_price),
                "failed_count": int(case.failed_count),
                "building_area_m2": (
                    float(case.building_area_m2)
                    if case.building_area_m2 is not None
                    else float("nan")
                ),
                "built_year": (
                    float(case.built_year) if case.built_year is not None else float("nan")
                ),
                "floor": float(case.floor) if case.floor is not None else float("nan"),
            }
        ]
    )


def build_target(frame: Any) -> Any:
    """타깃 = sold_price / appraisal_price (가격 자체보다 안정적)."""
    import pandas as pd

    appraisal = pd.to_numeric(frame["appraisal_price"], errors="coerce").astype("float64")
    sold = pd.to_numeric(frame["sold_price"], errors="coerce").astype("float64")
    return (sold / appraisal.replace(0.0, float("nan"))).astype("float64")
