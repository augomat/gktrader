from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

_COOKIE = "gkt_session"
_MSG = b"gkt-ui-v1"


def make_session_token(secret: str) -> str:
    return hmac.new(secret.encode(), _MSG, hashlib.sha256).hexdigest()


def get_session(request: Request) -> bool:
    from gktrader.config.settings import get_settings
    token = request.cookies.get(_COOKIE, "")
    if not token:
        return False
    expected = make_session_token(get_settings().internal_api_shared_secret)
    return hmac.compare_digest(token, expected)
