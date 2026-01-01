import re
from secrets import token_urlsafe
from typing import Self

from discord.ext.commands import Converter
from discord.ext.commands import FlagConverter as OriginalFlagConverter

from bot.core import Context
from config import config
from .attachment import PartialAttachment
from .role import FuzzyRole, StrictRole
from .time import Duration
from .user import HierarchyMember, StrictMember, StrictUser

__all__ = (
    "StrictMember",
    "StrictUser",
    "HierarchyMember",
    "FuzzyRole",
    "StrictRole",
    "PartialAttachment",
    "FlagConverter",
    "Duration",
    "Status",
    "SafeText",
    "Identifier",
)


class FlagConverter(
    OriginalFlagConverter,
    case_insensitive=True,
    prefix="--",
    delimiter=" ",
):
    @property
    def values(self):
        return self.get_flags().values()

    async def convert(self, ctx: Context, argument: str):
        argument = argument.replace("—", "--")
        return await super().convert(ctx, argument)

    async def find(
        self,
        ctx: Context,
        argument: str,
        *,
        remove: bool = True,
    ) -> tuple[str, Self]:
        argument = argument.replace("—", "--")
        flags = await self.convert(ctx, argument)

        if remove:
            for key, values in flags.parse_flags(argument).items():
                aliases = getattr(self.get_flags().get(key), "aliases", [])
                for _key in aliases:
                    argument = argument.replace(f"--{_key} {' '.join(values)}", "")

                argument = argument.replace(f"--{key} {' '.join(values)}", "")

        return argument.strip(), flags


class Status(Converter[bool]):
    async def convert(self, ctx: Context, argument: str) -> bool:
        return argument.lower() in {"enable", "yes", "on", "true"}


class SafeText(Converter[str]):
    async def convert(self, ctx: Context, argument: str) -> str:
        if ctx.author.guild_permissions.manage_messages:
            return argument

        UNSAFE_PATTERNS = [
            r"(?:(?:https?://)?(?:www)?discord(?:app)?\.(?:(?:com|gg)/invite/[a-z0-9-_]+)|(?:https?://)?(?:www)?discord\.gg/[a-z0-9-_]+)",
            r"(https?://\S+)",
        ]
        if any(re.search(pattern, argument) for pattern in UNSAFE_PATTERNS):
            raise ValueError("stop being weird")

        return argument
    
class Identifier:
    id: str

    def __init__(self, id: str) -> None:
        self.id = id

    def __str__(self) -> str:
        return f"[*`{self.id}`*]({config.backend.public_url}/{self.id})"

    def __repr__(self) -> str:
        return f"<Identifier id={self.id}>"

    def __eq__(self, other: str) -> bool:
        return str(other) == self.id

    @classmethod
    def create(cls) -> Self:
        return cls(id=token_urlsafe(4))

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        if argument.startswith(config.backend.public_url):
            argument = argument.split("/")[-1]

        if len(argument) > 8:
            raise ValueError("The provided identifier isn't valid")

        return cls(id=argument)
