from contextlib import suppress
from itertools import groupby
from typing import List, Optional, TypedDict, cast

from discord import (
    Embed,
    HTTPException,
    Member,
    Message,
    MessageType,
    PartialMessage,
    TextChannel,
    Thread,
)
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from humanfriendly import format_timespan
from xxhash import xxh32_intdigest

from bot.core import Context, Juno
from bot.shared import Paginator, Script, codeblock
from bot.shared.converters import FlagConverter
from bot.shared.formatter import vowel

from .notify import notify_failure
from .rejoin import Record as RejoinRecord
from .rejoin import Rejoin, rejoin_key


class WelcomeFlags(FlagConverter):
    delete_after: Range[int, 3, 360] = flag(
        aliases=["self_destruct"],
        description="Queue the welcome message for deletion.",
        default=0,
    )


class Record(TypedDict):
    guild_id: int
    channel_id: int
    template: str
    delete_after: Optional[int]


def welcome_key(guild_id: int, channel_id: int) -> str:
    key = xxh32_intdigest(f"{guild_id}:{channel_id}")
    return f"welcome:{key}"


class Welcome(Rejoin, Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_member_join")
    async def welcome_listener(self, member: Member) -> None:
        guild = member.guild
        if await self.bot.redis.sismember(rejoin_key(guild.id), str(member.id)):
            query = "SELECT * FROM system.rejoin WHERE guild_id = $1"
            records = cast(List[RejoinRecord], await self.bot.db.fetch(query, guild.id))
            if records:
                return await self.rejoin_dispatch(records, member)

        query = "SELECT * FROM system.welcome WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, guild.id))
        if not records:
            return
        
        ratelimited = await self.bot.redis.ratelimited(f"welcome:{guild.id}", 5, 30)
        if ratelimited:
            return
        
        published: List[Message] = []
        scheduled_deletion: List[Record] = []
        for record in records:
            channel = guild.get_channel_or_thread(record["channel_id"])
            if not isinstance(channel, (TextChannel, Thread)):
                continue

            script = Script(record["template"], [guild, channel, member])
            try:
                message = await script.send(channel)
            except HTTPException as exc:
                await notify_failure("welcome", member, channel, script, exc)
                scheduled_deletion.append(record)
            else:
                if record["delete_after"]:
                    await message.delete(delay=record["delete_after"])
                else:
                    published.append(message)

        if scheduled_deletion:
            await self.bot.db.executemany(
                """
                DELETE FROM system.welcome
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                [(guild.id, record["channel_id"]) for record in scheduled_deletion],
            )

        if published:
            await self.bot.redis.sadd(
                welcome_key(guild.id, member.id),
                *[f"{message.channel.id}:{message.id}" for message in published],
                ex=3_000,
            )

    @Cog.listener("on_message")
    async def welcome_system(self, message: Message) -> None:
        """Add the system welcome message to the welcome cache."""

        if message.type != MessageType.new_member:
            return

        elif not isinstance(message.author, Member):
            return

        elif not message.guild:
            return

        await self.bot.redis.sadd(
            welcome_key(message.guild.id, message.author.id),
            f"{message.channel.id}:{message.id}",
            ex=3_000,
        )

    @Cog.listener("on_member_remove")
    async def welcome_removal_listener(self, member: Member) -> None:
        """Remove welcome messages when a member leaves."""

        guild = member.guild
        key = welcome_key(guild.id, member.id)
        identifiers = await self.bot.redis.smembers(key)
        if not identifiers:
            return

        query = "SELECT welcome_removal FROM settings WHERE guild_id = $1"
        welcome_removal = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, guild.id),
        )
        if not welcome_removal:
            return

        partial_messages: List[PartialMessage] = []
        for identifier in identifiers:
            channel_id, message_id = identifier.split(":")
            channel = guild.get_channel_or_thread(int(channel_id))
            if not isinstance(channel, (TextChannel, Thread)):
                continue

            message = channel.get_partial_message(int(message_id))
            partial_messages.append(message)

        for channel, messages in groupby(
            partial_messages,
            lambda message: message.channel,
        ):
            if not isinstance(channel, (TextChannel, Thread)):
                continue

            with suppress(HTTPException):
                await channel.delete_messages(messages)

    @group(aliases=("welc", "hey"), invoke_without_command=True)
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome(self, ctx: Context) -> Message:
        """Automatically greet new members when they join.

        Welcome messages are sent when a member joins the server.
        You can configure up to 3 channels to receive welcome messages."""

        return await ctx.send_help(ctx.command)

    @welcome.command(name="add", aliases=("create",), extras={"flags": WelcomeFlags})
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        script: Script,
    ) -> Message:
        """Add a channel to receive welcome messages."""

        script.template, flags = await WelcomeFlags().find(ctx, script.template)
        if not script:
            return await ctx.send_help(ctx.command)

        query = "SELECT * FROM system.welcome WHERE guild_id = $1"
        records: List[Record] = [
            record
            for record in await self.bot.db.fetch(query, ctx.guild.id)
            if ctx.guild.get_channel_or_thread(record["channel_id"])
        ]
        if len(records) >= 3:
            return await ctx.warn("You can only have up to 3 welcome messages")

        status = await self.bot.db.execute(
            """
            INSERT INTO system.welcome (
                guild_id,
                channel_id,
                template,
                delete_after
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET
                template = EXCLUDED.template,
                delete_after = EXCLUDED.delete_after
            """,
            ctx.guild.id,
            channel.id,
            script.template,
            flags.delete_after,
        )

        return await ctx.approve(
            (
                f"Added {vowel(script.format)} welcome message to {channel.mention}"
                if status.startswith("INSERT 0 1")
                else f"Updated the welcome message for {channel.mention}"
            ),
            (
                f"The message will be deleted after `{format_timespan(flags.delete_after)}`"
                if flags.delete_after
                else ""
            ),
        )

    @welcome.command(name="removal", aliases=("later", "deletion"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_removal(self, ctx: Context) -> Message:
        """Toggle deletion of welcome messages on member removal."""

        status = not ctx.settings.record["welcome_removal"]
        await ctx.settings.upsert(welcome_removal=status)
        return await ctx.approve(
            f"Welcome messages will {'now' if status else 'no longer'} be deleted when a member leaves"
        )

    @welcome.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_remove(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """Remove an existing welcome message."""

        query = "DELETE FROM system.welcome WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn(f"No welcome message was found for {channel.mention}")

        return await ctx.approve(
            f"No longer sending welcome messages to {channel.mention}"
        )

    @welcome.command(name="view", aliases=("script", "template"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_view(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """View the welcome message for a channel."""

        query = "SELECT template FROM system.welcome WHERE channel_id = $1"
        template = cast(Optional[str], await self.bot.db.fetchval(query, channel.id))
        if not template:
            return await ctx.warn(f"No welcome message was found for {channel.mention}")

        script = Script(template, [ctx.guild, channel, ctx.author])
        await ctx.reply(codeblock(script.template, "yaml"))
        return await script.send(ctx.channel)

    @welcome.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_clear(self, ctx: Context) -> Message:
        """Remove all welcome messages."""

        query = "DELETE FROM system.welcome WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No channels are receiving welcome messages")

        return await ctx.approve("No longer sending welcome messages")

    @welcome.command(name="list")
    @has_permissions(manage_channels=True, manage_messages=True)
    async def welcome_list(self, ctx: Context) -> Message:
        """View all channels receiving welcome messages."""

        query = "SELECT * FROM system.welcome WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`{script.format.upper()}`]"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
            and (script := Script(record["template"]))
        ]
        if not channels:
            return await ctx.warn("No channels are receiving welcome messages")

        embed = Embed(title="Welcome Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
