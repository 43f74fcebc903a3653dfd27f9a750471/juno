from contextlib import suppress
from typing import List, Optional, TypedDict, cast

from discord import Embed, HTTPException, Member, Message, TextChannel, Thread
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared import Paginator, Script, codeblock
from bot.shared.converters import FlagConverter
from bot.shared.formatter import vowel

from .notify import notify_failure


class BoostFlags(FlagConverter):
    delete_after: Range[int, 3, 360] = flag(
        aliases=["self_destruct"],
        description="Queue the boost message for deletion.",
        default=0,
    )


class Record(TypedDict):
    guild_id: int
    channel_id: int
    template: str
    delete_after: Optional[int]


class Boost(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_member_boost")
    async def boost_listener(self, member: Member) -> None:
        guild = member.guild
        query = "SELECT * FROM system.boost WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, guild.id))

        scheduled_deletion: List[Record] = []
        for record in records:
            channel = guild.get_channel_or_thread(record["channel_id"])
            if not isinstance(channel, (TextChannel, Thread)):
                continue

            script = Script(record["template"], [guild, channel, member])
            try:
                message = await script.send(channel)
            except HTTPException as exc:
                await notify_failure("boost", member, channel, script, exc)
                scheduled_deletion.append(record)
            else:
                if record["delete_after"]:
                    await message.delete(delay=record["delete_after"])

        if scheduled_deletion:
            await self.bot.db.executemany(
                """
                DELETE FROM system.boost
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                [(guild.id, record["channel_id"]) for record in scheduled_deletion],
            )

    @Cog.listener("on_system_boost")
    async def boost_system_listener(self, message: Message) -> None:
        """Delete system boost messages if enabled."""

        if not message.guild:
            return

        query = "SELECT system_boost_removal FROM settings WHERE guild_id = $1"
        system_boost_removal = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, message.guild.id),
        )
        if not system_boost_removal:
            return

        with suppress(HTTPException):
            await message.delete()

    @group(invoke_without_command=True)
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost(self, ctx: Context) -> Message:
        """Automatically send a boost message when a member boosts.

        Boost messages are sent when a member boosts the server.
        You can configure up to 3 channels to receive boost messages."""

        return await ctx.send_help(ctx.command)

    @boost.command(name="add", aliases=("create",), extras={"flags": BoostFlags})
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        script: Script,
    ) -> Message:
        """Add a channel to receive boost messages."""

        script.template, flags = await BoostFlags().find(ctx, script.template)
        if not script:
            return await ctx.send_help(ctx.command)

        query = "SELECT * FROM system.boost WHERE guild_id = $1"
        records: List[Record] = [
            record
            for record in await self.bot.db.fetch(query, ctx.guild.id)
            if ctx.guild.get_channel_or_thread(record["channel_id"])
        ]
        if len(records) >= 3:
            return await ctx.warn("You can only have up to 3 boost messages")

        status = await self.bot.db.execute(
            """
            INSERT INTO system.boost (
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
                f"Added {vowel(script.format)} boost message to {channel.mention}"
                if status.startswith("INSERT")
                else f"Updated the boost message for {channel.mention}"
            ),
            (
                f"The message will be deleted after `{format_timespan(flags.delete_after)}`"
                if flags.delete_after
                else ""
            ),
        )

    @boost.command(name="removal", aliases=("system", "deletion"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_removal(self, ctx: Context) -> Message:
        """Toggle deletion of system boost messages."""

        status = not ctx.settings.record["system_boost_removal"]
        await ctx.settings.upsert(system_boost_removal=status)
        return await ctx.approve(
            f"System boost messages will {'now' if status else 'no longer'} be deleted automatically"
        )

    @boost.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_remove(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """Remove an existing boost message."""

        query = "DELETE FROM system.boost WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn(f"No boost message was found for {channel.mention}")

        return await ctx.approve(
            f"No longer sending boost messages to {channel.mention}"
        )

    @boost.command(name="view", aliases=("script", "template"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_view(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """View the boost message for a channel."""

        query = "SELECT template FROM system.boost WHERE channel_id = $1"
        template = cast(Optional[str], await self.bot.db.fetchval(query, channel.id))
        if not template:
            return await ctx.warn(f"No boost message was found for {channel.mention}")

        script = Script(template, [ctx.guild, channel, ctx.author])
        await ctx.reply(codeblock(script.template, "yaml"))
        return await script.send(ctx.channel)

    @boost.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_clear(self, ctx: Context) -> Message:
        """Remove all boost messages."""

        query = "DELETE FROM system.boost WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No channels are receiving boost messages")

        return await ctx.approve("No longer sending boost messages")

    @boost.command(name="list")
    @has_permissions(manage_channels=True, manage_messages=True)
    async def boost_list(self, ctx: Context) -> Message:
        """View all channels receiving boost messages."""

        query = "SELECT * FROM system.boost WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`{script.format.upper()}`]"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
            and (script := Script(record["template"]))
        ]
        if not channels:
            return await ctx.warn("No channels are receiving boost messages")

        embed = Embed(title="Boost Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
