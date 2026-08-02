"""수집기의 유일한 네트워크 접점.

fetch 와 parse 를 분리하는 이유: parse 를 순수 함수로 두면 테스트가 네트워크
없이 고정 XML 만으로 회귀 검증을 할 수 있다. 이 모듈만 requests 를 만진다.

process.md P7(수집기는 사이드이펙트 허용) 영역이지만, 그 사이드이펙트를
이 파일 한 장에 가둔다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Optional

from src.utils.errors import AgentError

log = logging.getLogger(__name__)

MODULE = "collectors._http"

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
BACKOFF_SECONDS = 1.5

USER_AGENT = "safebid-collector/0.1 (KB AI Challenge PoC)"


def get_text(
    url: str,
    params: Optional[Mapping[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    *,
    session: Any = None,
) -> str:
    """GET 후 응답 본문 문자열. 실패 시 지수 백오프로 재시도하고 마지막에 AgentError.

    공공데이터포털 응답은 Content-Type 이 부정확한 경우가 잦아 인코딩을
    UTF-8 로 강제한다(그러지 않으면 requests 가 ISO-8859-1 로 추정해 한글이 깨진다).
    """
    import requests

    http = session or requests
    last_error: Optional[Exception] = None

    for attempt in range(1, max(retries, 1) + 1):
        try:
            response = http.get(
                url,
                params=dict(params or {}),
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            if (response.encoding or "").lower() in {"iso-8859-1", "latin-1"}:
                response.encoding = "utf-8"
            return response.text
        except Exception as exc:  # noqa: BLE001 - 재시도 대상은 네트워크 전반
            last_error = exc
            log.warning("GET 실패(%d/%d) %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(BACKOFF_SECONDS * attempt)

    raise AgentError(
        module=MODULE,
        reason=f"{url} 요청이 {retries}회 모두 실패했습니다: {last_error}",
        recoverable=True,
    )
