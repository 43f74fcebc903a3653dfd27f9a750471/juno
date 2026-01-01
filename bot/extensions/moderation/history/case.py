from __future__ import annotations

import asyncio
import re
from datetime import datetime
from enum import StrEnum, auto
from typing import Literal, Optional, TypedDict, cast

from discord import ClientUser, Embed, Guild, Member, Role, Thread, User
from discord.abc import GuildChannel
from discord.utils import format_dt

from bot.core import Context, Juno

from ...config.logging import LogType, log

TargetType = Guild | Role | GuildChannel | Thread | Member | User


class Action(StrEnum):
    UNKNOWN = auto()
    KICK = auto()
    BAN = auto()
    HACKBAN = auto()
    SOFTBAN = auto()
    UNBAN = auto()
    UNBAN_ALL = auto()
    MUTE = auto()
    UNMUTE = auto()
    JAIL = auto()
    UNJAIL = auto()
    TIMEOUT = auto()
    UNTIMEOUT = auto()
    UNTIMEOUT_ALL = auto()
    LOCKDOWN = auto()
    UNLOCKDOWN = auto()
    SERVER_LOCKDOWN = auto()
    SERVER_UNLOCKDOWN = auto()
    NUKE = auto()
    SLOWMODE = auto()
    SLOWMODE_DISABLE = auto()

    def __str__(self) -> str:
        if self == Action.KICK:
            return "Kicked"

        elif self == Action.BAN:
            return "Banned"

        elif self == Action.HACKBAN:
            return "Hack Banned"

        elif self == Action.SOFTBAN:
            return "Soft Banned"

        elif self == Action.UNBAN:
            return "Unbanned"

        elif self == Action.UNBAN_ALL:
            return "Unbanned All"

        elif self == Action.MUTE:
            return "Muted"

        elif self == Action.UNMUTE:
            return "Unmuted"

        elif self == Action.JAIL:
            return "Jailed"

        elif self == Action.UNJAIL:
            return "Unjailed"

        elif self == Action.TIMEOUT:
            return "Timed Out"

        elif self == Action.UNTIMEOUT:
            return "Timeout Lifted"

        elif self == Action.UNTIMEOUT_ALL:
            return "All Timeouts Lifted"

        elif self == Action.LOCKDOWN:
            return "Locked Down"

        elif self == Action.UNLOCKDOWN:
            return "Lockdown Lifted"

        elif self == Action.SERVER_LOCKDOWN:
            return "Server Locked Down"

        elif self == Action.SERVER_UNLOCKDOWN:
            return "Server Lockdown Lifted"

        elif self == Action.NUKE:
            return "Channel Nuked"

        elif self == Action.SLOWMODE:
            return "Slowmode Enabled"

        elif self == Action.SLOWMODE_DISABLE:
            return "Slowmode Disabled"

        else:
            return "Unknown"


class Record(TypedDict):
    id: int
    guild_id: int
    target_id: int
    target_type: Literal["guild", "role", "channel", "member", "user"]
    moderator_id: int
    message_id: Optional[int]
    reason: str
    action: int
    action_expiration: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]


class Case:
    def __init__(self, bot: Juno, record: Record) -> None:
        self.bot = bot
        self.record = record

    @property
    def id(self) -> int:
        return self.record["id"]

    @property
    def guild(self) -> Guild:
        return self.bot.get_guild(self.record["guild_id"])  # type: ignore

    @property
    def action(self) -> Action:
        return Action(self.record["action"])

    @property
    def partial_moderator(self) -> Optional[User]:
        return self.bot.get_user(self.record["moderator_id"])

    async def moderator(self) -> User:
        return await self.bot.get_or_fetch_user(self.record["moderator_id"])

    async def target(self, partial: bool = False) -> Optional[TargetType]:
        target_id = self.record["target_id"]
        target_type = self.record["target_type"]

        if target_type == "guild":
            return self.guild

        elif target_type == "role":
            return self.guild.get_role(target_id)

        elif target_type == "channel":
            return self.guild.get_channel_or_thread(target_id)

        elif target_type in ("member", "user"):
            if partial:
                return self.bot.get_user(target_id)

            return await self.bot.get_or_fetch_user(target_id)

    async def embed(self) -> Embed:
        target, moderator = await asyncio.gather(self.target(), self.moderator())

        embed = Embed()
        embed.set_author(
            name=f"{moderator} [{moderator.id}]",
            icon_url=moderator.display_avatar,
        )

        information = (
            f"{format_dt(self.record['created_at'])} ({format_dt(self.record['created_at'], 'R')})"
            f"\n>>> **{self.record['target_type'].title()}:** {target or 'Unknown'} [`{self.record['target_id']}`]\n"
        )

        if self.record["action_expiration"]:
            information += f"**Expiration:** {format_dt(self.record['action_expiration'])} ({format_dt(self.record['action_expiration'], 'R')})\n"

        if self.record["updated_at"]:
            information += f"**Updated:** {format_dt(self.record['updated_at'])} ({format_dt(self.record['updated_at'], 'R')})\n"

        embed.add_field(
            name=f"Case #{self.id} | {self.action}",
            value=information + f"**Reason:** {self.record['reason']}\n",
        )

        return embed

    @classmethod
    async def create(
        cls,
        ctx: Context | Guild,
        target: TargetType,
        action: Action = Action.UNKNOWN,
        reason: str = "No reason provided",
        *,
        moderator: Optional[Member | User | ClientUser] = None,
        action_expiration: Optional[datetime] = None,
    ) -> Case:
        guild = ctx.guild if isinstance(ctx, Context) else ctx
        bot = cast(Juno, guild._state._get_client())
        if not moderator and isinstance(ctx, Context):
            moderator = ctx.author
        else:
            moderator = bot.user

        query = """
        INSERT INTO moderation.case (
            id,
            guild_id,
            target_id,
            target_type,
            moderator_id,
            reason,
            action,
            action_expiration
        ) VALUES (NEXT_CASE($1), $1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """
        record = cast(
            Record,
            await bot.db.fetchrow(
                query,
                guild.id,
                target.id,
                re.sub(r"^.*?(?=channel)|$", "", target.__class__.__name__.lower()),
                moderator.id,
                reason,
                action,
                action_expiration,
            ),
        )
        case = cls(bot, record)
        bot.loop.create_task(log(LogType.MODERATION, guild, case=case))
        return case

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Case:
        if not (match := re.match(r"^#?(\d+)$", argument)):
            raise ValueError("You must provide a valid case ID, e.g. `#27`")

        case_id = int(match[1])
        query = "SELECT * FROM moderation.case WHERE guild_id = $1 AND id = $2"
        record = cast(
            Optional[Record],
            await ctx.bot.db.fetchrow(query, ctx.guild.id, case_id),
        )
        if not record:
            raise ValueError(f"Case ID `#{case_id}` does not exist")

        return cls(ctx.bot, record)

    @classmethod
    async def most_recent(cls, ctx: Context) -> Case:
        query = (
            "SELECT * FROM moderation.case WHERE guild_id = $1 ORDER BY id DESC LIMIT 1"
        )
        record = cast(Optional[Record], await ctx.bot.db.fetchrow(query, ctx.guild.id))
        if not record:
            raise ValueError("No cases have been logged in this server")

        return cls(ctx.bot, record)
