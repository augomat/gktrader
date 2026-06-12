"""Celery task definitions — wire SignalPipeline to the Celery worker.

Each task creates a DB session, instantiates the pipeline with real
adapters and providers, runs a stage, and commits.

The inner pipeline logic is in ``pipeline.py`` and is independently
testable with mock adapters.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from gktrader.config.settings import get_settings
from gktrader.db.models import (
    Alert,
    InteractionState,
    MarketSnapshot,
    PaperTrade,
    PerformanceSnapshot,
    Position,
    WeeklyReport,
)
from gktrader.db.session import SessionLocal
from gktrader.domain.enums import InteractionStateType
from gktrader.intelligence.resolver import SecCompanyRecord, TickerResolver
from gktrader.reporting.weekly import WeeklyReportRow, build_weekly_report
from gktrader.sources import (
    CommerceAdapter,
    NISTAdapter,
    SECAdapter,
    TruthSocialAdapter,
    WhiteHouseAdapter,
)
from gktrader.tasks.celery_app import celery_app
from gktrader.tasks.pipeline import PipelineResult, SignalPipeline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cached, deterministic ticker resolver
# ---------------------------------------------------------------------------
#
# The resolver must be loaded with the SEC company/ticker master, otherwise no
# company name resolves to a validated ticker and nothing can ever reach
# TRADEABLE (and market data gets queried with company names instead of
# symbols).  The master is ~10k rows and must not be re-downloaded on every
# 60-second poll (SEC rate limits), so it is cached per worker process with a
# daily TTL and refreshed by ``refresh_ticker_master``.

_RESOLVER_TTL_SECONDS = 24 * 3600
_RESOLVER_LOCK = threading.Lock()
_RESOLVER_CACHE: dict[str, object] = {"resolver": None, "loaded_at": 0.0}


def _load_resolver(settings) -> TickerResolver:
    """Fetch the SEC ticker master and build a loaded resolver."""
    resolver = TickerResolver()
    client = httpx.Client(timeout=30)
    try:
        records = SECAdapter.fetch_ticker_master(client, settings.sec_user_agent)
    finally:
        client.close()
    resolver.load_sec_master(
        [
            SecCompanyRecord(ticker=r["ticker"], name=r["name"], cik=str(r["cik"]))
            for r in records
            if r.get("ticker")
        ]
    )
    return resolver


def _get_resolver(settings, *, force: bool = False) -> TickerResolver:
    """Return a resolver loaded with the SEC master, cached with a daily TTL.

    On fetch failure: keep serving the previous (stale) resolver if we have one;
    only as a last resort return an empty resolver, and in that case do *not*
    record a load time so the next poll retries the download.
    """
    with _RESOLVER_LOCK:
        cached = _RESOLVER_CACHE["resolver"]
        age = _time.time() - float(_RESOLVER_CACHE["loaded_at"])  # type: ignore[arg-type]
        if not force and cached is not None and age < _RESOLVER_TTL_SECONDS:
            return cached  # type: ignore[return-value]
        try:
            resolver = _load_resolver(settings)
        except Exception as exc:  # network/parse failure
            logger.warning("SEC ticker master load failed: %s", exc)
            if cached is not None:
                return cached  # type: ignore[return-value]
            return TickerResolver()
        _RESOLVER_CACHE["resolver"] = resolver
        _RESOLVER_CACHE["loaded_at"] = _time.time()
        return resolver


def _build_pipeline() -> SignalPipeline:
    """Build a production SignalPipeline instance with real adapters."""
    settings = get_settings()
    db = SessionLocal()

    adapters = {
        "whitehouse": WhiteHouseAdapter(),
        "nist": NISTAdapter(),
        "truthsocial": TruthSocialAdapter(
            gkfetch_url=settings.gkfetch_url,
            gkfetch_secret=settings.gkfetch_secret,
        ),
        "commerce": CommerceAdapter(
            gkfetch_url=settings.gkfetch_url,
            gkfetch_secret=settings.gkfetch_secret,
        ),
        "sec_8k": SECAdapter(),
    }

    resolver = _get_resolver(settings)

    return SignalPipeline(
        db_session=db,
        settings=settings,
        adapters=adapters,
        resolver=resolver,
    )


def _run_in_session(
    pipeline: SignalPipeline,
    source_names: list[str] | None = None,
) -> PipelineResult:
    """Run the full pipeline in a transactional session.

    Commits on success, rolls back on failure.
    """
    try:
        result = pipeline.run_full_pipeline(source_names)
        pipeline.db.commit()
        return result
    except Exception:
        pipeline.db.rollback()
        raise
    finally:
        pipeline.db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def poll_sources(self) -> dict:
    """Poll all enabled sources, process new documents, and create alerts.

    This replaces the noop stub with a full persisted pipeline:
      source ingestion -> classification -> signal creation -> alert outbox.
    """
    pipeline = _build_pipeline()
    result = _run_in_session(pipeline)
    return {
        "status": "completed",
        "task": self.name,
        "ran_at": datetime.now(UTC).isoformat(),
        "raw_documents": result.total_raw_documents,
        "signals": result.total_signals,
        "alerts": result.total_alerts,
    }


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def refresh_ticker_master(self) -> dict:
    """Reload the SEC company/ticker master into the resolver cache (spec §9).

    Runs daily so the deterministic resolver stays current. The resolver also
    self-refreshes via a TTL on first use, but this task guarantees a daily
    reload independent of poll cadence.
    """
    settings = get_settings()
    resolver = _get_resolver(settings, force=True)
    return {
        "status": "completed",
        "task": self.name,
        "ran_at": datetime.now(UTC).isoformat(),
        "companies_loaded": len(resolver.get_sec_records()),
    }


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def deliver_pending_alerts(self) -> dict:
    """Claim and deliver pending outbox alert entries via Telegram.

    Uses at-most-once delivery semantics.
    """
    settings = get_settings()
    db = SessionLocal()
    pipeline = SignalPipeline(
        db_session=db,
        settings=settings,
        adapters={},
        resolver=TickerResolver(),
    )

    try:
        results = pipeline.deliver_pending()
        db.commit()
        return {
            "status": "completed",
            "task": self.name,
            "ran_at": datetime.now(UTC).isoformat(),
            "delivered": len([r for r in results if r.status.value == "sent"]),
            "failed": len([r for r in results if r.status.value == "failed"]),
            "unknown": len([r for r in results if r.status.value == "unknown"]),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def generate_weekly_review(self) -> dict:
    """Generate a meaningful weekly performance review and persist it.

    Queries paper_trades and performance_snapshots to build grouped
    performance rows, constructs the report via ``build_weekly_report``,
    and stores the result in the ``weekly_reports`` table.

    Runs every Sunday at 14:00 Europe/Vienna (DST-safe via Celery crontab).
    """
    db = SessionLocal()
    try:
        now = datetime.now(UTC)

        # Gather paper trades with their performance snapshots
        trades = db.scalars(select(PaperTrade).order_by(PaperTrade.entry_time)).all()

        rows: list[WeeklyReportRow] = []
        for trade in trades:
            snapshots = db.scalars(
                select(PerformanceSnapshot)
                .where(PerformanceSnapshot.paper_trade_id == trade.id)
                .order_by(PerformanceSnapshot.horizon)
            ).all()

            # Use the 1d horizon for return/drawdown/runup if available
            day_snapshot = next((s for s in snapshots if s.horizon == "1d"), None)

            rows.append(
                WeeklyReportRow(
                    source_name="",  # TODO: derive from signal event chain
                    event_type="",
                    direction=trade.direction,
                    alert_level="TRADEABLE",  # TODO: derive from linked alert
                    ticker=trade.ticker,
                    notional_eur=trade.notional_eur,
                    return_pct=day_snapshot.return_pct if day_snapshot else None,
                    max_drawdown_pct=day_snapshot.max_drawdown_pct if day_snapshot else None,
                    max_runup_pct=day_snapshot.max_runup_pct if day_snapshot else None,
                    missing_data=day_snapshot.missing_data if day_snapshot else True,
                )
            )

        report_payload = build_weekly_report(rows, generated_at=now)

        # Attach open positions for confirmation
        positions = db.scalars(select(Position).where(Position.net_amount_eur > 0).order_by(Position.ticker)).all()
        report_payload["open_positions"] = [
            {
                "position_id": p.id,
                "ticker": p.ticker,
                "direction": p.direction.value,
                "net_amount_eur": p.net_amount_eur,
                "average_price": p.average_price,
            }
            for p in positions
        ]

        report = WeeklyReport(report_payload=report_payload, delivered=False)
        db.add(report)
        db.commit()

        return {
            "status": "completed",
            "task": self.name,
            "ran_at": now.isoformat(),
            "report_id": report.id,
            "total_trades": report_payload.get("total_trades", 0),
            "total_notional_eur": report_payload.get("total_notional_eur", 0.0),
            "open_positions": len(positions),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def compute_performance_horizons(self) -> dict:
    """Compute 1h/1d/5d/20d performance horizons for due paper trades.

    For each PaperTrade without a PerformanceSnapshot at a given horizon,
    checks whether the horizon target time has passed, looks up the current
    price via Alpaca, and persists a PerformanceSnapshot row.

    Runs on a schedule (e.g. every 30 minutes) so horizons are captured
    shortly after they become due.
    """
    settings = get_settings()
    db = SessionLocal()
    now = datetime.now(UTC)

    try:
        trades = db.scalars(select(PaperTrade)).all()
        if not trades:
            return {"status": "completed", "task": self.name, "ran_at": now.isoformat(), "snapshots_written": 0}

        from gktrader.reporting.horizons import SUPPORTED_HORIZONS, compute_horizon_session

        snapshots_written = 0
        for trade in trades:
            if not trade.entry_time:
                continue

            existing_horizons = {
                s.horizon
                for s in db.scalars(
                    select(PerformanceSnapshot).where(PerformanceSnapshot.paper_trade_id == trade.id)
                ).all()
            }

            for horizon in SUPPORTED_HORIZONS:
                if horizon in existing_horizons:
                    continue

                hr = compute_horizon_session(trade.entry_time, horizon)
                if hr.target_time is None or now < hr.target_time:
                    continue  # not due yet

                # Fetch price from Alpaca
                exit_price: float | None = None
                missing_data = hr.missing_data
                quality_flags: list[str] = list(hr.quality_flags)

                if settings.alpaca_api_key and settings.alpaca_api_secret:
                    try:
                        from gktrader.marketdata.alpaca import AlpacaIEXProvider

                        provider = AlpacaIEXProvider(
                            api_key=settings.alpaca_api_key,
                            api_secret=settings.alpaca_api_secret,
                        )
                        snap = provider.snapshot(trade.ticker)
                        provider.close()
                        exit_price = snap.price
                        quality_flags.extend(snap.quality_flags)
                    except Exception as exc:
                        missing_data = True
                        quality_flags.append(f"alpaca_error: {exc}")
                else:
                    missing_data = True
                    quality_flags.append("no_alpaca_credentials")

                return_pct: float | None = None
                max_drawdown_pct: float | None = None
                max_runup_pct: float | None = None

                if exit_price is not None and trade.entry_price and trade.entry_price > 0:
                    raw_return = (exit_price - trade.entry_price) / trade.entry_price * 100
                    # For bearish/short paper trades, invert return
                    from gktrader.domain.enums import Direction as Dir
                    if trade.direction == Dir.BEARISH:
                        raw_return = -raw_return
                    return_pct = round(raw_return, 4)
                    # Without intrabar data we can only record the point return;
                    # drawdown/runup remain None (missing_data flagged accordingly).
                    quality_flags.append("point_in_time_only")
                else:
                    missing_data = True

                snap_row = PerformanceSnapshot(
                    paper_trade_id=trade.id,
                    horizon=horizon,
                    return_pct=return_pct,
                    max_drawdown_pct=max_drawdown_pct,
                    max_runup_pct=max_runup_pct,
                    missing_data=missing_data,
                    quality={"quality_flags": quality_flags},
                )
                db.add(snap_row)
                snapshots_written += 1

        if snapshots_written:
            db.commit()

        return {
            "status": "completed",
            "task": self.name,
            "ran_at": now.isoformat(),
            "snapshots_written": snapshots_written,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def deliver_weekly_review(self) -> dict:
    """Deliver the latest undelivered weekly review via Telegram.

    Finds any WeeklyReport where ``delivered`` is False, sends the
    summary and open-position confirmation request via Telegram, and
    marks the report as delivered.  Creates InteractionState entries
    to track pending position confirmations.

    Idempotent: only undelivered reports are picked up; once delivered
    the flag prevents re-delivery.
    """
    settings = get_settings()
    db = SessionLocal()

    try:
        from sqlalchemy import desc

        report_row = db.scalars(
            select(WeeklyReport)
            .where(WeeklyReport.delivered == False)  # noqa: E712
            .order_by(desc(WeeklyReport.created_at))
            .limit(1)
        ).first()

        if not report_row:
            return {"status": "skipped", "reason": "no_undelivered_reports"}

        report = report_row.report_payload or {}
        positions = report.get("open_positions", [])
        total_trades = report.get("total_trades", 0)
        total_notional = report.get("total_notional_eur", 0.0)
        total_return = report.get("total_return_pct")

        # Build the weekly review message
        lines = [
            "📊 *Weekly GKTrader Review*",
            "",
            f"Total paper trades: {total_trades}",
            f"Total notional: EUR {total_notional:,.2f}",
        ]
        if total_return is not None:
            lines.append(f"Average return (1d): {total_return:+.2f}%")
        lines.append("")

        # Bug #12: Build inline keyboard rows for each open position (spec §14).
        # Each position gets Keep open / Close / Adjust buttons.
        # Callback data format: gkt:p:<short-id>:<action> (must fit 64 bytes)
        inline_keyboard: list[list[dict]] = []

        if positions:
            lines.append("*Open Positions — please confirm each:*")
            lines.append("")
            for i, pos in enumerate(positions, 1):
                ticker = pos.get("ticker", "???")
                direction = str(pos.get("direction", "bullish")).upper()
                amount = pos.get("net_amount_eur", 0)
                avg_price = pos.get("average_price")
                pid = pos.get("position_id", "")
                price_str = f" @ EUR {avg_price:,.2f}" if avg_price else ""
                lines.append(
                    f"{i}. *{ticker}* ({direction}) EUR {amount:,.2f}{price_str}"
                )
                # Short position ID for callback (8 chars, fits in 64-byte limit)
                short_pid = pid[:8] if pid else f"p{i}"
                inline_keyboard.append([
                    {"text": f"{ticker} Keep open", "callback_data": f"gkt:p:{short_pid}:keep"},
                    {"text": "Close", "callback_data": f"gkt:p:{short_pid}:close"},
                    {"text": "Adjust", "callback_data": f"gkt:p:{short_pid}:adjust"},
                ])
        else:
            lines.append("No open positions.")

        message_text = "\n".join(lines)

        # Send Telegram message with inline keyboard
        telegram_sent = False
        if settings.telegram_bot_token:
            try:
                import httpx

                send_payload: dict = {
                    "chat_id": settings.telegram_owner_id,
                    "text": message_text,
                    "parse_mode": "Markdown",
                }
                if inline_keyboard:
                    send_payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

                resp = httpx.post(
                    f"{settings.telegram_send_base_url}/bot{settings.telegram_bot_token}/sendMessage",
                    json=send_payload,
                    timeout=10.0,
                )
                resp.raise_for_status()
                telegram_sent = True
            except Exception:
                # Non-fatal: report is still marked delivered, OpenClaw can fetch it
                pass

        # Create interaction states to track pending confirmations
        if positions:
            for pos in positions:
                pid = pos.get("position_id", "")
                if pid:
                    istate = InteractionState(
                        owner_id=str(settings.telegram_owner_id),
                        state_type=InteractionStateType.AWAITING_POSITION_CONFIRMATION,
                        payload={
                            "position_id": pid,
                            "ticker": pos.get("ticker", ""),
                            "direction": str(pos.get("direction", "")),
                            "net_amount_eur": pos.get("net_amount_eur", 0),
                            "report_id": report_row.id,
                        },
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                    )
                    db.add(istate)

        report_row.delivered = True
        db.add(report_row)
        db.commit()

        return {
            "status": "completed",
            "task": self.name,
            "ran_at": datetime.now(UTC).isoformat(),
            "report_id": report_row.id,
            "telegram_sent": telegram_sent,
            "pending_confirmations": len(positions),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def deliver_snooze_reminders(self) -> dict:
    """Check for due snooze reminders and deliver them once.

    Queries ``interaction_states`` for SNOOZE_REMINDER entries whose
    ``expires_at`` has passed.  Sends a Telegram reminder message for
    the associated alert and removes the reminder entry.

    Idempotent: expired reminders are only delivered once.
    """
    settings = get_settings()
    db = SessionLocal()
    now = datetime.now(UTC)

    try:
        due_reminders = db.scalars(
            select(InteractionState).where(
                InteractionState.state_type == InteractionStateType.SNOOZE_REMINDER,
                InteractionState.expires_at <= now,
            )
        ).all()

        delivered_count = 0
        for reminder in due_reminders:
            payload = reminder.payload or {}
            alert_id = payload.get("alert_id", "")
            minutes = payload.get("minutes", 30)

            # Send Telegram reminder (best-effort; use the same sender as alerts)
            if settings.telegram_bot_token and alert_id:
                try:
                    alert = db.get(Alert, alert_id) if alert_id else None
                    alert_text = f"⏰ Reminder: alert {alert_id}"
                    if alert and alert.rendered_payload:
                        rendered = alert.rendered_payload
                        ticker = rendered.get("ticker", "")
                        level = rendered.get("level", "")
                        alert_text = (
                            f"⏰ Reminder ({minutes}min snooze): {ticker} {level}\n"
                            f"Alert ID: {alert_id}"
                        )
                except Exception:
                    pass  # alert lookup failed, use basic text

                try:
                    import httpx
                    resp = httpx.post(
                        f"{settings.telegram_send_base_url}/bot{settings.telegram_bot_token}/sendMessage",
                        json={
                            "chat_id": settings.telegram_owner_id,
                            "text": alert_text,
                        },
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                except Exception:
                    # Non-fatal: log and continue; reminder will be retried next cycle
                    continue

            # Remove the delivered reminder (idempotent: only delivered once)
            db.delete(reminder)
            delivered_count += 1

        if delivered_count:
            db.commit()

        return {
            "status": "completed",
            "task": self.name,
            "ran_at": now.isoformat(),
            "delivered": delivered_count,
            "remaining": len(due_reminders) - delivered_count,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
