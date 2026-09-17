"""Opportunity assembly and scoring pipeline."""

from src.opportunities.aggregation import (
    TickerOpportunityAggregate,
    TimeframeDetail,
    TriggeredSignalGroup,
    aggregate_opportunities_by_ticker,
    get_latest_active_opportunities,
)
from src.opportunities.assembly import OpportunityAssembler
from src.opportunities.scoring import OpportunityScorer

__all__ = [
    "OpportunityAssembler",
    "OpportunityScorer",
    "TickerOpportunityAggregate",
    "TimeframeDetail",
    "TriggeredSignalGroup",
    "aggregate_opportunities_by_ticker",
    "get_latest_active_opportunities",
]
