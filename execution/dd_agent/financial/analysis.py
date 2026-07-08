"""DD metrics + anomaly flagging on top of parsed financial statements.

Purely deterministic — no LLM calls. Thresholds come from config.yaml `materiality`.
Periods are ordered by the trailing year number in their label (e.g. "FY24" -> 24,
"FY2024" -> 2024) when parseable; otherwise the order they were extracted in is kept, on
the assumption that a single source document lists periods chronologically.
"""
from __future__ import annotations

import logging
import re

from dd_agent.config import MaterialityConfig
from dd_agent.schemas.financial import FinancialAnomaly, FinancialSummary, PeriodFigure

logger = logging.getLogger("dd_agent.financial.analysis")

_YEAR_RE = re.compile(r"(\d{2,4})\s*$")


def _period_sort_key(period: PeriodFigure, fallback_index: int) -> tuple[int, int]:
    match = _YEAR_RE.search(period.period_label)
    if match:
        year = int(match.group(1))
        if year < 100:  # two-digit year, e.g. "FY24"
            year += 2000
        return (0, year)
    return (1, fallback_index)  # unparseable labels sort after parseable ones, in original order


def sort_periods(periods: list[PeriodFigure]) -> list[PeriodFigure]:
    """Order periods chronologically by trailing year in the label; used by both anomaly
    flagging here and by the report renderers, so both present periods in the same order."""
    ordered = sorted(enumerate(periods), key=lambda pair: _period_sort_key(pair[1], pair[0]))
    return [p for _, p in ordered]


def flag_financial_anomalies(summary: FinancialSummary, materiality: MaterialityConfig) -> FinancialSummary:
    periods = sort_periods(summary.periods)

    anomalies: list[FinancialAnomaly] = []

    for previous, current in zip(periods, periods[1:]):
        if previous.revenue and current.revenue is not None:
            variance_pct = (current.revenue - previous.revenue) / previous.revenue * 100
            if abs(variance_pct) > materiality.revenue_variance_pct:
                anomalies.append(
                    FinancialAnomaly(
                        description=(
                            f"Revenue {'grew' if variance_pct > 0 else 'declined'} "
                            f"{abs(variance_pct):.1f}% from {previous.period_label} to "
                            f"{current.period_label} ({previous.revenue:,.0f} -> {current.revenue:,.0f}), "
                            f"versus a {materiality.revenue_variance_pct:.0f}% materiality threshold."
                        ),
                        severity="Medium" if abs(variance_pct) < materiality.revenue_variance_pct * 2 else "High",
                        period_label=current.period_label,
                        source_file=current.source_file,
                    )
                )

    for period in periods:
        if period.ebitda_reported and period.ebitda_adjusted is not None:
            adjustment_pct = (
                abs(period.ebitda_adjusted - period.ebitda_reported) / abs(period.ebitda_reported) * 100
            )
            if adjustment_pct > materiality.ebitda_adjustment_pct:
                items = ", ".join(period.ebitda_adjustments) if period.ebitda_adjustments else "unspecified items"
                anomalies.append(
                    FinancialAnomaly(
                        description=(
                            f"EBITDA normalization adjustments in {period.period_label} "
                            f"({items}) shift reported EBITDA of {period.ebitda_reported:,.0f} to an adjusted "
                            f"{period.ebitda_adjusted:,.0f}, a {adjustment_pct:.1f}% change versus a "
                            f"{materiality.ebitda_adjustment_pct:.0f}% materiality threshold."
                        ),
                        severity="Medium",
                        period_label=period.period_label,
                        source_file=period.source_file,
                    )
                )

        if period.related_party_revenue and period.revenue:
            related_pct = period.related_party_revenue / period.revenue * 100
            if related_pct > materiality.related_party_revenue_pct:
                anomalies.append(
                    FinancialAnomaly(
                        description=(
                            f"Related-party revenue in {period.period_label} is "
                            f"{period.related_party_revenue:,.0f}, {related_pct:.1f}% of total revenue "
                            f"({period.revenue:,.0f}) — versus a {materiality.related_party_revenue_pct:.0f}% "
                            f"materiality threshold."
                        ),
                        severity="High" if related_pct > materiality.related_party_revenue_pct * 2 else "Medium",
                        period_label=period.period_label,
                        source_file=period.source_file,
                    )
                )

    logger.info("Flagged %d financial anomal%s", len(anomalies), "y" if len(anomalies) == 1 else "ies")
    summary.anomalies = anomalies
    return summary
