import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union, cast

from discord import (
    Asset,
    Color,
    Guild,
    Member,
    Role,
    Status,
    TextChannel,
    Thread,
    User,
    VoiceChannel,
)
from humanfriendly import format_timespan
from pydantic import BaseModel

Block = Union[
    Member, User, Role, Guild, VoiceChannel, TextChannel, Thread, BaseModel, str
]
pattern = re.compile(r"(?<!\\)\{([a-zA-Z0-9_.]+)\}")


def to_dict(
    block: Block,
    _key: Optional[str] = None,
) -> Dict[str, str]:
    origin = block.__class__.__name__.lower()
    key = _key or getattr(block, "variable", origin)
    key = "user" if key == "member" else "channel" if "channel" in key else key

    data: Dict[str, str] = {key: str(block)}
    for name in dir(block):
        if name.startswith("_"):
            continue

        try:
            value = getattr(block, name)
        except (ValueError, AttributeError):
            continue

        if callable(value):
            continue

        if isinstance(value, datetime):
            data[f"{key}.{name}"] = str(int(value.timestamp()))

        elif isinstance(value, timedelta):
            data[f"{key}.{name}"] = format_timespan(value)

        elif isinstance(value, int):
            data[f"{key}.{name}"] = (
                format(value, ",")
                if not name.endswith(("id", "duration"))
                else str(value)
            )

        elif isinstance(value, (str, bool, Status, Asset, Color)):
            data[f"{key}.{name}"] = str(value)

        elif isinstance(value, BaseModel):
            base_model_data = to_dict(value)
            for __key, val in base_model_data.items():
                data[f"{key}.{__key}"] = val

    if "user.display_avatar" in data:
        data["user.avatar"] = data["user.display_avatar"]

    return data


def parse(string: str, blocks: List[Block | Tuple[str, Block]] = [], **kwargs) -> str:
    """
    Parse a string with a given environment.
    """

    blocks.extend(kwargs.items())
    string = string.replace("{embed}", "{embed:0}")
    variables: Dict[str, str] = {}
    for block in blocks:
        if isinstance(block, tuple):
            variables.update(to_dict(block[1], block[0]))
            continue

        variables.update(to_dict(block))

    def replace(match: re.Match) -> str:
        name = cast(str, match[1]).replace("author", "user").replace("member", "user")
        value = variables.get(name)

        return value or name

    return pattern.sub(replace, string)
