"""A2 낙찰가 밴드. LLM import 금지 영역(process.md P2).

pandas/lightgbm 은 이 패키지를 import 하는 것만으로 로드되지 않는다
(전부 함수 내부 지연 import).
"""
from __future__ import annotations

from src.agents.price_band.run import run

__all__ = ["run"]
