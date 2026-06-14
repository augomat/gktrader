from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

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
from gktrader.sources.truthsocial import resolve_truthsocial_source_url


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


class UIService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _resolve_document_source_url(self, doc: RawDocument) -> str:
        canonical_url = doc.canonical_url or ""
        if doc.source_name == "truthsocial":
            return resolve_truthsocial_source_url(canonical_url, doc.source_metadata)
        return canonical_url

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

    def get_news_detail(self, news_id: str) -> dict | None:
        doc = self.db.get(RawDocument, news_id)
        if not doc:
            return None

        context = self._news_context(doc)
        row = self._serialize_news_row(doc, context)
        signal = context["signal"]
        alert = context["alert"]
        parsed = context["parsed"]

        row.update({
            "text": doc.text,
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

    def get_alert_detail(self, alert_id: str) -> dict | None:
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
                "model": r.classifier_model or "—",
                "age": _age(r.created_at),
            })
        return result
