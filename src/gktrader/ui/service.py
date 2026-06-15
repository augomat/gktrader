from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from gktrader.config.settings import get_settings
from gktrader.db.models import (
    Alert,
    EventEvidence,
    EventCompany,
    ExtractedEvent,
    MarketSnapshot,
    PaperTrade,
    PerformanceSnapshot,
    Position,
    PositionEvent,
    ProcessingRun,
    RawDocument,
    SignalEvent,
    SourceDefinition,
    SourcePollRun,
)
from gktrader.domain.enums import AlertLevel, Direction
from gktrader.marketdata.alpaca import AlpacaIEXProvider, IEX_LABEL
from gktrader.sources.truthsocial import resolve_truthsocial_source_url


_DEFAULT_CHART_RANGE = "1W"
_CHART_RANGES = {
    "1W": {"label": "1W", "days": 7, "timeframe": "5Min"},
    "1M": {"label": "1M", "days": 30, "timeframe": "1Hour"},
    "3M": {"label": "3M", "days": 90, "timeframe": "1Hour"},
    "6M": {"label": "6M", "days": 180, "timeframe": "1Hour"},
    "1Y": {"label": "1Y", "days": 365, "timeframe": "1Hour"},
}


def _age(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_chart_range(range_key: str | None) -> str:
    if not range_key:
        return _DEFAULT_CHART_RANGE
    normalized = range_key.upper()
    if normalized in _CHART_RANGES:
        return normalized
    return _DEFAULT_CHART_RANGE


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


class UIService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _resolve_document_source_url(self, doc: RawDocument) -> str:
        canonical_url = doc.canonical_url or ""
        if doc.source_name == "truthsocial":
            return resolve_truthsocial_source_url(canonical_url, doc.source_metadata)
        return canonical_url

    def _resolve_full_document_text(self, doc: RawDocument) -> str:
        text = doc.text or ""
        metadata = doc.source_metadata or {}

        stored_full_text = metadata.get("normalized_line") or metadata.get("raw_line")
        if isinstance(stored_full_text, str) and len(stored_full_text) > len(text):
            return stored_full_text
        return text

    def _chart_range_options(self, selected_range: str) -> list[dict[str, str | bool]]:
        return [
            {
                "key": key,
                "label": config["label"],
                "selected": key == selected_range,
            }
            for key, config in _CHART_RANGES.items()
        ]

    def _chart_window(self, focus_at: datetime | None, days: int) -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        duration = timedelta(days=days)
        focus = _as_utc(focus_at) or now
        half = duration / 2
        start = focus - half
        end = focus + half
        if end > now:
            shift = end - now
            start -= shift
            end = now
        if start >= end:
            start = end - duration
        return start, end

    def _fetch_chart_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            raise ValueError("Alpaca credentials are not configured")

        provider = AlpacaIEXProvider(
            api_key=settings.alpaca_api_key,
            api_secret=settings.alpaca_api_secret,
        )
        try:
            return provider.historical_bars(
                ticker,
                start=start,
                end=end,
                timeframe=timeframe,
            )
        finally:
            provider.close()

    def _get_raw_doc_from_signal(self, signal: SignalEvent) -> RawDocument | None:
        payload = signal.payload or {}
        extracted_ids = payload.get("extracted_event_ids", [])
        if not extracted_ids:
            return None
        extracted = self.db.get(ExtractedEvent, extracted_ids[0])
        if not extracted:
            return None
        return self.db.get(RawDocument, extracted.raw_document_id)

    def _signal_occurred_at(self, signal: SignalEvent, doc: RawDocument | None) -> datetime | None:
        if doc:
            return _as_utc(doc.published_at or doc.detected_at)
        return _as_utc(signal.created_at)

    def _stock_events_for_chart(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        focus_kind: str,
        focus_id: str,
    ) -> tuple[list[dict[str, Any]], int]:
        signals = self.db.scalars(
            select(SignalEvent)
            .where(SignalEvent.payload["ticker"].as_string() == ticker.upper())
            .order_by(SignalEvent.created_at)
        ).all()

        signal_ids = [signal.id for signal in signals]
        alerts_by_signal: dict[str, Alert] = {}
        if signal_ids:
            alerts = self.db.scalars(
                select(Alert)
                .where(Alert.signal_event_id.in_(signal_ids))
                .order_by(desc(Alert.created_at))
            ).all()
            for alert in alerts:
                alerts_by_signal.setdefault(alert.signal_event_id, alert)

        visible_events: list[dict[str, Any]] = []
        total_events = 0
        for signal in signals:
            doc = self._get_raw_doc_from_signal(signal)
            occurred_at = self._signal_occurred_at(signal, doc)
            if occurred_at is None:
                continue
            total_events += 1
            if occurred_at < start or occurred_at > end:
                continue

            linked_alert = alerts_by_signal.get(signal.id)
            is_focus = (
                (focus_kind == "signal" and signal.id == focus_id)
                or (focus_kind == "news" and doc is not None and doc.id == focus_id)
            )
            title = doc.title if doc and doc.title else signal.event_type.replace("_", " ")
            detail_url = ""
            if linked_alert is not None:
                detail_url = f"/ui/alerts/{linked_alert.id}"
            elif doc is not None:
                detail_url = f"/ui/news/{doc.id}"

            visible_events.append({
                "signal_id": signal.id,
                "alert_id": linked_alert.id if linked_alert else "",
                "news_id": doc.id if doc else "",
                "occurred_at": occurred_at,
                "event_type": signal.event_type,
                "direction": signal.direction.value,
                "level": signal.alert_level.value,
                "title": title,
                "detail_url": detail_url,
                "is_focus": is_focus,
            })

        return visible_events, total_events

    def _build_stock_chart(
        self,
        ticker: str,
        *,
        focus_at: datetime | None,
        focus_kind: str,
        focus_id: str,
        range_key: str | None,
    ) -> dict[str, Any]:
        selected_range = _normalize_chart_range(range_key)
        range_config = _CHART_RANGES[selected_range]
        chart = {
            "available": False,
            "ticker": ticker,
            "selected_range": selected_range,
            "ranges": self._chart_range_options(selected_range),
            "label": IEX_LABEL,
            "timeframe": range_config["timeframe"],
            "reason": "",
        }

        if not ticker:
            chart["reason"] = "No resolved ticker is available for this item."
            return chart

        start, end = self._chart_window(focus_at, int(range_config["days"]))
        try:
            bars = self._fetch_chart_bars(
                ticker,
                start=start,
                end=end,
                timeframe=str(range_config["timeframe"]),
            )
        except Exception as exc:
            chart["reason"] = f"Chart data could not be loaded from Alpaca: {exc}"
            return chart

        bars = [
            bar for bar in bars
            if bar.get("timestamp") is not None and bar.get("close") is not None
        ]
        if not bars:
            chart["reason"] = "No IEX price bars were returned for this range."
            return chart

        visible_events, total_events = self._stock_events_for_chart(
            ticker,
            start=start,
            end=end,
            focus_kind=focus_kind,
            focus_id=focus_id,
        )

        width = 760.0
        height = 220.0

        lows = [bar.get("low") for bar in bars if bar.get("low") is not None]
        highs = [bar.get("high") for bar in bars if bar.get("high") is not None]
        closes = [bar.get("close") for bar in bars if bar.get("close") is not None]
        min_price = min(lows or closes)
        max_price = max(highs or closes)
        spread = max_price - min_price
        padding = spread * 0.08 if spread else max(max_price * 0.02, 1.0)
        chart_min = min_price - padding
        chart_max = max_price + padding
        chart_span = max(chart_max - chart_min, 1e-6)

        def to_y(price: float) -> float:
            return round(height - (((price - chart_min) / chart_span) * height), 2)

        # Index-based x: each bar gets equal spacing, no time gaps
        n_bars = len(bars)
        bar_points: list[dict[str, Any]] = []
        points: list[str] = []
        bar_timestamps: list[datetime] = []
        for i, bar in enumerate(bars):
            ts = bar["timestamp"]
            close = float(bar["close"])
            x = round((i / max(n_bars - 1, 1)) * width, 2) if n_bars > 1 else width / 2
            y = to_y(close)
            points.append(f"{x},{y}")
            bar_points.append({"x": x, "y": y, "close": close, "timestamp": ts})
            bar_timestamps.append(ts)

        def _bar_index_for(dt: datetime) -> int:
            """Binary search for the bar index closest in time to *dt*."""
            lo, hi = 0, n_bars - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if bar_timestamps[mid] <= dt:
                    lo = mid
                else:
                    hi = mid - 1
            if lo < n_bars - 1 and abs((bar_timestamps[lo + 1] - dt).total_seconds()) < abs(
                (bar_timestamps[lo] - dt).total_seconds()
            ):
                lo += 1
            return lo

        def _x_for_time(dt: datetime) -> float:
            return bar_points[_bar_index_for(dt)]["x"]

        marker_lines: list[dict[str, Any]] = []
        for event in visible_events:
            if event["is_focus"]:
                continue
            marker_lines.append({
                **event,
                "x": _x_for_time(event["occurred_at"]),
            })

        start_close = bar_points[0]["close"]
        end_close = bar_points[-1]["close"]
        move_pct = None
        if start_close:
            move_pct = ((end_close - start_close) / start_close) * 100.0

        def price_at_y(y: float) -> float:
            return chart_max - (y / height) * chart_span

        grid_y_values = [0.0, round(height * 1 / 3, 2), round(height * 2 / 3, 2), height]
        y_axis_left: list[dict[str, Any]] = []
        y_axis_right: list[dict[str, Any]] = []
        for gy in grid_y_values:
            p = price_at_y(gy)
            y_axis_left.append({"y": gy, "label": f"${p:,.2f}"})
            if start_close:
                rp = ((p - start_close) / start_close) * 100.0
                sign = "+" if rp > 0 else ""
                y_axis_right.append({"y": gy, "label": f"{sign}{rp:.1f}%"})
            else:
                y_axis_right.append({"y": gy, "label": "—"})

        # Day lines: first bar of each calendar day
        us_eastern = ZoneInfo("America/New_York")
        seen_days: set[str] = set()
        day_lines: list[dict[str, Any]] = []
        for i, ts in enumerate(bar_timestamps):
            day_key = ts.astimezone(us_eastern).strftime("%Y-%m-%d")
            if day_key not in seen_days:
                seen_days.add(day_key)
                day_lines.append({"x": bar_points[i]["x"]})

        now = datetime.now(tz=UTC)
        now_visible = start <= now <= end
        now_x = _x_for_time(now) if now_visible else None

        focus_dt = _as_utc(focus_at)
        focus_visible = bool(focus_dt and start <= focus_dt <= end)

        chart.update({
            "available": True,
            "start_at": start,
            "end_at": end,
            "focus_at": focus_dt,
            "focus_visible": focus_visible,
            "focus_x": _x_for_time(focus_dt) if focus_visible and focus_dt else None,
            "points": bar_points,
            "points_attr": " ".join(points),
            "bar_points_json": json.dumps([
                {
                    "x": bp["x"],
                    "y": bp["y"],
                    "close": bp["close"],
                    "ts": bp["timestamp"].isoformat(),
                }
                for bp in bar_points
            ]),
            "event_lines": marker_lines,
            "visible_event_count": len(marker_lines),
            "total_event_count": total_events,
            "min_price": chart_min,
            "max_price": chart_max,
            "first_close": start_close,
            "last_close": end_close,
            "move_pct": move_pct,
            "move_pct_label": _fmt_pct(move_pct),
            "low_price": min_price,
            "high_price": max_price,
            "width": width,
            "height": height,
            "y_axis_left": y_axis_left,
            "y_axis_right": y_axis_right,
            "grid_y_values": grid_y_values,
            "day_lines": day_lines,
            "now_visible": now_visible,
            "now_x": now_x,
        })
        return chart

    def _news_context(self, doc: RawDocument) -> dict:
        processing = self.db.scalar(
            select(ProcessingRun)
            .where(ProcessingRun.raw_document_id == doc.id)
            .order_by(desc(ProcessingRun.created_at))
            .limit(1)
        )
        extracted = self.db.scalar(
            select(ExtractedEvent)
            .where(ExtractedEvent.raw_document_id == doc.id)
            .order_by(desc(ExtractedEvent.created_at))
            .limit(1)
        )

        event_companies: list[EventCompany] = []
        signal = None
        if extracted:
            event_companies = self.db.scalars(
                select(EventCompany).where(EventCompany.extracted_event_id == extracted.id)
            ).all()
            signals = self.db.scalars(
                select(SignalEvent).order_by(desc(SignalEvent.created_at))
            ).all()
            for candidate in signals:
                extracted_ids = (candidate.payload or {}).get("extracted_event_ids", [])
                if extracted.id in extracted_ids:
                    signal = candidate
                    break

        parsed = processing.parsed_result or {} if processing else {}
        signal_payload = signal.payload or {} if signal else {}
        companies = [ec.candidate_name for ec in event_companies if ec.candidate_name]
        if not companies:
            companies = [c.get("name", "") for c in parsed.get("companies", []) if c.get("name")]

        alert = None
        evidence: list[EventEvidence] = []
        if signal:
            alert = self.db.scalar(
                select(Alert)
                .where(Alert.signal_event_id == signal.id)
                .order_by(desc(Alert.created_at))
                .limit(1)
            )
            evidence = self.db.scalars(
                select(EventEvidence).where(EventEvidence.signal_event_id == signal.id)
            ).all()

        return {
            "processing": processing,
            "extracted": extracted,
            "parsed": parsed,
            "signal": signal,
            "signal_payload": signal_payload,
            "event_companies": event_companies,
            "companies": companies,
            "best_company": companies[0] if companies else "",
            "best_mapping": max((ec.mapping_confidence for ec in event_companies), default=None),
            "alert": alert,
            "evidence": evidence,
        }

    def _serialize_news_row(self, doc: RawDocument, context: dict) -> dict:
        processing = context["processing"]
        parsed = context["parsed"]
        signal = context["signal"]
        signal_payload = context["signal_payload"]

        return {
            "id": doc.id,
            "source_name": doc.source_name,
            "source_tier": doc.source_tier.value,
            "title": doc.title,
            "canonical_url": doc.canonical_url,
            "source_url": self._resolve_document_source_url(doc),
            "published_at": doc.published_at,
            "detected_at": doc.detected_at,
            "retrieved_age": _age(doc.detected_at),
            "published_age": _age(doc.published_at),
            "processing_status": processing.status.value if processing else "pending",
            "processing_error": processing.error if processing else None,
            "relevant": parsed.get("relevant"),
            "event_type": parsed.get("event_type", ""),
            "direction": parsed.get("direction", ""),
            "classifier_confidence": parsed.get("confidence"),
            "company": context["best_company"],
            "company_count": len(context["companies"]),
            "mapping_confidence": context["best_mapping"],
            "ticker": signal_payload.get("ticker", ""),
            "alert_level": signal.alert_level.value if signal else "",
            "catalyst_score": signal.catalyst_score if signal else None,
        }

    # ------------------------------------------------------------------
    # Dashboard stats
    # ------------------------------------------------------------------

    def dashboard_stats(self) -> dict:
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)

        tradeable_today = self.db.scalar(
            select(func.count(Alert.id)).where(
                Alert.level == AlertLevel.TRADEABLE,
                Alert.created_at >= day_ago,
            )
        ) or 0

        positions = self.db.scalars(select(Position)).all()
        open_count = sum(1 for p in positions if p.net_amount_eur > 0)
        eur_deployed = sum(p.net_amount_eur for p in positions if p.net_amount_eur > 0)

        # Pipeline: sources with a poll run in the last hour
        sources_ok = self.db.scalar(
            select(func.count(func.distinct(SourcePollRun.source_name))).where(
                SourcePollRun.started_at >= now - timedelta(hours=1)
            )
        ) or 0
        total_sources = self.db.scalar(select(func.count(SourceDefinition.id))) or 5

        # Paper return: avg 1d return across completed snapshots
        day_snaps = self.db.scalars(
            select(PerformanceSnapshot).where(
                PerformanceSnapshot.horizon == "1d",
                PerformanceSnapshot.return_pct.is_not(None),
            )
        ).all()
        avg_return = (
            sum(s.return_pct for s in day_snaps) / len(day_snaps) if day_snaps else None
        )

        return {
            "tradeable_today": tradeable_today,
            "open_positions": open_count,
            "eur_deployed": eur_deployed,
            "sources_ok": sources_ok,
            "total_sources": total_sources,
            "avg_return_1d": avg_return,
            "paper_trade_count": len(day_snaps),
        }

    def recent_news(self, limit: int = 12) -> list[dict]:
        latest_docs = (
            select(
                RawDocument.source_name.label("source_name"),
                RawDocument.external_id.label("external_id"),
                func.max(RawDocument.detected_at).label("detected_at"),
            )
            .group_by(RawDocument.source_name, RawDocument.external_id)
            .subquery()
        )
        docs = self.db.scalars(
            select(RawDocument)
            .join(
                latest_docs,
                and_(
                    RawDocument.source_name == latest_docs.c.source_name,
                    RawDocument.external_id == latest_docs.c.external_id,
                    RawDocument.detected_at == latest_docs.c.detected_at,
                ),
            )
            .order_by(desc(RawDocument.detected_at))
            .limit(limit)
        ).all()
        result = []
        for doc in docs:
            result.append(self._serialize_news_row(doc, self._news_context(doc)))
        return result

    def get_news_detail(self, news_id: str, range_key: str | None = None) -> dict | None:
        doc = self.db.get(RawDocument, news_id)
        if not doc:
            return None

        context = self._news_context(doc)
        row = self._serialize_news_row(doc, context)
        signal = context["signal"]
        alert = context["alert"]
        parsed = context["parsed"]
        resolved_text = self._resolve_full_document_text(doc)

        row.update({
            "text": resolved_text,
            "text_truncated": bool(
                doc.text.endswith("…")
                and resolved_text == doc.text
                and doc.source_name == "truthsocial"
                and doc.fetch_path == "index_fallback"
            ),
            "fetch_path": doc.fetch_path,
            "external_id": doc.external_id,
            "correlation_id": doc.correlation_id,
            "source_metadata": doc.source_metadata or {},
            "signal_id": signal.id if signal else "",
            "signal_created_at": signal.created_at if signal else None,
            "alert_id": alert.id if alert else "",
            "alert_created_at": alert.created_at if alert else None,
            "rationale": context["signal_payload"].get("rationale", ""),
            "risks": context["signal_payload"].get("risks", []),
            "companies": context["companies"],
            "evidence": [
                {"text": e.evidence_text, "start": e.start_offset, "end": e.end_offset}
                for e in context["evidence"]
            ],
            "action_status": signal.action_status if signal else "",
            "relevant": parsed.get("relevant"),
            "chart": self._build_stock_chart(
                row.get("ticker", ""),
                focus_at=doc.published_at or doc.detected_at or (signal.created_at if signal else None),
                focus_kind="news",
                focus_id=doc.id,
                range_key=range_key,
            ),
        })
        return row

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def recent_alerts(self, limit: int = 30) -> list[dict]:
        rows = self.db.execute(
            select(Alert, SignalEvent)
            .join(SignalEvent, Alert.signal_event_id == SignalEvent.id)
            .order_by(desc(Alert.created_at))
            .limit(limit)
        ).all()
        result = []
        for alert, event in rows:
            p = alert.rendered_payload or {}
            ep = event.payload or {}
            result.append({
                "id": alert.id,
                "level": alert.level.value,
                "direction": event.direction.value,
                "event_type": event.event_type,
                "catalyst_score": event.catalyst_score,
                "classifier_confidence": event.classifier_confidence,
                "ticker": p.get("ticker", ""),
                "company": p.get("company", ""),
                "rationale": ep.get("rationale", ""),
                "source_name": ep.get("source_name", ""),
                "age": _age(alert.created_at),
                "created_at": alert.created_at,
            })
        return result

    def get_alert_detail(self, alert_id: str, range_key: str | None = None) -> dict | None:
        row = self.db.execute(
            select(Alert, SignalEvent)
            .join(SignalEvent, Alert.signal_event_id == SignalEvent.id)
            .where(Alert.id == alert_id)
        ).first()
        if not row:
            return None
        alert, event = row

        # Evidence
        evidence = self.db.scalars(
            select(EventEvidence).where(EventEvidence.signal_event_id == event.id)
        ).all()

        # Market snapshot
        ms = (
            self.db.get(MarketSnapshot, alert.market_snapshot_id)
            if alert.market_snapshot_id
            else None
        )
        raw_doc = self._get_raw_doc_from_signal(event)

        p = alert.rendered_payload or {}
        ep = event.payload or {}
        sc = alert.score_components or {}

        return {
            "id": alert.id,
            "level": alert.level.value,
            "direction": event.direction.value,
            "event_type": event.event_type,
            "catalyst_score": event.catalyst_score,
            "classifier_confidence": event.classifier_confidence,
            "ticker": p.get("ticker", ""),
            "company": p.get("company", ""),
            "rationale": ep.get("rationale", ""),
            "risks": ep.get("risks", []),
            "companies": ep.get("companies", []),
            "monetary_amounts": ep.get("monetary_amounts", []),
            "award_ids": ep.get("award_or_contract_ids", []),
            "evidence": [{"text": e.evidence_text, "start": e.start_offset, "end": e.end_offset} for e in evidence],
            "market": {
                "price": ms.price,
                "previous_close": ms.previous_close,
                "intraday_move_pct": ms.intraday_move_pct,
                "market_status": ms.market_status.value,
                "volume": ms.volume,
                "quality_flags": ms.quality_flags,
            } if ms else None,
            "chart": self._build_stock_chart(
                p.get("ticker", ""),
                focus_at=(raw_doc.published_at if raw_doc else None) or (raw_doc.detected_at if raw_doc else None) or alert.created_at,
                focus_kind="signal",
                focus_id=event.id,
                range_key=range_key,
            ),
            "score_components": sc,
            "age": _age(alert.created_at),
            "created_at": alert.created_at,
            "action_status": event.action_status,
            "fingerprint": event.fingerprint,
        }

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def list_positions(self) -> list[dict]:
        positions = self.db.scalars(select(Position).order_by(Position.ticker)).all()
        result = []
        for p in positions:
            last_event = self.db.scalar(
                select(PositionEvent)
                .where(PositionEvent.ticker == p.ticker)
                .order_by(desc(PositionEvent.created_at))
                .limit(1)
            )
            result.append({
                "id": p.id,
                "ticker": p.ticker,
                "direction": p.direction.value,
                "net_amount_eur": p.net_amount_eur,
                "average_price": p.average_price,
                "updated_at": p.updated_at,
                "age": _age(p.updated_at),
                "last_event_type": last_event.event_type.value if last_event else "—",
            })
        return result

    def position_events_log(self, limit: int = 20) -> list[dict]:
        events = self.db.scalars(
            select(PositionEvent).order_by(desc(PositionEvent.created_at)).limit(limit)
        ).all()
        return [
            {
                "ticker": e.ticker,
                "event_type": e.event_type.value,
                "direction": e.direction.value,
                "amount_eur": e.amount_eur,
                "price": e.price,
                "notes": e.notes or "",
                "age": _age(e.created_at),
            }
            for e in events
        ]

    def position_summary(self) -> dict:
        positions = self.db.scalars(select(Position)).all()
        open_pos = [p for p in positions if p.net_amount_eur > 0]
        long_count = sum(1 for p in open_pos if p.direction == Direction.BULLISH)
        short_count = sum(1 for p in open_pos if p.direction == Direction.BEARISH)
        total_eur = sum(p.net_amount_eur for p in open_pos)
        return {
            "total_deployed_eur": total_eur,
            "long_count": long_count,
            "short_count": short_count,
            "open_count": len(open_pos),
        }

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    def paper_performance(self) -> list[dict]:
        trades = self.db.scalars(
            select(PaperTrade).order_by(desc(PaperTrade.entry_time))
        ).all()
        result = []
        for trade in trades:
            snaps = self.db.scalars(
                select(PerformanceSnapshot)
                .where(PerformanceSnapshot.paper_trade_id == trade.id)
            ).all()
            by_horizon = {s.horizon: s for s in snaps}
            result.append({
                "ticker": trade.ticker,
                "direction": trade.direction.value,
                "notional_eur": trade.notional_eur,
                "entry_price": trade.entry_price,
                "entry_time": trade.entry_time,
                "age": _age(trade.entry_time),
                "r1h": by_horizon.get("1h"),
                "r1d": by_horizon.get("1d"),
                "r5d": by_horizon.get("5d"),
                "r20d": by_horizon.get("20d"),
            })
        return result

    def performance_summary(self) -> dict:
        snaps_1d = self.db.scalars(
            select(PerformanceSnapshot).where(
                PerformanceSnapshot.horizon == "1d",
                PerformanceSnapshot.return_pct.is_not(None),
            )
        ).all()
        total = len(snaps_1d)
        if total == 0:
            return {"total_trades": 0, "avg_return_1d": None, "positive_count": 0, "best_return": None, "worst_return": None}
        returns = [s.return_pct for s in snaps_1d]
        return {
            "total_trades": total,
            "avg_return_1d": sum(returns) / total,
            "positive_count": sum(1 for r in returns if r > 0),
            "best_return": max(returns),
            "worst_return": min(returns),
        }

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def pipeline_health(self) -> list[dict]:
        sources = self.db.scalars(select(SourceDefinition)).all()
        result = []
        for src in sources:
            last_run = self.db.scalar(
                select(SourcePollRun)
                .where(SourcePollRun.source_name == src.source_name)
                .order_by(desc(SourcePollRun.started_at))
                .limit(1)
            )
            result.append({
                "source_name": src.source_name,
                "source_tier": src.source_tier.value,
                "enabled": src.enabled,
                "poll_interval_seconds": src.poll_interval_seconds,
                "status": last_run.status.value if last_run else "unknown",
                "last_poll_age": _age(last_run.started_at if last_run else None),
                "new_count": last_run.new_count if last_run else 0,
                "fetch_count": last_run.fetch_count if last_run else 0,
                "errors": last_run.errors if last_run else [],
            })
        return result

    def recent_poll_runs(self, limit: int = 30) -> list[dict]:
        runs = self.db.scalars(
            select(SourcePollRun).order_by(desc(SourcePollRun.started_at)).limit(limit)
        ).all()
        return [
            {
                "source_name": r.source_name,
                "status": r.status.value,
                "fetch_count": r.fetch_count,
                "new_count": r.new_count,
                "fetch_path": r.fetch_path or "—",
                "errors": r.errors,
                "started_at": r.started_at.strftime("%H:%M:%S") if r.started_at else "—",
                "age": _age(r.started_at),
            }
            for r in runs
        ]

    def recent_processing(self, limit: int = 10) -> list[dict]:
        runs = self.db.scalars(
            select(ProcessingRun).order_by(desc(ProcessingRun.created_at)).limit(limit)
        ).all()
        result = []
        for r in runs:
            raw = self.db.get(RawDocument, r.raw_document_id) if r.raw_document_id else None
            result.append({
                "source_name": raw.source_name if raw else "—",
                "title": (raw.title[:55] + "…") if raw and len(raw.title) > 55 else (raw.title if raw else "—"),
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "status": r.status.value,
                "error": r.error,
                "model": r.classifier_model or "—",
                "age": _age(r.created_at),
            })
        return result
