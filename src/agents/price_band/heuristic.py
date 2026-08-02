"""A2 HEURISTIC 경로 — 표본이 1건 이상일 때의 세그먼트 통계.

process.md §T3 폴백 그대로: 동일 region_code × property_type 의 최근 12개월
낙찰가율 중앙값을 p50, IQR 로 p10/p90. 세그먼트 표본이 10건 미만이면
시도(region_code 앞 2자리) → 전체 순으로 넓히고, 최종 표본이 0건이면
zero_data 사전 경로로 위임한다.

`as_of` 는 반드시 인자로 받는다. 이 모듈은 시계를 읽지 않는다 —
date.today() 를 쓰면 같은 입력이 날짜에 따라 다른 출력을 내서 §T3 의
결정성 요구를 깨뜨린다.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from src.agents.price_band import zero_data
from src.agents.price_band.zero_data import ceil_unit, floor_unit
from src.schemas.core import ComparableCase, PriceBandEstimate, PropertyCase
from src.utils.config import Config

MODEL_VERSION = "segment-median-v1"

# 세그먼트를 넓히는 최소 표본 기준(process.md §T3)
MIN_SEGMENT_ROWS = 10
LOOKBACK_MONTHS = 12

# 정규분포 가정에서 IQR = 1.349σ, p10 = Q1 - 0.607σ ≈ Q1 - 0.45×IQR.
# 사분위수만으로 10/90 분위를 외삽하기 위한 계수이며 관측된 분위수가 아니다.
IQR_TO_TAIL = 0.45

MAX_COMPARABLES = 5


def select_segment(frame: Any, case: PropertyCase, as_of: date) -> tuple[Any, str]:
    """(선택된 표본, 세그먼트 설명). 표본이 모자라면 단계적으로 넓힌다."""
    import pandas as pd

    end = pd.Timestamp(as_of)
    start = end - pd.DateOffset(months=LOOKBACK_MONTHS)
    recent = frame[(frame["sold_date"] >= start) & (frame["sold_date"] <= end)]

    ptype = case.property_type.value
    segment = recent[
        (recent["region_code"] == case.region_code)
        & (recent["property_type"] == ptype)
    ]
    scope = f"시군구({case.region_code})×{ptype}, 최근 {LOOKBACK_MONTHS}개월"

    if len(segment) < MIN_SEGMENT_ROWS:
        sido = case.region_code[:2]
        wider = recent[
            (recent["region_code"].astype("string").str[:2] == sido)
            & (recent["property_type"] == ptype)
        ]
        if len(wider) > len(segment):
            segment = wider
            scope = f"시도({sido})×{ptype}, 최근 {LOOKBACK_MONTHS}개월"

    if len(segment) < MIN_SEGMENT_ROWS and len(recent) > len(segment):
        segment = recent
        scope = f"전체 지역·유형, 최근 {LOOKBACK_MONTHS}개월"

    return segment, scope


def _ratio_series(segment: Any) -> tuple[Any, Any]:
    usable = segment[(segment["appraisal_price"] > 0) & (segment["sold_price"] > 0)]
    return usable["sold_price"] / usable["appraisal_price"], usable


def select_comparables(
    segment: Any, case: PropertyCase, top_n: int = MAX_COMPARABLES
) -> list[ComparableCase]:
    """[면적, log(감정가)] 표준화 거리 상위 N건. 근거 제시용이며 예측에 재사용하지 않는다."""
    import numpy as np

    if len(segment) == 0:
        return []
    try:
        area = segment["building_area_m2"].astype("float64").to_numpy()
        logp = np.log(segment["appraisal_price"].clip(lower=1).astype("float64").to_numpy())
        case_area = float(case.building_area_m2 or np.nanmedian(area))
        case_logp = float(np.log(max(case.appraisal_price, 1)))

        def _z(values: Any, point: float) -> Any:
            std = float(np.nanstd(values))
            if not np.isfinite(std) or std == 0.0:
                return np.zeros(len(values))
            return (values - point) / std

        dist = np.sqrt(_z(area, case_area) ** 2 + _z(logp, case_logp) ** 2)
        dist = np.nan_to_num(dist, nan=float("inf"))
        order = np.argsort(dist)[:top_n]
    except Exception:  # pragma: no cover - 근거 표시는 실패해도 밴드를 막지 않는다
        return []

    out: list[ComparableCase] = []
    for idx in order:
        row = segment.iloc[int(idx)]
        appraisal = int(row["appraisal_price"])
        if appraisal <= 0:
            continue
        d = float(dist[int(idx)])
        out.append(
            ComparableCase(
                case_id=str(row["case_id"]),
                # 수집 스키마에 주소가 없다. 없는 주소를 지어내지 않고 지역코드·유형으로 표기한다.
                address_short=f"{row['region_code']} {row['property_type']}",
                sold_price=int(row["sold_price"]),
                sold_date=row["sold_date"].date(),
                appraisal_ratio=round(int(row["sold_price"]) / appraisal, 4),
                similarity=round(1.0 / (1.0 + d), 4),
            )
        )
    return out


def estimate(
    case: PropertyCase,
    cfg: Config,
    frame: Any,
    as_of: date,
    *,
    extra_caveats: Optional[Sequence[str]] = None,
) -> PriceBandEstimate:
    """세그먼트 낙찰가율 중앙값 + IQR 밴드. 표본 0건이면 zero_data 로 위임."""
    caveats: list[str] = list(extra_caveats or [])

    segment, scope = select_segment(frame, case, as_of)
    ratios, usable = _ratio_series(segment)
    sample_size = int(len(ratios))

    if sample_size == 0:
        caveats.append(
            f"기준일 {as_of.isoformat()} 이전 {LOOKBACK_MONTHS}개월 안에서 "
            "쓸 수 있는 낙찰 사례를 찾지 못해 사건 구조 기반 사전 구간으로 대체했습니다."
        )
        return zero_data.estimate(case, cfg, extra_caveats=caveats)

    unit = cfg.bid_round_unit
    appraisal = case.appraisal_price
    q1 = float(ratios.quantile(0.25))
    q2 = float(ratios.quantile(0.50))
    q3 = float(ratios.quantile(0.75))
    iqr = max(q3 - q1, 0.0)

    r10 = max(q1 - IQR_TO_TAIL * iqr, 0.0)
    r90 = q3 + IQR_TO_TAIL * iqr

    p10 = floor_unit(r10 * appraisal, unit)
    p50 = floor_unit(q2 * appraisal, unit)
    p90 = ceil_unit(r90 * appraisal, unit)

    # 현재 회차 최저매각가격 미만은 응찰이 불가능하므로 하한을 끌어올린다.
    legal_floor = floor_unit(case.min_bid_price, unit)
    if p10 < legal_floor:
        p10 = legal_floor
        caveats.append(
            f"통계 하한이 현재 회차 최저매각가격({legal_floor:,}원)보다 낮아 "
            "응찰 가능한 하한으로 끌어올렸습니다. (참고 정보)"
        )
    p50 = max(p50, p10)
    p90 = max(p90, p50)

    ratio = p50 / appraisal if appraisal > 0 else 0.0

    caveats += [
        f"표본 수 {sample_size}건 — 세그먼트: {scope}, 기준일 {as_of.isoformat()}.",
        f"세그먼트 낙찰가율 중앙값 {q2:.4f}, 사분위 {q1:.4f}~{q3:.4f} 를 감정가에 곱해 만든 참고 구간입니다.",
        f"p10/p90 은 관측 분위수가 아니라 IQR 에 계수 {IQR_TO_TAIL} 를 곱해 외삽한 값입니다. (참고 정보)",
        "실제 매각가는 이 구간을 벗어날 수 있습니다. 경쟁이 몰린 물건은 p90 을 넘겨 매각되기도 합니다. (참고 정보)",
        "입찰 판단의 참고 정보이며 권장 입찰가가 아닙니다.",
    ]
    if sample_size < MIN_SEGMENT_ROWS:
        caveats.append(
            f"표본 수 {sample_size}건으로 통계적 안정성이 낮습니다. 참고용으로만 보십시오."
        )

    return PriceBandEstimate(
        case_id=case.case_id,
        p10=p10,
        p50=p50,
        p90=p90,
        appraisal_ratio_p50=round(ratio, 4),
        method="HEURISTIC",
        comparables=select_comparables(usable, case),
        model_version=MODEL_VERSION,
        caveats=caveats,
        sample_size=sample_size,
    )
