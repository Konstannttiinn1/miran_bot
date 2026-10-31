"""Validate raw Telegram Mini App initData; never trust initDataUnsafe."""

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl


def verify_init_data(raw: str, bot_token: str, *, max_age=3600, now=None) -> dict:
    try:
        if not bot_token or not raw or len(raw) > 16384:
            raise ValueError
        pairs = parse_qsl(raw, strict_parsing=True, max_num_fields=64)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError
        supplied_hash = fields.pop("hash")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash):
            raise ValueError
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied_hash.lower()):
            raise ValueError
        stamp = int(fields["auth_date"])
        age = (time.time() if now is None else now) - stamp
        if age < -30 or age > max_age or max_age < 1:
            raise ValueError
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            raise ValueError
        return user
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError("Invalid or expired Telegram session") from None
