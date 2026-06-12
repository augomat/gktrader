"""Tests for weekly report generation and grouping."""

from __future__ import annotations

from datetime import UTC, datetime

from gktrader.domain.enums import AlertLevel, Direction
from gktrader.reporting.weekly import (
    IEX_PARTIAL_LABEL,
    GroupedSection,
    WeeklyReportRow,
    build_weekly_report,
    group_performance_rows,
)


class TestWeeklyReportRow:
    """WeeklyReportRow dataclass."""

    def test_default_label(self) -> None:
        row = WeeklyReportRow(
            source_name="white_house",
            event_type="government_funding",
            direction=Direction.BULLISH,
            alert_level=AlertLevel.TRADEABLE,
            ticker="RGTI",
            notional_eur=1000.0,
        )
        assert row.label == IEX_PARTIAL_LABEL

    def test_minimal_row(self) -> None:
        row = WeeklyReportRow(
            source_name="white_house",
            event_type="government_funding",
            direction="bullish",
            alert_level="TRADEABLE",
            ticker="RGTI",
            notional_eur=1000.0,
        )
        assert row.return_pct is None
        assert row.max_drawdown_pct is None
        assert row.max_runup_pct is None
        assert row.missing_data is False


class TestGroupPerformanceRows:
    """Grouping performance rows by (source, event_type, direction, alert_level)."""

    def test_single_row(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
            ),
        ]
        sections = group_performance_rows(rows)
        assert len(sections) == 1
        assert sections[0].count == 1
        assert sections[0].total_notional == 1000.0
        assert sections[0].avg_return_pct == 5.0

    def test_multiple_groups(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
            ),
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.REVIEW,
                ticker="QBTS",
                notional_eur=500.0,
            ),
            WeeklyReportRow(
                source_name="sec",
                event_type="regulatory_headwind",
                direction=Direction.BEARISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="MU",
                notional_eur=1000.0,
            ),
        ]
        sections = group_performance_rows(rows)
        assert len(sections) == 3

    def test_grouping_by_all_four_keys(self) -> None:
        """Rows with same source, event_type, direction, alert_level are grouped."""
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
            ),
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="QBTS",
                notional_eur=1000.0,
                return_pct=3.0,
            ),
        ]
        sections = group_performance_rows(rows)
        assert len(sections) == 1
        assert sections[0].count == 2
        assert sections[0].total_notional == 2000.0
        assert sections[0].avg_return_pct == 4.0

    def test_empty_rows(self) -> None:
        sections = group_performance_rows([])
        assert sections == []

    def test_avg_return_none_when_no_returns(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
            ),
        ]
        sections = group_performance_rows(rows)
        assert sections[0].avg_return_pct is None


class TestBuildWeeklyReport:
    """Weekly report payload generation."""

    def test_basic_report_structure(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
            ),
        ]
        report = build_weekly_report(rows)
        assert "generated_at" in report
        assert "summary" in report
        assert "total_trades" in report
        assert "total_notional_eur" in report
        assert "sections" in report
        assert report["total_trades"] == 1
        assert report["total_notional_eur"] == 1000.0

    def test_label_in_report(self) -> None:
        """Report must clearly label IEX-derived results."""
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
            ),
        ]
        report = build_weekly_report(rows)
        assert report["label"] == IEX_PARTIAL_LABEL
        assert IEX_PARTIAL_LABEL in report["summary"]

    def test_summary_contains_grouped_sections(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
            ),
            WeeklyReportRow(
                source_name="sec",
                event_type="regulatory_headwind",
                direction=Direction.BEARISH,
                alert_level=AlertLevel.REVIEW,
                ticker="MU",
                notional_eur=500.0,
                return_pct=-2.0,
            ),
        ]
        report = build_weekly_report(rows)
        assert report["total_trades"] == 2
        assert report["total_notional_eur"] == 1500.0
        assert len(report["sections"]) == 2

    def test_section_rows_contain_all_fields(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
                max_drawdown_pct=-2.0,
                max_runup_pct=7.0,
                missing_data=False,
            ),
        ]
        report = build_weekly_report(rows)
        section = report["sections"][0]
        row = section["rows"][0]
        assert row["source_name"] == "white_house"
        assert row["event_type"] == "government_funding"
        assert row["direction"] == "bullish"
        assert row["alert_level"] == "TRADEABLE"
        assert row["ticker"] == "RGTI"
        assert row["notional_eur"] == 1000.0
        assert row["return_pct"] == 5.0
        assert row["max_drawdown_pct"] == -2.0
        assert row["max_runup_pct"] == 7.0
        assert row["missing_data"] is False
        assert row["label"] == IEX_PARTIAL_LABEL

    def test_missing_data_flag_in_summary(self) -> None:
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                missing_data=True,
            ),
        ]
        report = build_weekly_report(rows)
        assert "[MISSING DATA]" in report["summary"]

    def test_empty_report(self) -> None:
        report = build_weekly_report([])
        assert report["total_trades"] == 0
        assert report["total_notional_eur"] == 0.0
        assert report["sections"] == []

    def test_generated_at_custom(self) -> None:
        dt = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
            ),
        ]
        report = build_weekly_report(rows, generated_at=dt)
        assert dt.isoformat() in report["generated_at"]