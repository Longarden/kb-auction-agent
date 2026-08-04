"""리포트 렌더링 (process.md §6 T7, P3/P6).

P3: 렌더 결과의 모든 수치는 Jinja 변수 치환으로만 들어간다.
    템플릿 산문에 숫자 리터럴을 쓰지 않는다.
P6: 면책 고지(DISCLAIMER)를 항상 포함한다.

렌더 결과 전체는 `src.utils.guard.assert_clean(..., check_band=True)` 를 통과해야 한다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src.schemas.core import (
    DISCLAIMER,
    Confidence,
    FinancingCeiling,
    PolicyMatchResult,
    PriceBandEstimate,
    PropertyCase,
    RightsAnalysisReport,
    Signal,
    TCOReport,
    UserProfile,
)
from src.utils import guard

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "report.md.j2"

_NO_VALUE = "정보 없음"

SIGNAL_LABEL: dict[str, str] = {
    Signal.GREEN.value: "GREEN — 감당 가능 범위 안에서 검토할 수 있습니다",
    Signal.YELLOW.value: "YELLOW — 조건부로만 검토하세요",
    Signal.RED.value: "RED — 이 물건은 입찰을 권하지 않습니다",
}

CONFIDENCE_LABEL: dict[str, str] = {
    Confidence.CONFIRMED.value: "확인됨(문서 명시)",
    Confidence.LIKELY.value: "추정(문서 교차 대조)",
    Confidence.UNCERTAIN.value: "판단 불가(보수적으로 인수 가정)",
}

EVICTION_LABEL: dict[str, str] = {
    "LOW": "낮음",
    "MID": "보통",
    "HIGH": "높음",
}

BINDING_LABEL: dict[str, str] = {
    "LTV": "담보인정비율(LTV)",
    "DSR": "총부채원리금상환비율(DSR)",
    "CASH": "동원 가능 현금",
    "PRODUCT_CAP": "상품 한도",
    "LOAN_FORBIDDEN": "규제상 대출 불가",
}

CEILING_NOTICE = (
    "이 금액은 감당 가능 상한이며 낙찰을 보장하지 않습니다. "
    "실제 응찰가는 본인의 자금 계획과 물건 상태를 직접 확인한 뒤 결정하세요."
)


# ── 포맷 필터 (P3: 숫자는 전부 이 필터를 거쳐 문자열이 된다) ──────────
def won(value: Optional[float]) -> str:
    """정수 원화. 1234567 -> '1,234,567원'."""
    if value is None:
        return _NO_VALUE
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return _NO_VALUE
    return f"{n:,}원"


def eok(value: Optional[float]) -> str:
    """억/만원 표기. 152700000 -> '1억 5,270만원'."""
    if value is None:
        return _NO_VALUE
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return _NO_VALUE
    sign = "-" if n < 0 else ""
    n = abs(n)
    units = n // 100_000_000
    man = (n % 100_000_000) // 10_000
    rest = n % 10_000
    parts: list[str] = []
    if units:
        parts.append(f"{units:,}억")
    if man:
        parts.append(f"{man:,}만원")
    # 억/만 단위가 있으면 만원 미만은 버린다(정확한 금액은 won 필터로 따로 표기한다).
    if not parts:
        parts.append(f"{rest:,}원")
    return sign + " ".join(parts)


def pct(value: Optional[float], digits: int = 1) -> str:
    """비율 -> 백분율. 0.7 -> '70.0%'."""
    if value is None:
        return _NO_VALUE
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError, OverflowError):
        return _NO_VALUE


def ymd(value: Any) -> str:
    if value is None:
        return _NO_VALUE
    return str(value)


def tri(value: Optional[bool], yes: str = "있음", no: str = "없음") -> str:
    if value is None:
        return "판단 불가"
    return yes if value else no


_ENV: Optional[Environment] = None


def _env() -> Environment:
    global _ENV
    if _ENV is None:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR), encoding="utf-8"),
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        env.filters["won"] = won
        env.filters["eok"] = eok
        env.filters["pct"] = pct
        env.filters["ymd"] = ymd
        env.filters["tri"] = tri
        _ENV = env
    return _ENV


# ── rationale ────────────────────────────────────────────────────
def _rule_rationale(ctx: dict[str, Any]) -> str:
    """키 없이도 동작하는 결정적 서술문. 수치는 전부 ctx 에서만 온다."""
    case: PropertyCase = ctx["case"]
    rights: RightsAnalysisReport = ctx["rights"]
    financing: FinancingCeiling = ctx["financing"]
    tco: TCOReport = ctx["tco"]
    signal: Signal = ctx["signal"]
    reason: Optional[str] = ctx["red_reason"]

    grade = rights.risk_grade.value
    binding = BINDING_LABEL.get(
        financing.binding_constraint.value, financing.binding_constraint.value
    )

    if signal == Signal.RED:
        head = (
            f"{case.case_id} 물건은 인수 리스크 {grade}등급으로 평가되었습니다. "
            f"{reason}"
            if reason
            else f"{case.case_id} 물건은 인수 리스크 {grade}등급으로 평가되었습니다."
        )
        tail = (
            f"참고로 회원님의 소득과 현금 여력으로 계산한 감당 가능 상한은 "
            f"{won(financing.max_bid_price)}이고, 이 물건의 인수 예상 금액은 "
            f"{won(rights.total_assumed_cost)}입니다. 인수 금액은 낙찰가와 별도로 "
            f"현금이 필요한 부분이므로, 다른 물건을 검토할 때도 같은 기준으로 확인하세요."
        )
        return f"{head}\n\n{tail}"

    head = (
        f"{case.case_id} 물건의 인수 리스크는 {grade}등급이며, 인수 예상 금액은 "
        f"{won(rights.total_assumed_cost)}입니다. 회원님의 소득과 현금 여력을 "
        f"{binding} 기준으로 계산한 결과, 감당 가능 상한은 "
        f"{won(financing.max_bid_price)}으로 산출되었습니다."
    )
    tail = (
        f"이 상한으로 낙찰받는다고 가정하면 세금과 명도비, 인수 금액까지 더한 "
        f"실질 취득가는 {won(tco.effective_acquisition_price)}이 되고, 대출 "
        f"{won(financing.max_loan)}에 대한 월 상환액은 "
        f"{won(financing.monthly_payment_at_max)} 수준입니다. 이 상한은 감당할 수 있는 "
        f"금액의 한계선이며, 실제 응찰가는 물건 상태를 직접 확인한 뒤 결정하세요."
    )
    return f"{head}\n\n{tail}"


def _llm_rationale(ctx: dict[str, Any]) -> Optional[str]:
    """`src/llm/client.py` 가 서술문 훅을 제공하면 그것을 쓴다.

    훅 규약: 모듈 수준 `render_rationale(context: dict) -> str`.
    모듈이 없거나 훅이 없거나 호출이 실패하면 None 을 돌려 규칙 기반으로 폴백한다.
    (P3 은 그대로 강제된다 — 결과 문자열도 guard 를 통과해야 한다.)
    """
    try:
        from src.llm import client as llm_client  # noqa: PLC0415  (지연 import)
    except Exception:
        return None
    hook = getattr(llm_client, "render_rationale", None)
    if not callable(hook):
        return None
    try:
        text = hook(ctx)
    except Exception:
        logger.warning("LLM rationale 훅 호출 실패 — 규칙 기반 서술문으로 대체", exc_info=True)
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


def build_rationale(ctx: dict[str, Any]) -> str:
    text = _llm_rationale(ctx) or _rule_rationale(ctx)
    # 서술문 단독으로도 금지 표현 검사를 통과해야 한다.
    return guard.assert_clean(text, where="rationale", check_band=False)


# ── 컨텍스트 + 렌더 ───────────────────────────────────────────────
def build_context(
    *,
    case: PropertyCase,
    user: UserProfile,
    rights: RightsAnalysisReport,
    band: Optional[PriceBandEstimate],
    financing: FinancingCeiling,
    tco: TCOReport,
    policy: Optional[PolicyMatchResult],
    signal: Signal,
    recommended_max_bid: Optional[int],
    key_numbers: dict[str, Any],
    warnings: list[str],
    red_reason_text: Optional[str],
    config_version: str,
    config_verified: bool,
) -> dict[str, Any]:
    sample_size = getattr(band, "sample_size", None) if band is not None else None
    ctx: dict[str, Any] = {
        "case": case,
        "user": user,
        "rights": rights,
        "band": band,
        "financing": financing,
        "tco": tco,
        "policy": policy,
        "products": list(policy.products) if policy is not None else [],
        "signal": signal,
        "signal_label": SIGNAL_LABEL.get(signal.value, signal.value),
        "recommended_max_bid": recommended_max_bid,
        "key_numbers": key_numbers,
        "warnings": warnings,
        "warnings_need_band_note": _needs_band_note(warnings),
        "red_reason": red_reason_text,
        "ceiling_notice": CEILING_NOTICE,
        "risk_grade": rights.risk_grade.value,
        "eviction_label": EVICTION_LABEL.get(
            rights.eviction_difficulty, rights.eviction_difficulty
        ),
        "binding_label": BINDING_LABEL.get(
            financing.binding_constraint.value, financing.binding_constraint.value
        ),
        "confidence_label": CONFIDENCE_LABEL,
        "band_is_prior": sample_size == 0,
        "band_sample_size": sample_size,
        "band_caveats": [c.strip() for c in band.caveats if (c or "").strip()]
        if band is not None
        else [],
        "config_version": config_version,
        "config_verified": config_verified,
        "disclaimer": DISCLAIMER,
    }
    ctx["rationale"] = build_rationale(ctx)
    return ctx


def _needs_band_note(warnings: list[str]) -> bool:
    """경고 목록이 밴드를 언급하면, 같은 블록에 '참고 정보' 수식어를 붙여야 한다.

    guard 와 같은 정규식을 재사용해 판정 기준을 하나로 유지한다.
    """
    return any(guard.BAND_MENTION.search(guard.normalize(w)) for w in warnings)


def render(ctx: dict[str, Any]) -> str:
    markdown = _env().get_template(TEMPLATE_NAME).render(**ctx)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    return guard.assert_clean(markdown, where="report_markdown", check_band=True)
