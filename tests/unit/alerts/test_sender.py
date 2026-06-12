"""Tests for Telegram outbound sender behavior.

Covers payload building, delivery status mapping, timeout handling,
and continuation message sending.
"""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

import httpx
import pytest

from gktrader.alerts.keyboard import AlertButton
from gktrader.alerts.sender import (
    _build_inline_keyboard,
    _build_send_payload,
    send_alert,
    send_continuation_messages,
    send_telegram_message,
)
from gktrader.config.settings import Settings, get_settings
from gktrader.domain.contracts import AlertPayload
from gktrader.domain.enums import AlertLevel, DeliveryStatus, Direction


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="test:token123",
        telegram_send_base_url="https://api.telegram.org",
    )


class TestBuildInlineKeyboard:
    """Inline keyboard JSON construction."""

    def test_empty_buttons(self) -> None:
        result = _build_inline_keyboard([])
        assert result == {"inline_keyboard": []}

    def test_single_row(self) -> None:
        buttons = [[AlertButton(text="Buy", callback_data="gkt:a:123:buy")]]
        result = _build_inline_keyboard(buttons)
        assert len(result["inline_keyboard"]) == 1
        assert result["inline_keyboard"][0][0]["text"] == "Buy"
        assert result["inline_keyboard"][0][0]["callback_data"] == "gkt:a:123:buy"

    def test_multiple_rows(self) -> None:
        buttons = [
            [AlertButton(text="Buy", callback_data="gkt:a:123:buy")],
            [AlertButton(text="Open source", url="https://example.com")],
        ]
        result = _build_inline_keyboard(buttons)
        assert len(result["inline_keyboard"]) == 2

    def test_url_button(self) -> None:
        buttons = [[AlertButton(text="Open source", url="https://example.com")]]
        result = _build_inline_keyboard(buttons)
        assert result["inline_keyboard"][0][0]["url"] == "https://example.com"
        assert "callback_data" not in result["inline_keyboard"][0][0]


class TestBuildSendPayload:
    """Telegram API payload construction."""

    def test_basic_payload(self) -> None:
        payload = _build_send_payload(chat_id=12345, text="Hello")
        assert payload["chat_id"] == 12345
        assert payload["text"] == "Hello"
        assert "reply_markup" not in payload

    def test_with_buttons(self) -> None:
        buttons = [[AlertButton(text="Buy", callback_data="gkt:a:123:buy")]]
        payload = _build_send_payload(chat_id=12345, text="Alert", buttons=buttons)
        assert "reply_markup" in payload
        assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Buy"


class TestSendTelegramMessage:
    """Low-level Telegram sendMessage."""

    def test_missing_token_raises(self) -> None:
        settings = Settings(telegram_bot_token="")
        with pytest.raises(ValueError, match="telegram_bot_token is not configured"):
            send_telegram_message(settings, chat_id=12345, text="Hello")

    @patch("gktrader.alerts.sender.httpx.Client")
    def test_successful_send(self, mock_client_cls: MagicMock, settings: Settings) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 42}}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = send_telegram_message(settings, chat_id=12345, text="Hello")

        assert result["ok"] is True
        mock_client.post.assert_called_once()

    @patch("gktrader.alerts.sender.httpx.Client")
    def test_send_with_keyboard(self, mock_client_cls: MagicMock, settings: Settings) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        buttons = [[AlertButton(text="Buy", callback_data="gkt:a:123:buy")]]
        send_telegram_message(settings, chat_id=12345, text="Alert", buttons=buttons)

        call_kwargs = mock_client.post.call_args[1]
        assert "json" in call_kwargs
        assert "reply_markup" in call_kwargs["json"]


class TestSendAlert:
    """Alert delivery status mapping."""

    def _make_payload(self) -> AlertPayload:
        return AlertPayload(
            alert_id="test-1",
            level=AlertLevel.TRADEABLE,
            text="Test alert",
            dedupe_key="key",
        )

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_success_returns_sent(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.return_value = {"ok": True}
        status = send_alert(settings, chat_id=12345, payload=self._make_payload())
        assert status == DeliveryStatus.SENT

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_api_error_returns_failed(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.return_value = {"ok": False}
        status = send_alert(settings, chat_id=12345, payload=self._make_payload())
        assert status == DeliveryStatus.FAILED

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_timeout_returns_unknown(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.side_effect = httpx.TimeoutException("timeout")
        status = send_alert(settings, chat_id=12345, payload=self._make_payload())
        assert status == DeliveryStatus.UNKNOWN

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_http_error_returns_failed(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
        status = send_alert(settings, chat_id=12345, payload=self._make_payload())
        assert status == DeliveryStatus.FAILED

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_generic_error_returns_failed(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.side_effect = RuntimeError("unexpected")
        status = send_alert(settings, chat_id=12345, payload=self._make_payload())
        assert status == DeliveryStatus.FAILED


class TestSendContinuationMessages:
    """Continuation message delivery."""

    def test_empty_list(self, settings: Settings) -> None:
        statuses = send_continuation_messages(settings, chat_id=12345, continuation_messages=[])
        assert statuses == []

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_sends_all_messages(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.return_value = {"ok": True}
        messages = ["Part 1", "Part 2", "Part 3"]
        statuses = send_continuation_messages(
            settings, chat_id=12345, continuation_messages=messages
        )
        assert len(statuses) == 3
        assert all(s == DeliveryStatus.SENT for s in statuses)
        assert mock_send.call_count == 3

    @patch("gktrader.alerts.sender.send_telegram_message")
    def test_partial_failure(self, mock_send: MagicMock, settings: Settings) -> None:
        mock_send.side_effect = [
            {"ok": True},
            httpx.TimeoutException("timeout"),
            {"ok": False},
        ]
        messages = ["Part 1", "Part 2", "Part 3"]
        statuses = send_continuation_messages(
            settings, chat_id=12345, continuation_messages=messages
        )
        assert statuses == [
            DeliveryStatus.SENT,
            DeliveryStatus.UNKNOWN,
            DeliveryStatus.FAILED,
        ]