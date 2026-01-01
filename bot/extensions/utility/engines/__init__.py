from typing import Optional

from bs4 import NavigableString, Tag
from html2text import html2text as h2t

SAFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    )
}


def get_text(tag: Optional[Tag | NavigableString], **kwargs) -> Optional[str]:
    if not tag:
        return None

    return h2t(str(tag), **kwargs).strip() if tag.text else None
