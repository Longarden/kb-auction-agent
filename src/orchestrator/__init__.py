"""ORCH 오케스트레이터 공개 진입점 (process.md §3.5).

여기서는 가벼운 모듈만 import 한다. 에이전트 모듈(lightgbm/pandas/pdfplumber 등)은
`run()` 내부에서 지연 import 되므로 `import src.orchestrator` 는 무겁지 않다.
"""
from src.orchestrator.run import run, run_detailed
from src.orchestrator.verdict import RED_REASON_PREFIX, decide, red_reason

__all__ = ["run", "run_detailed", "decide", "red_reason", "RED_REASON_PREFIX"]
