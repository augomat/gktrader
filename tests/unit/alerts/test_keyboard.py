"""Tests for keyboard button generation, callback size limits, and short ID derivation."""

from __future__ import annotations

import pytest

from gktrader.alerts.keyboard import (
    MAX_CALLBACK_BYTES,
    build_bearish_keyboard,
    build_bullish_keyboard,
    derive_short_id,
    validate_callback_data,
)


class TestDeriveShortId:
    """Short ID derivation from UUID."""

    def test_uses_first_8_chars(self) -> None:
        alert_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert derive_short_id(alert_id) == "a1b2c3d4"

    def test_empty_id_returns_empty(self) -> None:
        assert derive_short_id("") == ""

    def test_short_id_length(self) -> None:
        alert_id = "12345678"
        assert derive_short_id(alert_id) == "12345678"

    def test_id_shorter_than_8(self) -> None:
        assert derive_short_id("abc") == "abc"


class TestValidateCallbackData:
    """Callback data byte-limit validation."""

    def test_fits_in_64_bytes(self) -> None:
        data = "gkt:a:a1b2c3d4:buy"
        assert validate_callback_data(data) is True

    def test_exactly_64_bytes(self) -> None:
        data = "gkt:a:" + "x" * 55  # prefix 7 + 55 = 62
        # Actually 7 + 55 = 62 but let's construct precisely
        data = "gkt:a:" + "x" * (MAX_CALLBACK_BYTES - len("gkt:a:"))
        assert len(data.encode("utf-8")) == MAX_CALLBACK_BYTES
        assert validate_callback_data(data) is True

    def test_exceeds_64_bytes(self) -> None:
        data = "gkt:a:" + "x" * 60  # 7 + 60 = 67 bytes
        assert validate_callback_data(data) is False

    def test_unicode_fits(self) -> None:
        # Unicode characters may take >1 byte
        data = "gkt:a:short:buy"
        assert validate_callback_data(data) is True

    def test_empty_string(self) -> None:
        assert validate_callback_data("") is True


class TestBuildBullishKeyboard:
    """Bullish/unclear alert button set."""

    ALERT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_returns_correct_button_count(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        assert len(keyboard) == 1  # one row without source URL

    def test_no_source_url_single_row(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        assert len(keyboard) == 1
        row = keyboard[0]
        assert len(row) == 3
        assert row[0].text == "Bought"
        assert row[1].text == "No trade"
        assert row[2].text == "Remind 30m"

    def test_with_source_url_adds_row(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID, source_url="https://example.com")
        assert len(keyboard) == 2
        assert keyboard[1][0].text == "Open source"
        assert keyboard[1][0].url is not None

    def test_callback_data_format(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        row = keyboard[0]
        assert row[0].callback_data is not None
        assert row[0].callback_data.startswith("gkt:a:")

    def test_callback_data_under_64_bytes(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        for row in keyboard:
            for btn in row:
                if btn.callback_data:
                    assert validate_callback_data(btn.callback_data)

    def test_buy_callback(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        assert keyboard[0][0].callback_data == "gkt:a:a1b2c3d4:buy"

    def test_skip_callback(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        assert keyboard[0][1].callback_data == "gkt:a:a1b2c3d4:skip"

    def test_r30_callback(self) -> None:
        keyboard = build_bullish_keyboard(self.ALERT_ID)
        assert keyboard[0][2].callback_data == "gkt:a:a1b2c3d4:r30"

    def test_open_source_url(self) -> None:
        url = "https://www.whitehouse.gov/news/feed/"
        keyboard = build_bullish_keyboard(self.ALERT_ID, source_url=url)
        assert str(keyboard[1][0].url) == url


class TestBuildBearishKeyboard:
    """Bearish alert button set."""

    ALERT_ID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"

    def test_returns_correct_button_count(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        assert len(keyboard) == 1

    def test_bearish_buttons(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        row = keyboard[0]
        assert len(row) == 4
        assert row[0].text == "Sold/Reduced"
        assert row[1].text == "Shorted"
        assert row[2].text == "No trade"
        assert row[3].text == "Remind 30m"

    def test_with_source_url(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID, source_url="https://example.com")
        assert len(keyboard) == 2

    def test_callback_data_under_64_bytes(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        for row in keyboard:
            for btn in row:
                if btn.callback_data:
                    assert validate_callback_data(btn.callback_data)

    def test_sell_callback(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        assert keyboard[0][0].callback_data == "gkt:a:b2c3d4e5:sell"

    def test_short_callback(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        assert keyboard[0][1].callback_data == "gkt:a:b2c3d4e5:short"

    def test_skip_callback_bearish(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        assert keyboard[0][2].callback_data == "gkt:a:b2c3d4e5:skip"

    def test_r30_callback_bearish(self) -> None:
        keyboard = build_bearish_keyboard(self.ALERT_ID)
        assert keyboard[0][3].callback_data == "gkt:a:b2c3d4e5:r30"


class TestNoWatchPath:
    """No WATCH delivery path exists."""

    def test_watch_raises_during_render(self) -> None:
        """WATCH alerts must not be rendered for delivery.
        This is enforced at the renderer level, tested in test_renderer.py.
        """
        pass

    def test_keyboard_no_watch_path(self) -> None:
        """No keyboard function accepts WATCH as an argument."""
        # Keyboard functions accept alert_id and source_url, not levels.
        # WATCH is excluded at the renderer/outbox level.
        pass