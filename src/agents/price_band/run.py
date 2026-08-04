"""A2 진입점. process.md §T3.

    run(case, cfg, as_of=None) -> PriceBandEstimate

경로 선택:
    모델 파일 존재 & 표본 >= 300  → MODEL
    표본 >= 1                     → HEURISTIC (세그먼트 중앙값 + IQR)
    표본 == 0                     → HEURISTIC (사건 구조 기반 사전, sample_size=0)

**정상 입력에 대해 이 모듈은 예외를 던지지 않는다.** process.md §T3 이
"파이프라인은 절대 이 모듈 때문에 중단되지 않는다"고 못박았으므로 각 단계는
실패 시 caveat 를 붙이고 한 단계 아래로 강등된다.

시계를 읽지 않는다. as_of 가 없으면 수집 데이터의 최신 낙찰일을 기준일로
쓴다(데이터가 없으면 애초에 사전 경로라 기준일이 필요 없다).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from src.agents.price_band import data, heuristic, zero_data
from src.agents.price_band.zero_data import ceil_unit, floor_unit
from src.schemas.core import PriceBandEstimate, PropertyCase
from src.utils.config import Config

log = logging.getLogger(__name__)


def _latest_sold_date(frame: Any) -> Optional[date]:
    if len(frame) == 0:
        return None
    latest = frame["sold_date"].max()
    try:
        return latest.date()
    except AttributeError:  # pragma: no cover - dtype 이 이미 date 인 경우
        return latest


def _enforce_order(estimate: PriceBandEstimate) -> PriceBandEstimate:
    """p10 <= p50 <= p90 보정. 모델이 분위 교차를 내면 정렬하고 caveat 를 남긴다."""
    ordered = sorted((estimate.p10, estimate.p50, estimate.p90))
    if ordered == [estimate.p10, estimate.p50, estimate.p90]:
        return estimate
    fixed = estimate.model_copy(
        update={
            "p10": ordered[0],
            "p50": ordered[1],
            "p90": ordered[2],
            "caveats": estimate.caveats
            + ["분위 예측이 교차해 p10 <= p50 <= p90 순서로 정렬 보정했습니다. (참고 정보)"],
        }
    )
    return fixed


def _model_estimate(
    case: PropertyCase, cfg: Config, frame: Any, as_of: date, caveats: list[str]
) -> PriceBandEstimate:
    # train 은 여기서만 import 한다. 패키지 __init__ 이 train 을 미리 로드하면
    # `python -m src.agents.price_band.train` 이 runpy 경고를 뱉는다.
    from src.agents.price_band import train

    meta = train.load_meta()
    ratios = train.predict_ratios(case, meta)

    unit = cfg.bid_round_unit
    appraisal = case.appraisal_price
    p10 = floor_unit(ratios["p10"] * appraisal, unit)
    p50 = floor_unit(ratios["p50"] * appraisal, unit)
    p90 = ceil_unit(ratios["p90"] * appraisal, unit)

    legal_floor = floor_unit(case.min_bid_price, unit)
    model_caveats = list(caveats)
    if p10 < legal_floor:
        p10 = legal_floor
        model_caveats.append(
            f"모델 하한이 현재 회차 최저매각가격({legal_floor:,}원)보다 낮아 "
            "응찰 가능한 하한으로 끌어올렸습니다. (참고 정보)"
        )

    sample_size = int(meta.get("total_rows", len(frame)))
    segment, _scope = heuristic.select_segment(frame, case, as_of)
    model_caveats += [
        f"LightGBM 분위회귀(alpha 0.1/0.5/0.9) 예측입니다. 학습 표본 {sample_size}건, "
        f"학습 분할 기준일 {meta.get('split_date', '미상')}.",
        "실제 매각가는 이 구간을 벗어날 수 있습니다. 경쟁이 몰린 물건은 p90 을 넘겨 매각되기도 합니다. (참고 정보)",
        "입찰 판단의 참고 정보이며 권장 입찰가가 아닙니다.",
    ]

    return PriceBandEstimate(
        case_id=case.case_id,
        p10=p10,
        p50=p50,
        p90=p90,
        appraisal_ratio_p50=round(p50 / appraisal, 4) if appraisal > 0 else 0.0,
        method="MODEL",
        comparables=heuristic.select_comparables(segment, case),
        model_version=meta.get("version", train.MODEL_VERSION),
        caveats=model_caveats,
        sample_size=sample_size,
    )


def run(
    case: PropertyCase, cfg: Config, as_of: Optional[date] = None
) -> PriceBandEstimate:
    from src.agents.price_band import train

    caveats: list[str] = []

    try:
        frame = data.load_auction_cases()
    except Exception as exc:  # noqa: BLE001 - 어떤 이유든 밴드는 나와야 한다
        log.warning("낙찰 사례 로드 실패: %s", exc)
        caveats.append(
            "수집 데이터 파일을 읽지 못해 사건 구조 기반 사전 구간으로 대체했습니다."
        )
        return zero_data.estimate(case, cfg, extra_caveats=caveats)

    rows = len(frame)
    if rows == 0:
        return zero_data.estimate(case, cfg, extra_caveats=caveats)

    effective_as_of = as_of or _latest_sold_date(frame) or case.sale_date
    if effective_as_of is None:
        caveats.append("기준일을 정할 수 없어 사건 구조 기반 사전 구간으로 대체했습니다.")
        return zero_data.estimate(case, cfg, extra_caveats=caveats)

    if rows >= train.MIN_TRAIN_ROWS and train.model_available():
        try:
            return _enforce_order(
                _model_estimate(case, cfg, frame, effective_as_of, caveats)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("모델 경로 실패, 세그먼트 통계로 강등: %s", exc)
            caveats.append(
                "모델 예측에 실패해 세그먼트 낙찰가율 중앙값 방식으로 대체했습니다."
            )

    try:
        return _enforce_order(
            heuristic.estimate(
                case, cfg, frame, effective_as_of, extra_caveats=caveats
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("세그먼트 통계 실패, 사건 구조 사전으로 강등: %s", exc)
        caveats.append(
            "세그먼트 통계 계산에 실패해 사건 구조 기반 사전 구간으로 대체했습니다."
        )
        return zero_data.estimate(case, cfg, extra_caveats=caveats)
