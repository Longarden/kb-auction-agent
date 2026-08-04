"""A1 권리분석 진입점.

책임 분리:
  parser.py  원문 -> 구조(순수 함수)
  client.py  프로바이더 선택(규칙 기본, LLM 선택)
  schemas.py 프로바이더 출력 계약
  rules.py   구조 -> 판정(최종 판단은 항상 규칙)
  run.py     이 넷을 잇고, RawExtraction 한 곳에서만 수렴시킨다.

cfg 는 인자로 주입한다(P7). 전역 상태 없음, 입력 파일 읽기 외 부작용 없음.
"""
from __future__ import annotations

import logging

from src.agents.rights_analysis.parser import extract_rule, load_document
from src.agents.rights_analysis.rules import (
    build_assumed_right,
    build_tenant,
    determine_base_right,
    determine_eviction_difficulty,
    determine_risk_grade,
)
from src.agents.rights_analysis.schemas import RawExtraction, extraction_json_schema
from src.llm.client import (
    LLMUnavailable,
    RuleProvider,
    combine_documents,
    get_provider,
)
from src.schemas.core import PropertyCase, RightsAnalysisReport
from src.utils.config import Config
from src.utils.errors import AgentError
from src.utils.io import read_text

logger = logging.getLogger(__name__)

MODULE = "rights_analysis"
PROMPT_PATH = "src/agents/rights_analysis/prompts/extract.md"


def _dedup(items: list[str]) -> list[str]:
    """순서를 지키면서 중복만 제거한다(set 을 쓰면 경고 순서가 흔들린다)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def run(case: PropertyCase, cfg: Config) -> RightsAnalysisReport:
    """매각물건명세서(+등기부)를 읽어 권리분석 리포트를 만든다."""
    if case.documents.sale_spec_path is None:
        raise AgentError(
            MODULE,
            "매각물건명세서 없이는 권리분석을 진행할 수 없습니다.",
            recoverable=False,
        )

    sale_spec_text = load_document(case.documents.sale_spec_path)
    registry_text = (
        load_document(case.documents.registry_path) if case.documents.registry_path else ""
    )
    document_text = combine_documents(sale_spec_text, registry_text)

    warnings: list[str] = []
    if not registry_text:
        warnings.append(
            "등기사항전부증명서가 없어 말소기준권리를 매각물건명세서 기재에만 의존했습니다."
        )

    provider = get_provider(extractor=extract_rule)
    prompt = read_text(PROMPT_PATH)
    try:
        result = provider.extract(
            prompt=prompt,
            document_text=document_text,
            json_schema=extraction_json_schema(),
        )
    except LLMUnavailable as exc:
        logger.warning("LLM 추출 실패 -> 규칙 프로바이더로 폴백합니다: %s", exc)
        warnings.append(
            "LLM 추출을 사용할 수 없어 규칙 기반 파서로 처리했습니다."
        )
        result = RuleProvider(extract_rule).extract(
            prompt=prompt,
            document_text=document_text,
            json_schema=extraction_json_schema(),
        )

    warnings.extend(result.warnings)
    # 프로바이더 출력의 유일한 수렴 지점. 이후 코드는 출처를 알지 못한다.
    extraction = RawExtraction.model_validate(result.data)
    for note in extraction.notes:
        logger.info("[%s] %s", case.case_id, note)

    base_right, base_warnings, base_evidence = determine_base_right(extraction)
    warnings.extend(base_warnings)
    for item in base_evidence:
        logger.info("[%s] %s", case.case_id, item)

    fallback_ratio = float(
        cfg.heuristics["tenant_deposit_fallback"]["jeonse_ratio_of_appraisal"]
    )
    unknown_ratio = float(cfg.heuristics["unknown_right_cost_ratio"])

    tenants = []
    for row in extraction.tenants:
        tenant, tenant_warnings = build_tenant(row, base_right, case, fallback_ratio)
        tenants.append(tenant)
        warnings.extend(tenant_warnings)

    assumed_rights = []
    for row in extraction.special_rights:
        right, right_warnings = build_assumed_right(row, case, unknown_ratio)
        if right is not None:
            assumed_rights.append(right)
            warnings.extend(right_warnings)

    eviction_difficulty = determine_eviction_difficulty(tenants)
    total_assumed_cost = sum(r.estimated_cost for r in assumed_rights) + sum(
        t.expected_assumed_deposit for t in tenants
    )
    risk_grade = determine_risk_grade(
        assumed_rights=assumed_rights,
        tenants=tenants,
        base_right=base_right,
        total_assumed_cost=total_assumed_cost,
        appraisal_price=case.appraisal_price,
        eviction_difficulty=eviction_difficulty,
    )

    if not cfg.verified:
        warnings.append(
            "규제·판정 기준값이 아직 검증되지 않았습니다(config verified=false)."
        )

    report = RightsAnalysisReport(
        case_id=case.case_id,
        base_right=base_right,
        assumed_rights=assumed_rights,
        tenants=tenants,
        total_assumed_cost=total_assumed_cost,
        eviction_difficulty=eviction_difficulty,
        risk_grade=risk_grade,
        warnings=_dedup(warnings),
    )
    # 합계 불변식: 리포트가 스스로와 모순되면 즉시 실패시킨다.
    recomputed = sum(r.estimated_cost for r in report.assumed_rights) + sum(
        t.expected_assumed_deposit for t in report.tenants
    )
    if recomputed != report.total_assumed_cost:
        raise AgentError(
            MODULE,
            f"총 인수금액 불일치: {report.total_assumed_cost} != {recomputed}",
            recoverable=False,
        )
    logger.info(
        "[%s] 권리분석 완료 grade=%s total=%d eviction=%s provider=%s",
        case.case_id,
        report.risk_grade.value,
        report.total_assumed_cost,
        report.eviction_difficulty,
        result.provider,
    )
    return report
