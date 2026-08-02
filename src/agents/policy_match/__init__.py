"""A5 정책·상품 매칭. 자격 판정은 100% 룰이며 LLM 은 summary_note 서술에만 허용된다."""
from __future__ import annotations

from src.agents.policy_match.run import run

__all__ = ["run"]
