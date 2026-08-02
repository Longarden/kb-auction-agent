"""A2 사전(prior) 경로 — 관측 표본이 0건일 때의 기본 경로.

이 모듈이 만드는 구간은 **통계 추정치가 아니다**. 낙찰 사례를 한 건도
수집하지 못한 상태(인증키 미발급)에서 process.md §T3 의 HEURISTIC 폴백
("최근 12개월 낙찰가율 중앙값")을 그대로 쓰면 median() 이 NaN 이 되어
int(NaN) 에서 죽는다. 그래서 표본 0건 구간은 통계가 아니라 **사건 구조에서
유도한 사전(prior)** 으로 만들고, sample_size=0 으로 '측정된 밴드'와 구별한다.

구간은 오직 두 개의 사실 위에 서 있다.
  · 하한 = 현재 회차 최저매각가격. 그 미만은 법적으로 응찰이 불가능하다.
  · 상한 = 직전 회차 최저매각가격. 그 가격에 팔리지 않았기 때문에 유찰됐다.

회차당 저감률은 config 값(discount_per_fail)이 아니라 **사건에서 역산한 값**을
우선한다. 법원·물건마다 저감률이 다르고(20%/30%), config 기본값 0.20 을
case_001(실제 30% 저감)에 적용하면 상한이 1.4억으로 잘못 나온다.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from src.schemas.core import PriceBandEstimate, PropertyCase
from src.utils.config import Config

MODEL_VERSION = "prior-structural-v1"

# 부동소수 오차 보정 폭. (112_000_000 / 0.7) 는 160_000_000.00000003 이 되어
# 보정 없이 ceil 하면 상한이 10만원 튄다.
_REL_TOL = 1e-9
_ABS_TOL = 1e-6


def _quantize(value: float, unit: int, *, up: bool) -> int:
    """value 를 unit 배수로 내림(up=False)/올림(up=True). 부동소수 오차는 흡수한다."""
    if unit <= 0:
        return int(round(value))
    q = value / unit
    nearest = round(q)
    if math.isclose(q, nearest, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
        q = float(nearest)
    else:
        q = math.ceil(q) if up else math.floor(q)
    return int(q) * unit


def floor_unit(value: float, unit: int) -> int:
    return _quantize(value, unit, up=False)


def ceil_unit(value: float, unit: int) -> int:
    return _quantize(value, unit, up=True)


def derive_previous_min_bid(case: PropertyCase, cfg: Config) -> tuple[int, str]:
    """(직전 회차 최저매각가격, 산출 근거 문자열).

    n 회 유찰이면 현재 최저가 L = A × step^n 이므로 step = (L/A)^(1/n) 이고
    직전 회차 최저가는 L / step 이다. 역산이 불가능한 경우에만 config 폴백.
    """
    unit = cfg.bid_round_unit
    appraisal = case.appraisal_price
    lowest = case.min_bid_price
    failed = case.failed_count

    if failed >= 1 and appraisal > 0 and 0 < lowest < appraisal:
        step = (lowest / appraisal) ** (1.0 / failed)
        upper = ceil_unit(lowest / step, unit)
        note = (
            f"회차당 잔존율 {step:.4f} 을 사건에서 역산했습니다"
            f"(최저매각가격/감정가의 {failed}제곱근). "
            f"config 기본 저감률({cfg.discount_per_fail:.2f})보다 사건 역산값을 우선합니다."
        )
        return upper, note

    if failed >= 1:
        remain = 1.0 - cfg.discount_per_fail
        if remain > 0:
            upper = ceil_unit(lowest / remain, unit)
        else:
            upper = max(lowest, appraisal)
        note = (
            f"사건에서 저감률을 역산할 수 없어 config 기본 저감률 "
            f"{cfg.discount_per_fail:.2f} 을 적용했습니다."
        )
        return upper, note

    upper = max(lowest, appraisal)
    note = "신건(유찰 0회)이라 직전 회차가 없습니다. 구조적 상한과 하한이 사실상 같습니다."
    return upper, note


def estimate(
    case: PropertyCase,
    cfg: Config,
    *,
    extra_caveats: Optional[Sequence[str]] = None,
) -> PriceBandEstimate:
    """표본 0건 구간. comparables 는 비운다(유사사례가 없으면 없는 척하지 않는다)."""
    unit = cfg.bid_round_unit
    upper, step_note = derive_previous_min_bid(case, cfg)

    p10 = floor_unit(case.min_bid_price, unit)
    p90 = max(p10, upper)
    p50 = floor_unit((p10 + p90) / 2, unit)
    p50 = min(max(p50, p10), p90)

    ratio = p50 / case.appraisal_price if case.appraisal_price > 0 else 0.0

    caveats: list[str] = list(extra_caveats or [])
    caveats += [
        "관측 0건 — 낙찰 사례를 아직 한 건도 수집하지 못해 통계 추정이 불가능합니다. 표본 수 0건.",
        "이 구간은 시장 관측치가 아니라 사건 구조값(감정가·최저매각가격·유찰횟수)"
        "만으로 만든 사전(prior)이며, 어디까지나 참고 정보입니다.",
        f"p10 {p10:,}원 = 현재 회차 최저매각가격. 이 금액 미만으로는 법적으로 응찰할 수 없습니다. (참고 정보)",
        f"p90 {p90:,}원 = 직전 회차 최저매각가격. 그 가격에 매각되지 않아 유찰된 회차의 가격입니다. (참고 정보)",
        f"p50 {p50:,}원 = p10 과 p90 의 산술 중앙값입니다. 분포에서 추정한 중앙값이 아닙니다. (참고 정보)",
        step_note,
        "실제 매각가는 이 구간을 벗어날 수 있습니다. 경쟁이 몰린 물건은 p90 을 넘겨 매각되기도 합니다. (참고 정보)",
        "입찰 판단의 참고 정보이며 권장 입찰가가 아닙니다.",
        "낙찰 사례 데이터가 수집되면 통계 기반 추정으로 자동 대체됩니다.",
    ]

    return PriceBandEstimate(
        case_id=case.case_id,
        p10=p10,
        p50=p50,
        p90=p90,
        appraisal_ratio_p50=round(ratio, 4),
        method="HEURISTIC",
        comparables=[],
        model_version=MODEL_VERSION,
        caveats=caveats,
        sample_size=0,
    )
