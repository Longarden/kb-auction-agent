"""A3 자금조달 상한 모듈. LLM 을 쓰지 않는 순수 규칙 엔진.

주의: 이 패키지는 src.agents.tco 를 import 하지 않는다.
비용함수는 run(cost_fn=...) 로 주입한다.
"""
from __future__ import annotations

from src.agents.financing.run import run

__all__ = ["run"]
