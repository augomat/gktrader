"""Continuation message handling for long messages and bearish histories.

When bearish alerts include many prior bullish signals, the message may exceed
Telegram's 4096-character limit. Instead of silent truncation, we generate
numbered continuation messages.
"""

from __future__ import annotations


def split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a long message into chunks not exceeding max_len.

    Attempts to split at newline boundaries for readability.
    Each chunk is a complete segment; no trailing truncation mid-word.

    Args:
        text: The text to split.
        max_len: Maximum length per chunk (default 4096 for Telegram).

    Returns:
        A list of message chunks, each ≤ max_len characters.
    """
    if not text:
        return []

    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Try to split at a newline before max_len
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            # No newline found, split at word boundary
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            # No word boundary either, hard split
            split_at = max_len

        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        chunks.append(chunk)

    return chunks


def build_continuation_messages(
    text: str,
    max_len: int = 4096,
) -> list[str]:
    """Build numbered continuation messages from a text block.

    If the text fits in one message, returns it as a single-element list.
    Otherwise, splits into numbered continuation parts.

    Args:
        text: The full text to deliver.
        max_len: Maximum length per message segment (default 4096).

    Returns:
        A list of message text strings. If no splitting is needed,
        returns an empty list (the caller should embed the text directly).
        If splitting is needed, returns the continuation chunks with
        numbering annotations.
    """
    if not text:
        return []

    if len(text) <= max_len:
        # Fits inline — return empty to indicate the caller should use text directly
        return []

    chunks = split_message(text, max_len)

    if len(chunks) <= 1:
        return []

    # Annotate continuation parts with numbering
    result: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        if i == 1:
            # First chunk is kept inline by caller
            continue
        result.append(f"[Continuation {i}/{len(chunks)}]\n{chunk}")

    return result