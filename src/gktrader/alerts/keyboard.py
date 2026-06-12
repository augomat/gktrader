"""Contextual inline keyboard definitions for Telegram alerts.

Bullish/unclear alerts get:
  Bought | No trade | Remind 30m | Open source

Bearish alerts get:
  Sold/Reduced | Shorted | No trade | Remind 30m | Open source

Callback payloads must stay under Telegram's 64-byte limit.
Format: gkt:a:<short-id>:<action>
"""

from __future__ import annotations

from gktrader.domain.contracts import AlertButton

# Telegram limit for callback_data (bytes, UTF-8 encoded)
MAX_CALLBACK_BYTES = 64

# Callback payload prefix for GKTrader alerts
_CALLBACK_PREFIX = "gkt:a:"


def derive_short_id(alert_id: str) -> str:
    """Derive a short identifier from a full UUID alert ID.

    Uses the first 8 characters (32 bits of entropy), which is sufficient
    for disambiguation within any practical alert window.
    """
    return alert_id[:8]


def validate_callback_data(data: str) -> bool:
    """Validate that callback data fits within Telegram's 64-byte limit.

    Args:
        data: The callback data string to validate.

    Returns:
        True if the UTF-8 encoded data fits in 64 bytes, False otherwise.
    """
    return len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES


def _build_callback(short_id: str, action: str) -> str:
    """Build a callback data string and validate it.

    Args:
        short_id: Short alert ID (typically 8 hex chars).
        action: Action keyword (e.g. 'buy', 'skip', 'r30').

    Returns:
        A validated callback data string.

    Raises:
        ValueError: If the resulting callback data exceeds the byte limit.
    """
    data = f"{_CALLBACK_PREFIX}{short_id}:{action}"
    if not validate_callback_data(data):
        raise ValueError(
            f"Callback data exceeds {MAX_CALLBACK_BYTES} bytes: "
            f"{len(data.encode('utf-8'))} bytes for '{data}'"
        )
    return data


def build_bullish_keyboard(
    alert_id: str,
    source_url: str | None = None,
) -> list[list[AlertButton]]:
    """Build the inline keyboard for bullish or unclear alerts.

    Args:
        alert_id: Full UUID of the alert.
        source_url: Optional URL for the 'Open source' button.

    Returns:
        A list of button rows (each row is a list of AlertButton).

    Raises:
        ValueError: If any callback data exceeds the byte limit.
    """
    short_id = derive_short_id(alert_id)

    row: list[AlertButton] = [
        AlertButton(text="Bought", callback_data=_build_callback(short_id, "buy")),
        AlertButton(text="No trade", callback_data=_build_callback(short_id, "skip")),
        AlertButton(text="Remind 30m", callback_data=_build_callback(short_id, "r30")),
    ]

    rows: list[list[AlertButton]] = [row]

    if source_url:
        rows.append(
            [
                AlertButton(
                    text="Open source",
                    url=source_url,  # type: ignore[arg-type]
                )
            ]
        )

    return rows


def build_bearish_keyboard(
    alert_id: str,
    source_url: str | None = None,
) -> list[list[AlertButton]]:
    """Build the inline keyboard for bearish alerts.

    Args:
        alert_id: Full UUID of the alert.
        source_url: Optional URL for the 'Open source' button.

    Returns:
        A list of button rows (each row is a list of AlertButton).

    Raises:
        ValueError: If any callback data exceeds the byte limit.
    """
    short_id = derive_short_id(alert_id)

    row: list[AlertButton] = [
        AlertButton(text="Sold/Reduced", callback_data=_build_callback(short_id, "sell")),
        AlertButton(text="Shorted", callback_data=_build_callback(short_id, "short")),
        AlertButton(text="No trade", callback_data=_build_callback(short_id, "skip")),
        AlertButton(text="Remind 30m", callback_data=_build_callback(short_id, "r30")),
    ]

    rows: list[list[AlertButton]] = [row]

    if source_url:
        rows.append(
            [
                AlertButton(
                    text="Open source",
                    url=source_url,  # type: ignore[arg-type]
                )
            ]
        )

    return rows