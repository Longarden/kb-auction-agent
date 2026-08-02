"""오케스트레이터 (process.md §6 T7).

실행 순서와 실패 정책만 담당한다. 자체 수치 계산은 하지 않는다(§3.2).

에이전트 모듈 import 는 전부 `run()` 안에서 지연 실행한다. 이유:
`import src.orchestrator` 만으로 lightgbm/pandas/pdfplumber 가 끌려오면
Streamlit 기동과 테스트 수집이 눈에 띄게 느려지고, `src/` 가 UI 라이브러리에
간접 의존하는지 정적으로 판별하기 어려워지기 때문이다.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import partial
from typing import Any, Optional

from src.orchestrator import report as report_mod
from src.orchestrator.verdict import RED_REASON_PREFIX, decide, red_reason
from src.schemas.core import (
    FinalVerdict,
    PolicyMatchResult,
    PriceBandEstimate,
    PropertyCase,
    Signal,
    UserProfile,
)
from src.utils import guard
from src.utils.config import Config

logger = logging.getLogger(__name__)

BAND_MISSING_WARNING = "예상 낙찰가 구간을 추정하지 못했습니다(참고 정보 없음)."
BAND_PRIOR_WARNING = (
    "예상 낙찰가 구간은 관측 0건에서 유도한 참고 구간입니다. 실제 낙찰 결과와 다를 수 있습니다."
)
POLICY_MISSING_WARNING = (
    "정책·상품 매칭에 실패해 추천 금융 상품을 표시하지 못했습니다. "
    "금융기관 창구에서 직접 확인하세요."
)
ZERO_CEILING_NOTE = (
    "감당 가능 상한이 0원으로 산출되어, 총비용 브레이크다운은 "
    "법정 최저매각가격을 가정 입찰가로 두고 계산했습니다."
)
CONFIG_UNVERIFIED_WARNING = (
    "규제 수치가 최신 확인 전 예시값입니다(설정 verified=false). "
    "실제 입찰 전 LTV·DSR·취득세 규정을 재확인하세요."
)

_MAX_BAND_CAVEATS = 3


def run(
    case: PropertyCase,
    user: UserProfile,
    cfg: Config,
    as_of: Optional[date] = None,
) -> FinalVerdict:
    """A1~A5 를 순서대로 실행하고 최종 판정을 만든다.

    `as_of` 는 A2 의 기준일이다. 오케스트레이터는 오늘 날짜를 스스로 읽지 않는다
    (결정성 확보 — 호출자가 명시적으로 넘긴다).
    """
    return run_detailed(case, user, cfg, as_of)[0]


def run_detailed(
    case: PropertyCase,
    user: UserProfile,
    cfg: Config,
    as_of: Optional[date] = None,
) -> tuple[FinalVerdict, dict[str, Any]]:
    """`run()` 과 동일하지만 중간 산출물(A1~A5 리포트)을 함께 돌려준다.

    FinalVerdict 스키마는 동결되어 있어 중간 리포트를 담을 수 없다. UI 가
    차트를 그리려고 파이프라인을 두 번 도는 것을 막기 위한 부가 진입점이며,
    판정 로직은 `run()` 과 완전히 동일하다(같은 코드 경로).
    """
    # 지연 import: 여기서만 무거운 의존이 로드된다.
    from src.agents.financing import run as fin_run
    from src.agents.policy_match import run as policy_run
    from src.agents.price_band import run as band_run
    from src.agents.rights_analysis import run as rights_run
    from src.agents.tco import cost_function

    warnings: list[str] = []

    # ── A1 권리분석 — 실패하면 서비스 자체가 성립하지 않는다 ────────────
    rights = rights_run(case, cfg)
    warnings.extend(rights.warnings)

    # ── A2 낙찰가 밴드 — 참고 정보이므로 실패해도 계속 진행한다 ──────────
    band: Optional[PriceBandEstimate]
    try:
        band = band_run(case, cfg, as_of=as_of)
    except Exception:
        logger.warning("A2 price_band 실패 — 밴드 없이 진행", exc_info=True)
        band = None
        warnings.append(BAND_MISSING_WARNING)
    else:
        if band is not None:
            if band.sample_size == 0:
                warnings.append(BAND_PRIOR_WARNING)
            warnings.extend(_select_caveats(band))

    # ── A3 자금조달 상한 — 서비스의 1차 출력. 실패하면 중단한다 ──────────
    cost_fn = partial(cost_function, case=case, user=user, rights=rights, cfg=cfg)
    financing = fin_run(case, user, rights, cost_fn, cfg)

    # ── A4 최종 TCO ────────────────────────────────────────────────
    if financing.max_bid_price == 0:
        warnings.append(ZERO_CEILING_NOTE)
    tco_bid = max(financing.max_bid_price, case.min_bid_price)
    tco = cost_function(bid=tco_bid, case=case, user=user, rights=rights, cfg=cfg)

    # ── A5 정책·상품 매칭 — 실패해도 계속 진행한다 ────────────────────
    policy: Optional[PolicyMatchResult]
    try:
        policy = policy_run(case, user, financing, cfg)
    except Exception:
        logger.warning("A5 policy_match 실패 — 빈 상품 목록으로 진행", exc_info=True)
        policy = PolicyMatchResult(case_id=case.case_id)
        warnings.append(POLICY_MISSING_WARNING)

    if not cfg.verified:
        warnings.append(CONFIG_UNVERIFIED_WARNING)

    # ── 판정 ──────────────────────────────────────────────────────
    signal, recommended = decide(rights, band, financing, case.min_bid_price)
    reason = red_reason(rights, band, financing, case.min_bid_price)
    if signal == Signal.RED and reason:
        warnings.insert(0, RED_REASON_PREFIX + reason)

    key_numbers: dict[str, Any] = {
        "p50": band.p50 if band else None,
        "max_bid": financing.max_bid_price,
        "assumed_cost": rights.total_assumed_cost,
        "effective_price_at_max": tco.effective_acquisition_price,
        "monthly_payment": financing.monthly_payment_at_max,
    }

    warnings = _dedupe(warnings)

    ctx = report_mod.build_context(
        case=case,
        user=user,
        rights=rights,
        band=band,
        financing=financing,
        tco=tco,
        policy=policy,
        signal=signal,
        recommended_max_bid=recommended,
        key_numbers=key_numbers,
        warnings=warnings,
        red_reason_text=reason if signal == Signal.RED else None,
        config_version=cfg.version,
        config_verified=cfg.verified,
    )
    markdown = report_mod.render(ctx)

    verdict = FinalVerdict(
        case_id=case.case_id,
        signal=signal,
        recommended_max_bid=recommended,
        key_numbers=key_numbers,
        rationale=ctx["rationale"],
        warnings=warnings,
        report_markdown=markdown,
    )
    guard.assert_object_clean(verdict, where="FinalVerdict")

    detail: dict[str, Any] = {
        "rights": rights,
        "band": band,
        "financing": financing,
        "tco": tco,
        "policy": policy,
        "red_reason": reason,
    }
    return verdict, detail


def _select_caveats(band: PriceBandEstimate) -> list[str]:
    """A2 의 caveat 중 사용자에게 노출할 것만 골라 상위 몇 건으로 자른다.

    caveat 는 모델 내부 사정까지 담을 수 있어 전부 띄우면 경고가 희석된다.
    """
    picked: list[str] = []
    for c in band.caveats:
        text = (c or "").strip()
        if not text or text in picked:
            continue
        picked.append(text)
        if len(picked) >= _MAX_BAND_CAVEATS:
            break
    return picked


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        t = (x or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out
