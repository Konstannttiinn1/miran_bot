"""Кастомные эмодзи проекта.

В текстах сообщений кастомные эмодзи рендерятся через HTML-тег <tg-emoji>.
В inline-кнопках используется icon_custom_emoji_id, а обычный emoji-маркер
из текста кнопки удаляется, чтобы не было дублей.
"""

import re

EMOJI_IDS: dict[str, str] = {
    "🌐": "5361558686646968477",
    "👋": "5361606618481993030",
    "🏠": "5361708757099259707",
    "📱": "5361722483814738041",
    "🆘": "5362010590220950583",
    "💎": "5362071845044526162",
    "🏦": "5362052749619932339",
    "⭐": "5361941136304812347",
    "💳": "5361927976525013346",
    "🕒": "5361927976525013346",
    "💰": "5361868572832344347",
    "💵": "5361657930456276475",
    "📎": "5361574487831651517",
    "🧾": "5361551153274330019",
    "🛒": "5361860493998858220",
    "✅": "5361858836141483318",
    "❌": "5362069396913169319",
    "⚠️": "5362027447967588957",
    "🚫": "5361732873340623973",
    "🚨": "5362066626659261836",
    "⏳": "5362066626659261836",
    "🎁": "5361723793779764126",
    "❓": "5359435040067461918",
    "🔗": "5361620577125704736",
    "📅": "5361653369201007631",
    "📊": "5361988642938071773",
    "🤝": "5361677339413503764",
    "🆕": "5361572954528326155",
    "👤": "5361825786368140580",
    "📦": "5361559030244352114",
    "👑": "5361904315550180439",
    "🔧": "5361590460815022700",
    "👥": "5361967168101588411",
    "🔎": "5362040895510190709",
    "🆔": "5361967168101588411",
    "🎭": "5361849984213884441",
    "➕": "5361626912202464679",
    "➖": "5361542730843461985",
    "🔄": "5361997988786906869",
    "🗑": "5361740488317641119",
    "🔙": "5361963495904550690",
    "⬅️": "5361963495904550690",
    "➡️": "5361847304154294008",
}

if not EMOJI_IDS or any(not emoji for emoji in EMOJI_IDS):
    raise RuntimeError("EMOJI_IDS contains an empty emoji key")

_TG_EMOJI_RE = re.compile(
    r'<tg-emoji\s+emoji-id="[^"]+">(?P<fallback>.*?)</tg-emoji>',
    re.DOTALL,
)
_EMOJI_RE = re.compile(
    "|".join(re.escape(emoji) for emoji in sorted(EMOJI_IDS, key=len, reverse=True))
)


def strip_custom_emoji_tags(text: str) -> str:
    return _TG_EMOJI_RE.sub(lambda match: match.group("fallback"), text)


def apply_emoji(text: str, use_custom: bool) -> str:
    if not use_custom:
        return strip_custom_emoji_tags(text)

    plain = strip_custom_emoji_tags(text)
    return _EMOJI_RE.sub(
        lambda match: (
            f'<tg-emoji emoji-id="{EMOJI_IDS[match.group(0)]}">'
            f'{match.group(0)}</tg-emoji>'
        ),
        plain,
    )


def button_parts(text: str, use_custom: bool) -> tuple[str, str | None]:
    plain = strip_custom_emoji_tags(text)
    if not use_custom:
        return plain, None

    icon_id: str | None = None
    label = plain
    for emoji in sorted(EMOJI_IDS, key=len, reverse=True):
        if emoji in label:
            if icon_id is None:
                icon_id = EMOJI_IDS[emoji]
            label = label.replace(emoji, "")

    return label.strip(), icon_id
