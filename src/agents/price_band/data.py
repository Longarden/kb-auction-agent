"""A2 학습·통계용 데이터 로더.

파일이 없어도 **컬럼과 dtype 이 동일한 빈 프레임**을 돌려준다. 하류 통계
코드가 "파일 있음/없음" 분기를 두지 않아도 되게 하려는 것이다(분기가 생기면
표본 0건 경로만 테스트되지 않은 채로 남는다).

pandas 는 함수 안에서만 import 한다. `import src.orchestrator` 가 pandas 를
sys.modules 에 끌고 들어오면 안 된다(P2 인접 제약).
"""
from __future__ import annotations

from typing import Any

AUCTION_CASES_PATH = "data/processed/auction_cases.parquet"
TRADES_PATH = "data/processed/trades.parquet"

# 컬럼 → dtype. 빈 프레임과 채워진 프레임이 반드시 같아야 한다.
AUCTION_CASE_DTYPES: dict[str, str] = {
    "case_id": "object",
    "region_code": "object",
    "property_type": "object",
    "appraisal_price": "int64",
    "min_bid_price": "int64",
    "failed_count": "int64",
    "sold_price": "int64",
    "sold_date": "datetime64[ns]",
    "building_area_m2": "float64",
}

TRADE_DTYPES: dict[str, str] = {
    "region_code": "object",
    "deal_amount": "int64",
    "area_m2": "float64",
    "deal_date": "datetime64[ns]",
    "build_year": "float64",
    "floor": "float64",
}

AUCTION_CASE_COLUMNS: tuple[str, ...] = tuple(AUCTION_CASE_DTYPES)
TRADE_COLUMNS: tuple[str, ...] = tuple(TRADE_DTYPES)


def empty_frame(dtypes: dict[str, str]) -> Any:
    """스키마만 있고 행이 0인 DataFrame."""
    import pandas as pd

    return pd.DataFrame({col: pd.Series(dtype=dt) for col, dt in dtypes.items()})


def coerce(frame: Any, dtypes: dict[str, str]) -> Any:
    """누락 컬럼을 채우고 dtype 을 캐논 형태로 맞춘다."""
    import pandas as pd

    out = frame.copy()
    for col, dt in dtypes.items():
        if col not in out.columns:
            out[col] = pd.Series([pd.NA] * len(out), dtype="object")
        if dt == "datetime64[ns]":
            out[col] = pd.to_datetime(out[col], errors="coerce")
        elif dt == "int64":
            out[col] = (
                pd.to_numeric(out[col], errors="coerce").fillna(0).astype("int64")
            )
        elif dt == "float64":
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
        else:
            out[col] = out[col].astype("object")
    return out[list(dtypes)]


def _load(path: str, dtypes: dict[str, str]) -> Any:
    import pandas as pd

    from src.utils.io import resolve

    target = resolve(path)
    if not target.exists():
        return empty_frame(dtypes)
    frame = pd.read_parquet(target)
    if len(frame) == 0:
        return empty_frame(dtypes)
    return coerce(frame, dtypes)


def load_auction_cases(path: str = AUCTION_CASES_PATH) -> Any:
    """낙찰 사례. 파일이 없으면 스키마만 같은 빈 프레임."""
    return _load(path, AUCTION_CASE_DTYPES)


def load_trades(path: str = TRADES_PATH) -> Any:
    """실거래가. 파일이 없으면 스키마만 같은 빈 프레임."""
    return _load(path, TRADE_DTYPES)


def write_frame(frame: Any, path: str, dtypes: dict[str, str]) -> Any:
    """수집기 산출물 저장. 저장 시점에 dtype 을 고정한다."""
    from src.utils.io import resolve

    target = resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    coerce(frame, dtypes).to_parquet(target, index=False)
    return target
