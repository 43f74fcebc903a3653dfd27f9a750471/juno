from __future__ import annotations

from contextlib import suppress
from typing import Optional, TypedDict, cast

from cashews import cache
from discord import Embed, Guild, HTTPException, Member
from discord.utils import utcnow
from xxhash import xxh32_hexdigest

from bot.core import Context, Juno
from bot.shared import codeblock

__all__ = ("Settings",)


class Module(TypedDict):
    threshold: int
    duration: int


class Record(TypedDict):
    guild_id: int
    managers: list[int]
    whitelist: list[int]
    bot_add: bool
    vanity: bool
    ban: Optional[Module]
    kick: Optional[Module]
    role: Optional[Module]
    channel: Optional[Module]
    webhook: Optional[Module]
    emoji: Optional[Module]


class Settings:
    bot: Juno
    guild: Guild
    record: Record

    def __init__(self, bot: Juno, guild: Guild, record: Record) -> None:
        self.bot = bot
        self.guild = guild
        self.record = dict(record)  # type: ignore

    def __repr__(self) -> str:
        return f"<Settings bot_add={self.bot_add} ban={self.ban} kick={self.kick} role={self.role} channel={self.channel} webhook={self.webhook} emoji={self.emoji}>"

    def __bool__(self) -> bool:
        return any(
            getattr(self, module)
            for module in (
                "bot_add",
                "vanity",
                "ban",
                "kick",
                "role",
                "channel",
                "webhook",
                "emoji",
            )
        )

    @property
    def managers(self) -> list[int]:
        return self.record["managers"]

    @property
    def whitelist(self) -> list[int]:
        return self.record["whitelist"]

    @property
    def bot_add(self) -> bool:
        return self.record["bot_add"]

    @property
    def vanity(self) -> bool:
        return self.record["vanity"]

    @property
    def ban(self) -> Optional[Module]:
        return self.record["ban"]

    @property
    def kick(self) -> Optional[Module]:
        return self.record["kick"]

    @property
    def role(self) -> Optional[Module]:
        return self.record["role"]

    @property
    def channel(self) -> Optional[Module]:
        return self.record["channel"]

    @property
    def webhook(self) -> Optional[Module]:
        return self.record["webhook"]

    @property
    def emoji(self) -> Optional[Module]:
        return self.record["emoji"]

    def is_manager(self, target: Context | Member) -> bool:
        member = target
        if isinstance(target, Context):
            member = target.author

        return member.id in {
            self.guild.me.id,
            self.guild.owner_id,
            *self.managers,
            *self.bot.config.owner_ids,
        }

    def is_whitelisted(self, target: Context | Member) -> bool:
        member = target
        if isinstance(target, Context):
            member = target.author

        return self.is_manager(member) or member.id in self.whitelist

    @staticmethod
    def _default_config() -> Record:
        return {
            "guild_id": 0,
            "managers": [],
            "whitelist": [],
            "bot_add": False,
            "vanity": False,
            "ban": None,
            "kick": None,
            "role": None,
            "channel": None,
            "webhook": None,
            "emoji": None,
        }

    async def exceeds_threshold(self, module: str, member: Member) -> bool:
        if self.is_whitelisted(member):
            return False

        module_config: Optional[Module] = getattr(self, module)
        if module_config is None:
            return False

        key = "antinuke_" + xxh32_hexdigest(f"{module}:{self.guild.id}-{member.id}")
        pipe = self.bot.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, module_config["duration"])
        value, _ = cast(
            tuple[int, int],
            await pipe.execute(),
        )

        return value > module_config["threshold"]

    async def dispatch_log(
        self,
        member: Member,
        module: str,
        *,
        description: Optional[str] = None,
        details: Optional[str] = None,
        elapsed: float = 0.0,
        failure: bool = False,
    ) -> None:
        if not self.guild.owner:
            return

        module_config: Optional[Module] = getattr(self, module)
        if module_config is None:
            return

        key = f"antinuke_log:{self.guild.id}.{module}"
        locked = await self.bot.redis.get(key)
        if locked:
            return

        await self.bot.redis.setex(key, 1, 20)
        embed = Embed(title="Antinuke Notification", timestamp=utcnow())
        embed.set_footer(text=f"Resolved in {elapsed:.2f}s")
        embed.set_author(
            url=self.guild.vanity_url,
            name=self.guild.name,
            icon_url=self.guild.icon,
        )
        embed.add_field(
            name="Perpetrator",
            value=f"{member} [`{member.id}`]",
        )

        embed.description = description or (
            f"**{member}** has triggered the {module} module"
            if module != "bot_add"
            else f"**{member}** has added a bot to the server"
        )
        if failure:
            embed.description += "\n> **FAILED TO PUNISH THE PERPETRATOR**"

        if details:
            embed.add_field(
                name="Details",
                value=">>> " + codeblock(details),
                inline=False,
            )

        with suppress(HTTPException):
            await self.guild.owner.send(embed=embed)

    @classmethod
    @cache(ttl="30m", key="antinuke.settings:{guild.id}")
    async def fetch(cls, bot: Juno, guild: Guild) -> Settings:
        query = "SELECT * FROM antinuke WHERE guild_id = $1"
        record = (
            cast(Optional[Record], await bot.db.fetchrow(query, guild.id))
            or cls._default_config()
        )

        return cls(bot, guild, record)

    async def upsert(self, revalidate: bool = True, **kwargs) -> Settings:
        query = """
        INSERT INTO antinuke (
            guild_id,
            managers,
            whitelist,
            bot_add,
            vanity,
            ban,
            kick,
            role,
            channel,
            webhook,
            emoji
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            managers = EXCLUDED.managers,
            whitelist = EXCLUDED.whitelist,
            bot_add = EXCLUDED.bot_add,
            vanity = EXCLUDED.vanity,
            ban = EXCLUDED.ban,
            kick = EXCLUDED.kick,
            role = EXCLUDED.role,
            channel = EXCLUDED.channel,
            webhook = EXCLUDED.webhook,
            emoji = EXCLUDED.emoji
        RETURNING *
        """

        record = await self.bot.db.fetchrow(
            query,
            self.guild.id,
            kwargs.get("managers", self.managers),
            kwargs.get("whitelist", self.whitelist),
            kwargs.get("bot_add", self.bot_add),
            kwargs.get("vanity", self.vanity),
            kwargs.get("ban", self.ban),
            kwargs.get("kick", self.kick),
            kwargs.get("role", self.role),
            kwargs.get("channel", self.channel),
            kwargs.get("webhook", self.webhook),
            kwargs.get("emoji", self.emoji),
        )
        self.record = dict(record)  # type: ignore
        if revalidate:
            await cache.delete(f"antinuke.settings:{self.guild.id}")

        return self
