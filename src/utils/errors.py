"""공통 예외. 모든 에이전트는 이 예외로만 실패를 보고한다(process.md §3.5)."""
from __future__ import annotations


class AgentError(Exception):
    """에이전트 실행 실패.

    recoverable=False 이면 오케스트레이터가 파이프라인 전체를 중단한다.
    recoverable=True 이면 해당 모듈만 건너뛰고 경고를 남긴 채 계속한다.
    """

    def __init__(self, module: str, reason: str, recoverable: bool) -> None:
        self.module = module
        self.reason = reason
        self.recoverable = recoverable
        super().__init__(f"[{module}] {reason} (recoverable={recoverable})")
