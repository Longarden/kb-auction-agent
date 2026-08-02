"""A2 LightGBM quantile 학습 스크립트.

    python -m src.agents.price_band.train

분할은 **시간 기준**이다(sold_date 오름차순, 최근 20% 검증). 랜덤 split 은
미래 정보를 학습에 넣는 누수이므로 쓰지 않는다.

행 수가 MIN_TRAIN_ROWS 미만이면 예외가 아니라 로그를 남기고 종료한다.
오늘 이 레포에서 이 스크립트를 돌리면 표본 0건이라 바로 이 경로로 끝난다.

lightgbm/pandas 는 함수 안에서만 import 한다.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src.agents.price_band import data, features
from src.utils.io import resolve, write_json

log = logging.getLogger(__name__)

MODEL_DIR = "data/models"
MODEL_VERSION = "lgbm-quantile-v1"
MIN_TRAIN_ROWS = 300
VALIDATION_FRACTION = 0.20

QUANTILES: dict[str, float] = {"p10": 0.1, "p50": 0.5, "p90": 0.9}

LGB_PARAMS: dict[str, Any] = {
    "objective": "quantile",
    "metric": "quantile",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
}
NUM_BOOST_ROUND = 400
EARLY_STOPPING_ROUNDS = 40


def model_path(quantile_key: str, version: str = MODEL_VERSION) -> Path:
    return resolve(f"{MODEL_DIR}/price_band_{version}_{quantile_key}.txt")


def meta_path(version: str = MODEL_VERSION) -> Path:
    return resolve(f"{MODEL_DIR}/price_band_{version}_meta.json")


def model_available(version: str = MODEL_VERSION) -> bool:
    """3개 분위 모델과 메타가 모두 있어야 MODEL 경로를 탄다."""
    return meta_path(version).exists() and all(
        model_path(key, version).exists() for key in QUANTILES
    )


def load_meta(version: str = MODEL_VERSION) -> dict[str, Any]:
    from src.utils.io import read_json

    return read_json(meta_path(version))


def _time_split(frame: Any) -> tuple[Any, Any]:
    ordered = frame.sort_values("sold_date", kind="mergesort")
    cut = int(len(ordered) * (1.0 - VALIDATION_FRACTION))
    cut = max(1, min(cut, len(ordered) - 1))
    return ordered.iloc[:cut], ordered.iloc[cut:]


def train(
    cases_path: str = data.AUCTION_CASES_PATH,
    trades_path: str = data.TRADES_PATH,
    version: str = MODEL_VERSION,
    as_of: Optional[date] = None,
) -> int:
    """학습 성공 시 0, 데이터 부족으로 건너뛰면 2를 돌려준다(예외를 던지지 않는다)."""
    frame = data.load_auction_cases(cases_path)
    rows = len(frame)
    if rows < MIN_TRAIN_ROWS:
        log.warning(
            "학습 데이터 %d건 < 최소 %d건 — 모델을 학습하지 않고 종료합니다. "
            "A2 는 HEURISTIC/사전 경로로 계속 동작합니다.",
            rows,
            MIN_TRAIN_ROWS,
        )
        return 2

    usable = frame[(frame["appraisal_price"] > 0) & (frame["sold_price"] > 0)]
    usable = usable[usable["sold_date"].notna()]
    if len(usable) < MIN_TRAIN_ROWS:
        log.warning(
            "감정가·낙찰가·낙찰일이 모두 유효한 행이 %d건뿐입니다(최소 %d건). 학습을 건너뜁니다.",
            len(usable),
            MIN_TRAIN_ROWS,
        )
        return 2

    train_frame, valid_frame = _time_split(usable)
    split_date = train_frame["sold_date"].max()
    effective_as_of = as_of or split_date.date()

    region_freq = features.region_frequency(train_frame)
    trades = data.load_trades(trades_path)
    trade_median = features.trade_median_unit_price(trades, effective_as_of)

    x_train = features.build_features(
        train_frame, region_freq=region_freq, trade_median=trade_median
    )
    x_valid = features.build_features(
        valid_frame, region_freq=region_freq, trade_median=trade_median
    )
    features.assert_no_forbidden(x_train.columns)
    features.assert_no_forbidden(x_valid.columns)

    y_train = features.build_target(train_frame)
    y_valid = features.build_target(valid_frame)

    import lightgbm as lgb

    resolve(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    best_iterations: dict[str, int] = {}
    for key, alpha in QUANTILES.items():
        params = dict(LGB_PARAMS, alpha=alpha)
        booster = lgb.train(
            params,
            lgb.Dataset(x_train, label=y_train),
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[lgb.Dataset(x_valid, label=y_valid)],
            callbacks=[
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(0),
            ],
        )
        booster.save_model(str(model_path(key, version)))
        best_iterations[key] = int(booster.best_iteration or NUM_BOOST_ROUND)
        log.info("%s(alpha=%.2f) 학습 완료, best_iteration=%d", key, alpha, best_iterations[key])

    write_json(
        {
            "version": version,
            "trained_rows": int(len(train_frame)),
            "validation_rows": int(len(valid_frame)),
            "total_rows": int(len(usable)),
            "split": "time-based",
            "split_date": str(split_date.date()),
            "as_of": effective_as_of.isoformat(),
            "feature_columns": list(features.FEATURE_COLUMNS),
            "forbidden_features": sorted(features.FORBIDDEN_FEATURES),
            "region_freq": region_freq,
            "trade_median_unit_price": trade_median,
            "best_iterations": best_iterations,
        },
        str(meta_path(version)),
    )
    log.info("모델 저장 완료: %s", meta_path(version))
    return 0


def predict_ratios(
    case: Any, meta: dict[str, Any], version: str = MODEL_VERSION
) -> dict[str, float]:
    """단건 추론 → {'p10': ratio, 'p50': ratio, 'p90': ratio}."""
    import lightgbm as lgb

    frame = features.case_to_frame(case)
    x = features.build_features(
        frame,
        region_freq=meta.get("region_freq", {}),
        trade_median=meta.get("trade_median_unit_price", {}),
    )
    out: dict[str, float] = {}
    for key in QUANTILES:
        booster = lgb.Booster(model_file=str(model_path(key, version)))
        out[key] = float(booster.predict(x)[0])
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="A2 낙찰가 밴드 모델 학습")
    parser.add_argument("--cases", default=data.AUCTION_CASES_PATH)
    parser.add_argument("--trades", default=data.TRADES_PATH)
    parser.add_argument("--version", default=MODEL_VERSION)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return train(cases_path=args.cases, trades_path=args.trades, version=args.version)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
