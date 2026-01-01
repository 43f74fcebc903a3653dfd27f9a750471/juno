from typing import Any

from . import Adapter


class SafeObjectAdapter(Adapter):
    """
    The base class safe objects.
    """

    object: Any

    def __init__(self, base: dict[str, str]):
        super().__init__()

    def get_value(self, verb: str) -> str:
        if verb.startswith("_") or "." in verb:
            return ""

        try:
            attribute = getattr(self.object, verb)
        except AttributeError:
            return ""

        if callable(attribute):
            return ""

        elif isinstance(attribute, float):
            return str(int(attribute))

        return str(attribute)
