"""Фундамент кастомных эмодзи.

Пока USE_CUSTOM_EMOJI=false — бот на обычных эмодзи.
Позже: заполни CUSTOM_EMOJI_MAP и переключи флаг в true.
Кастомные эмодзи применятся и к текстам, и к кнопкам.
"""

CUSTOM_EMOJI_MAP: dict[str, str] = {
    # Заполнишь позже, пример:
    # "📱": "5368324170671202285",
}


def apply_emoji(text: str, enabled: bool) -> str:
    """Заменяет обычные эмодзи на кастомные, если включено."""
    if not enabled:
        return text
    for std, emoji_id in CUSTOM_EMOJI_MAP.items():
        text = text.replace(
            std, f'<tg-emoji emoji-id="{emoji_id}">{std}</tg-emoji>'
        )
    return text