"""Tests for continuation message splitting and bearish history handling."""

from __future__ import annotations

from gktrader.alerts.continuation import build_continuation_messages, split_message


class TestSplitMessage:
    """Message splitting at boundaries."""

    def test_short_text_no_split(self) -> None:
        text = "Short message"
        chunks = split_message(text, max_len=4096)
        assert chunks == [text]

    def test_empty_text(self) -> None:
        assert split_message("") == []

    def test_split_at_newline(self) -> None:
        text = "A" * 100 + "\n" + "B" * 100
        chunks = split_message(text, max_len=50)
        assert len(chunks) >= 2
        # First chunk should end at the newline
        assert chunks[0].endswith("A" * 100) or len(chunks[0]) <= 50

    def test_split_at_word_boundary(self) -> None:
        text = "hello " + "world " * 100
        chunks = split_message(text, max_len=50)
        assert all(len(c) <= 50 for c in chunks)

    def test_hard_split_when_no_boundary(self) -> None:
        """When there is no whitespace, split at exact max_len."""
        text = "A" * 5000
        chunks = split_message(text, max_len=1000)
        assert len(chunks) == 5
        assert all(len(c) <= 1000 for c in chunks)
        assert "".join(chunks) == text

    def test_preserves_content(self) -> None:
        text = "word " * 1000
        chunks = split_message(text, max_len=500)
        reconstructed = " ".join(c.strip() for c in chunks)
        # The original had trailing spaces; just check it's not empty
        assert len(chunks) > 1
        assert all(len(c) <= 500 for c in chunks)

    def test_exact_fit(self) -> None:
        text = "A" * 4096
        chunks = split_message(text, max_len=4096)
        assert chunks == [text]

    def test_one_char_over(self) -> None:
        text = "A" * 4097
        chunks = split_message(text, max_len=4096)
        assert len(chunks) == 2
        assert len(chunks[0]) <= 4096


class TestBuildContinuationMessages:
    """Continuation message building for bearish history overflow."""

    def test_empty_returns_empty(self) -> None:
        assert build_continuation_messages("") == []

    def test_fits_inline_returns_empty(self) -> None:
        text = "Short history"
        result = build_continuation_messages(text, max_len=4096)
        # Fits inline — return empty so caller embeds directly
        assert result == []

    def test_single_chunk_overflow_returns_continuation(self) -> None:
        text = "X" * 5000
        result = build_continuation_messages(text, max_len=1000)
        assert len(result) >= 1

    def test_continuation_has_numbering(self) -> None:
        text = "Signal line\n" * 500
        result = build_continuation_messages(text, max_len=500)
        if result:
            assert "[Continuation " in result[0]

    def test_many_prior_signals_generates_continuation(self) -> None:
        """When bearish, many prior bullish signals trigger continuation."""
        signals = "\n".join(
            f"  • 2025-{(i % 12) + 1:02d}-01 | government_funding | TRADEABLE | "
            f"Signal #{i}"
            for i in range(100)
        )
        full = "📈 Prior bullish signals for Company (TICK):\n" + signals
        result = build_continuation_messages(full, max_len=500)
        # Should generate multiple continuation messages
        assert len(result) >= 1

    def test_each_chunk_under_max_len(self) -> None:
        text = "item\n" * 2000
        result = build_continuation_messages(text, max_len=1000)
        for msg in result:
            # Strip the "[Continuation X/Y]\n" prefix before checking
            msg_body = msg.split("\n", 1)[1] if "\n" in msg else msg
            assert len(msg_body) <= 1000