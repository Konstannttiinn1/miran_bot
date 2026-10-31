"""Set only the Mini App URL, keeping a private backup of the existing .env."""

import argparse
from pathlib import Path
from shutil import copy2
from time import time_ns
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key


def configure(path: Path, url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                 and not parts.query and not parts.fragment and not any(c.isspace() or ord(c) < 32 for c in url))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Нужен HTTPS-адрес без логина, параметров и пробелов")
    if not path.is_file():
        raise ValueError("Файл .env не найден; настройка отменена")
    normalized = url.rstrip("/") + "/"
    backup = path.with_name(f".env.bak-analytics-{time_ns()}")
    copy2(path, backup)
    try:
        set_key(str(path), "ANALYTICS_WEBAPP_URL", normalized, quote_mode="never")
        if dotenv_values(path).get("ANALYTICS_WEBAPP_URL") != normalized:
            raise ValueError("Проверка настройки не прошла")
    except Exception:
        copy2(backup, path)
        raise
    return normalized


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        result = configure(Path(__file__).resolve().parents[1] / ".env", args.url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print("Адрес Mini App настроен:", result)
