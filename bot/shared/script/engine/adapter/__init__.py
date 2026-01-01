from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Optional

from discord import Asset, Color

Objects = dict[
    str, Optional[str | int | bool | Asset | Color | datetime | Callable | "Adapter"]
]


def _sub_match(match: re.Match) -> str:
    return "\\" + match[1]


class Adapter:
    """
    The base class for all adapter blocks.
    """

    objects: Objects

    def __init__(self, objects: Optional[Objects] = None):
        self.objects = objects or {}

    def __repr__(self):
        return f"<{type(self).__qualname__} at {hex(id(self))}>"

    def get_value(self, verb: str) -> str:
        output = self.objects.get(verb, "")
        if output is None:
            return ""

        if isinstance(output, datetime):
            return output.strftime("%Y-%m-%d %H:%M:%S")

        elif isinstance(output, Asset):
            return output.url

        elif isinstance(output, Adapter):
            return output.get_value("name")

        elif callable(output):
            return output()

        return re.sub(
            r"(?<!\\)([{():|}])",
            _sub_match,
            str(output),
        )
