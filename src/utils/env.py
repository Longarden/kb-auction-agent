"""환경변수 로드. API 키는 .env 에만 두고 커밋하지 않는다(process.md §8).

수집기(T1)와 LLM 프로바이더에서만 쓴다. 데모와 테스트는 키 없이 동작한다.
"""
from __future__ import annotations

import os
from typing import Optional

from src.utils.io import REPO_ROOT

_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv 는 코어 의존
        _LOADED = True
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
    _LOADED = True


def optional(name: str, default: Optional[str] = None) -> Optional[str]:
    _ensure_loaded()
    return os.getenv(name, default)


def require(name: str) -> str:
    _ensure_loaded()
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} 가 설정되지 않았습니다. 레포 루트 .env 에 추가하세요. "
            f"(수집기 전용 — 데모와 테스트에는 필요하지 않습니다.)"
        )
    return value
