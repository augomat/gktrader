"""Outbound Telegram sender behavior.

Provides sendMessage via Telegram Bot API using httpx.
Supports at-most-once delivery semantics: on timeout after dispatch,
mark the delivery as UNKNOWN and do not retry.

Continuation messages are sent after the main alert with threading disabled
to keep them separate.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from gktrader.alerts.keyboard import AlertButton
from gktrader.alerts.outbox import DeliveryStatus
from gktrader.config.settings import Settings
from gktrader.domain.contracts import AlertPayload

# Maximum Telegram message length in characters
_TELEGRAM_MAX_LEN = 4096

# Timeouts for Telegram API calls
_SEND_TIMEOUT_SECONDS = 15.0


def _build_inline_keyboard(
    buttons: list[list[AlertButton]],
) -> dict[str, Any]:
    """Convert AlertButton rows to Telegram InlineKeyboardMarkup format."""
    rows: list[list[dict[str, str]]] = []
    for row in buttons:
        telegram_row: list[dict[str, str]] = []
        for btn in row:
            btn_dict: dict[str, str] = {"text": btn.text}
            if btn.callback_data:
                btn_dict["callback_data"] = btn.callback_data
            if btn.url:
                url = str(btn.url)
                if getattr(btn.url, "path", None) == "/" and not getattr(btn.url, "query", None):
                    url = url.rstrip("/")
                btn_dict["url"] = url
            telegram_row.append(btn_dict)
        rows.append(telegram_row)

    return {"inline_keyboard": rows}


def _build_send_payload(
    chat_id: int,
    text: str,
    buttons: list[list[AlertButton]] | None = None,
) -> dict[str, Any]:
    """Build the Telegram sendMessage request payload.

    Args:
        chat_id: The target Telegram chat/user ID.
        text: The message text.
        buttons: Optional inline keyboard buttons.

    Returns:
        A dict suitable for JSON-serialization as the Telegram API request body.
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }

    if buttons:
        payload["reply_markup"] = _build_inline_keyboard(buttons)

    return payload


def send_telegram_message(
    settings: Settings,
    chat_id: int,
    text: str,
    buttons: list[list[AlertButton]] | None = None,
    timeout: float = _SEND_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send a message to Telegram via the Bot API.

    This is a low-level function. It does NOT manage delivery status.
    The caller is responsible for outbox state management.

    Args:
        settings: Application settings (must have telegram_bot_token).
        chat_id: Target Telegram user/chat ID.
        text: Message body (should be ≤ 4096 characters).
        buttons: Optional inline keyboard.
        timeout: HTTP request timeout in seconds.

    Returns:
        The parsed Telegram API response JSON.

    Raises:
        httpx.TimeoutException: If the request times out after dispatch.
        httpx.HTTPStatusError: If Telegram returns a non-2xx status.
        ValueError: If the bot token is not configured.
    """
    if not settings.telegram_bot_token:
        raise ValueError("telegram_bot_token is not configured")

    base_url = settings.telegram_send_base_url.rstrip("/")
    url = f"{base_url}/bot{settings.telegram_bot_token}/sendMessage"

    payload = _build_send_payload(chat_id, text, buttons)

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


def send_alert(
    settings: Settings,
    chat_id: int,
    payload: AlertPayload,
) -> DeliveryStatus:
    """Send the main alert message to Telegram.

    Handles outbox-adjacent delivery status:
    - SENT: Telegram accepted the message.
    - UNKNOWN: Request timed out after dispatch.
    - FAILED: Telegram returned an error.

    Does NOT manage the outbox entry itself — the caller handles that.

    Args:
        settings: Application settings.
        chat_id: Target Telegram user/chat ID.
        payload: The rendered alert payload.

    Returns:
        DeliveryStatus indicating the outcome.
    """
    try:
        response = send_telegram_message(
            settings=settings,
            chat_id=chat_id,
            text=payload.text,
            buttons=payload.buttons if payload.buttons else None,
        )
        if response.get("ok"):
            return DeliveryStatus.SENT
        return DeliveryStatus.FAILED
    except httpx.TimeoutException:
        # Request dispatched but we don't know if Telegram received it
        return DeliveryStatus.UNKNOWN
    except httpx.HTTPStatusError:
        return DeliveryStatus.FAILED
    except Exception:
        return DeliveryStatus.FAILED


def send_continuation_messages(
    settings: Settings,
    chat_id: int,
    continuation_messages: list[str],
) -> list[DeliveryStatus]:
    """Send continuation messages after the main alert.

    Continuation messages are sent without inline keyboards.
    Each message is sent individually.

    Args:
        settings: Application settings.
        chat_id: Target Telegram user/chat ID.
        continuation_messages: List of message texts to send.

    Returns:
        A list of DeliveryStatus values, one per continuation message.
    """
    statuses: list[DeliveryStatus] = []
    for msg in continuation_messages:
        try:
            response = send_telegram_message(
                settings=settings,
                chat_id=chat_id,
                text=msg,
                buttons=None,
            )
            if response.get("ok"):
                statuses.append(DeliveryStatus.SENT)
            else:
                statuses.append(DeliveryStatus.FAILED)
        except httpx.TimeoutException:
            statuses.append(DeliveryStatus.UNKNOWN)
        except Exception:
            statuses.append(DeliveryStatus.FAILED)

    return statuses
