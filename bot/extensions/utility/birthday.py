from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated, List, Optional, Set, TypedDict, cast

import dateparser
from asyncpg import UniqueViolationError
from discord import (
    Embed,
    Guild,
    HTTPException,
    Member,
    Message,
    Role,
    TextChannel,
    Thread,
    User,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    cooldown,
    group,
    has_permissions,
    is_owner,
    parameter,
)
from discord.ext.tasks import loop
from discord.utils import format_dt, utcnow

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters.role import StrictRole
from bot.shared.formatter import ordinal, vowel
from bot.shared.paginator import Paginator
from bot.shared.script import Script


class Record(TypedDict):
    user_id: int
    birthday: datetime


class ConfigRecord(TypedDict):
    guild_id: int
    role_id: Optional[int]
    channel_id: Optional[int]
    template: Optional[str]


class Birthday(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.birthday_role_assignment.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.birthday_role_assignment.stop()
        return await super().cog_unload()

    @loop(seconds=10)
    async def birthday_role_assignment(self) -> None:
        """Assign server based roles to users with a birthday today."""

        today = utcnow()
        yesterday = today - timedelta(days=1)

        query = """
        SELECT *
        FROM birthday.user
        WHERE EXTRACT(MONTH FROM birthday) = $1
        AND EXTRACT(DAY FROM birthday) = $2
        OR EXTRACT(DAY FROM birthday) = $3
        """
        birthdays = cast(
            List[Record],
            await self.bot.db.fetch(query, today.month, today.day, yesterday.day),
        )
        if not birthdays:
            return

        guilds: Set[Guild] = set()
        for record in birthdays:
            user = self.bot.get_user(record["user_id"])
            if user:
                guilds.update(user.mutual_guilds)

        config_query = """
        SELECT *
        FROM birthday.config
        WHERE guild_id = ANY($1::BIGINT[])
        """
        config_records = cast(
            List[ConfigRecord],
            await self.bot.db.fetch(
                config_query,
                [
                    guild.id
                    for guild in guilds
                    if guild.me.guild_permissions.administrator
                ],
            ),
        )
        config_dict = {record["guild_id"]: record for record in config_records}

        for guild in guilds:
            config = config_dict.get(guild.id)
            if not config or not config["role_id"]:
                continue

            role = guild.get_role(config["role_id"])
            channel = cast(
                Optional[TextChannel | Thread],
                guild.get_channel_or_thread(config["channel_id"] or 0),
            )
            if not role:
                continue

            for record in birthdays:
                member = guild.get_member(record["user_id"])
                if not member:
                    continue

                with suppress(HTTPException):
                    if record["birthday"].day == today.day:
                        if role not in member.roles:
                            await member.add_roles(
                                role,
                                reason="Birthday role assignment",
                            )
                            if channel:
                                script = Script(
                                    config["template"]
                                    or "Happy birthday {user.mention}, enjoy your role today 🎉🎂",
                                    [guild, channel, role, member],
                                )
                                await script.send(channel)

                    elif record["birthday"].day == yesterday.day:
                        if role in member.roles:
                            await member.remove_roles(
                                role,
                                reason="Birthday role removal",
                            )

    @group(aliases=("bday", "bd"), invoke_without_command=True)
    async def birthday(
        self,
        ctx: Context,
        *,
        member: Member = parameter(
            default=lambda ctx: ctx.author,
        ),
    ) -> Message:
        """View your or another member's birthday."""

        query = "SELECT birthday FROM birthday.user WHERE user_id = $1"
        birthday = cast(
            Optional[datetime], await self.bot.db.fetchval(query, member.id)
        )
        if not birthday:
            if member == ctx.author:
                return await ctx.warn(
                    "You have not set your birthday yet",
                    f"Use `{ctx.clean_prefix}birthday set <date>` to set it",
                )

            return await ctx.warn(f"{member.mention} hasn't set their birthday yet")

        current = utcnow()
        next_birthday = current.replace(month=birthday.month, day=birthday.day)
        if next_birthday <= current:
            next_birthday = next_birthday.replace(year=current.year + 1)

        days_until = (next_birthday - current).days

        if days_until == 0:
            phrase = "today, happy birthday! 🎊"
        elif days_until == 1:
            phrase = "tomorrow, happy early birthday! 🎊"
        else:
            phrase = f"`{next_birthday.strftime('%B')} {ordinal(next_birthday.day)}`, that's {format_dt(next_birthday, 'R')}"

        return await ctx.respond(
            f"Your birthday is {phrase}"
            if member == ctx.author
            else f"{member.mention}'s birthday is {phrase}"
        )

    @birthday.command(name="set")
    async def birthday_set(self, ctx: Context, *, date: str) -> Message:
        """Set your birthday."""

        try:
            birthday = dateparser.parse(date)
            if not birthday:
                raise ValueError
        except (ValueError, KeyError):
            return await ctx.warn("The provided date is invalid")

        await ctx.prompt(
            f"Are you sure you want to set your birthday as `{birthday:%B} {ordinal(birthday.day)}`?",
            "This is a one-time action and cannot be changed once set",
        )

        query = "INSERT INTO birthday.user (user_id, birthday) VALUES ($1, $2)"
        try:
            await self.bot.db.execute(query, ctx.author.id, birthday)
        except UniqueViolationError:
            return await ctx.warn(
                "You've already set your birthday before",
                f"Join our [support server]({self.bot.config.support.invite}) to have it changed",
            )

        return await ctx.approve(
            f"Your birthday has been set to `{birthday:%B} {ordinal(birthday.day)}`"
        )

    @birthday.command(name="remove", aliases=("delete", "del", "rm"), hidden=True)
    @is_owner()
    async def birthday_remove(self, ctx: Context, user: User) -> None:
        """Remove a user's birthday."""

        query = "DELETE FROM birthday.user WHERE user_id = $1"
        await self.bot.db.execute(query, user.id)
        return await ctx.add_check()

    @birthday.command(name="list", aliases=("all",))
    @cooldown(1, 10, BucketType.guild)
    async def birthday_list(self, ctx: Context) -> Message:
        """View all upcoming birthdays in this server."""

        query = """
        SELECT *
        FROM birthday.user
        WHERE user_id = ANY($1::BIGINT[])
        ORDER BY 
            CASE 
                WHEN (birthday - DATE_TRUNC('day', CURRENT_DATE)) < INTERVAL '0 days'
                THEN birthday + INTERVAL '1 year' - CURRENT_DATE
                ELSE birthday - CURRENT_DATE
            END
        """
        records = cast(
            List[Record],
            await self.bot.db.fetch(
                query,
                [member.id for member in ctx.guild.members],
            ),
        )
        now = utcnow()
        birthdays = [
            f"**{member}** - {birthday:%B} {ordinal(birthday.day)} "
            f"({'Today' if (birthday.month, birthday.day) == (now.month, now.day) else f'{(birthday - now).days % 365} days'})"
            for record in records
            if (member := ctx.guild.get_member(record["user_id"]))
            and (birthday := record["birthday"])
        ]
        if not birthdays:
            return await ctx.warn("No upcoming birthdays in this server")

        embed = Embed(title="Upcoming Birthdays")
        paginator = Paginator(ctx, birthdays, embed)
        return await paginator.start()

    @birthday.command(name="settings", aliases=("config", "cfg"))
    @has_permissions(manage_guild=True)
    async def birthday_settings(self, ctx: Context) -> Message:
        """View the birthday settings for this server."""

        query = "SELECT * FROM birthday.config WHERE guild_id = $1"
        config = cast(
            Optional[ConfigRecord],
            await self.bot.db.fetchrow(query, ctx.guild.id),
        )
        if not config:
            return await ctx.warn("No birthday settings have been configured")

        role = ctx.guild.get_role(config["role_id"] or 0)
        channel = cast(
            Optional[TextChannel | Thread],
            ctx.guild.get_channel_or_thread(config["channel_id"] or 0),
        )
        if not role and not channel:
            return await ctx.warn("No birthday settings have been configured")

        embed = Embed(title="Birthday Settings")
        embed.description = "\n".join(
            [
                f"**Role:** {role.mention if role else 'N/A'}",
                f"**Channel:** {channel.mention if channel else 'N/A'}",
            ]
        )
        embed.add_field(
            name="Script Template",
            value=codeblock(
                config["template"]
                or "Happy birthday {user.mention}, enjoy your role today 🎉🎂",
                "yaml",
            ),
        )

        return await ctx.send(embed=embed)

    @birthday.group(name="role", invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def birthday_role(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole(check_dangerous=True)],
    ) -> Message:
        """Set a role to be assigned to members on their birthday.

        This role will be removed once their birthday is over.
        """

        query = """
        INSERT INTO birthday.config (guild_id, role_id)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE
        SET role_id = EXCLUDED.role_id
        """
        await self.bot.db.execute(query, ctx.guild.id, role.id)
        return await ctx.approve(
            f"Now assigning {role.mention} to members on their birthday"
        )

    @birthday_role.command(name="remove", aliases=("delete", "del", "rm"), hidden=True)
    @has_permissions(manage_roles=True)
    async def birthday_role_remove(self, ctx: Context) -> Message:
        """Remove the birthday role."""

        query = "UPDATE birthday.config SET role_id = NULL WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "UPDATE 0":
            return await ctx.warn("No birthday role is set")

        return await ctx.approve("No longer assigning a role on birthdays")

    @birthday.group(name="channel", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def birthday_channel(
        self,
        ctx: Context,
        *,
        channel: TextChannel | Thread,
    ) -> Message:
        """Set a channel to send birthday messages in."""

        query = """
        INSERT INTO birthday.config (guild_id, channel_id)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE
        SET channel_id = EXCLUDED.channel_id
        """
        await self.bot.db.execute(query, ctx.guild.id, channel.id)
        return await ctx.approve(f"Now sending birthday messages in {channel.mention}")

    @birthday_channel.command(
        name="remove",
        aliases=("delete", "del", "rm"),
        hidden=True,
    )
    @has_permissions(manage_channels=True)
    async def birthday_channel_remove(self, ctx: Context) -> Message:
        """Remove the birthday message channel."""

        query = "UPDATE birthday.config SET channel_id = NULL WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "UPDATE 0":
            return await ctx.warn("No birthday message channel is set")

        return await ctx.approve("No longer sending birthday messages")

    @birthday.group(
        name="message",
        aliases=("msg", "template"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def birthday_message(self, ctx: Context, *, script: Script) -> Message:
        """Set a custom message to send on birthdays."""

        query = """
        INSERT INTO birthday.config (guild_id, template)
        VALUES ($1, $2)
        ON CONFLICT (guild_id) DO UPDATE
        SET template = EXCLUDED.template
        """
        await self.bot.db.execute(query, ctx.guild.id, script.template)
        return await ctx.approve(
            f"Now sending {vowel(script.format)} message on birthdays"
        )

    @birthday_message.command(
        name="remove",
        aliases=("delete", "del", "rm"),
        hidden=True,
    )
    @has_permissions(manage_messages=True)
    async def birthday_message_remove(self, ctx: Context) -> Message:
        """Remove the custom birthday message."""

        query = "UPDATE birthday.config SET template = NULL WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "UPDATE 0":
            return await ctx.warn("No custom birthday message is set")

        return await ctx.approve("Reset the birthday message to the default")
