"""A4 진입점. 오케스트레이터는 이 run() 만 호출한다."""
from __future__ import annotations

import logging

from src.agents.tco.cost_function import cost_function
from src.schemas.core import PropertyCase, RightsAnalysisReport, TCOReport, UserProfile
from src.utils.config import Config

logger = logging.getLogger(__name__)


def run(
    case: PropertyCase,
    user: UserProfile,
    rights: RightsAnalysisReport,
    cfg: Config,
    bid: int,
) -> TCOReport:
    """주어진 입찰가에 대한 총소유비용 리포트를 만든다."""
    report = cost_function(bid, case, user, rights, cfg)
    logger.debug(
        "TCO case=%s bid=%s upfront=%s",
        case.case_id,
        report.bid_price,
        report.total_upfront_excl_bid,
    )
    return report
