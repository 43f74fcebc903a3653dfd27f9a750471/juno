from typing import List, Optional, TypedDict, cast

from discord import Embed, HTTPException, Member, Message, TextChannel, Thread
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from humanfriendly import format_timespan
from xxhash import xxh32_intdigest

from bot.core import Context, Juno
from bot.shared import Paginator, Script, codeblock
from bot.shared.converters import FlagConverter
from bot.shared.formatter import vowel

from .notify import notify_failure

class RejoinFlags(FlagConverter):
    delete_after: Range[int, 3, 360] = flag(
        aliases=["self_destruct"],
        description="Queue the rejoin message for deletion.",
        default=0,
    )


class Record(TypedDict):
    guild_id: int
    channel_id: int
    template: str
    delete_after: Optional[int]


def rejoin_key(guild_id: int) -> str:
    return f"rejoin:{guild_id}"

def welcome_key(guild_id: int, channel_id: int) -> str:
    key = xxh32_intdigest(f"{guild_id}:{channel_id}")
    return f"welcome:{key}"

class Rejoin(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    async def rejoin_dispatch(self, records: List[Record], member: Member) -> None:
        guild = member.guild
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
                await notify_failure("rejoin", member, channel, script, exc)
                scheduled_deletion.append(record)
            else:
                if record["delete_after"]:
                    await message.delete(delay=record["delete_after"])
                else:
                    published.append(message)

        if scheduled_deletion:
            await self.bot.db.executemany(
                """
                DELETE FROM system.rejoin
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

    @Cog.listener("on_member_remove")
    async def rejoin_watcher(self, member: Member) -> None:
        """Add the member to the removal keys."""

        await self.bot.redis.sadd(
            rejoin_key(member.guild.id),
            str(member.id),
            ex=3_000,
        )

    @group(aliases=("rejoins",), invoke_without_command=True)
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin(self, ctx: Context) -> Message:
        """Automatically greet new members when they rejoin.

        Rejoin messages are sent when a member leaves and rejoins the server.
        You can configure up to 3 channels to receive rejoin messages."""

        return await ctx.send_help(ctx.command)

    @rejoin.command(name="add", aliases=("create",), extras={"flags": RejoinFlags})
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        script: Script,
    ) -> Message:
        """Add a channel to receive rejoin messages."""

        script.template, flags = await RejoinFlags().find(ctx, script.template)
        if not script:
            return await ctx.send_help(ctx.command)

        query = "SELECT * FROM system.rejoin WHERE guild_id = $1"
        records: List[Record] = [
            record
            for record in await self.bot.db.fetch(query, ctx.guild.id)
            if ctx.guild.get_channel_or_thread(record["channel_id"])
        ]
        if len(records) >= 3:
            return await ctx.warn("You can only have up to 3 rejoin messages")

        status = await self.bot.db.execute(
            """
            INSERT INTO system.rejoin (
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
                f"Added {vowel(script.format)} rejoin message to {channel.mention}"
                if status.startswith("INSERT 0 1")
                else f"Updated the rejoin message for {channel.mention}"
            ),
            (
                f"The message will be deleted after `{format_timespan(flags.delete_after)}`"
                if flags.delete_after
                else ""
            ),
        )

    @rejoin.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin_remove(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """Remove an existing rejoin message."""

        query = "DELETE FROM system.rejoin WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn(f"No rejoin message was found for {channel.mention}")

        return await ctx.approve(
            f"No longer sending rejoin messages to {channel.mention}"
        )

    @rejoin.command(name="view", aliases=("script", "template"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin_view(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """View the rejoin message for a channel."""

        query = "SELECT template FROM system.rejoin WHERE channel_id = $1"
        template = cast(Optional[str], await self.bot.db.fetchval(query, channel.id))
        if not template:
            return await ctx.warn(f"No rejoin message was found for {channel.mention}")

        script = Script(template, [ctx.guild, channel, ctx.author])
        await ctx.reply(codeblock(script.template, "yaml"))
        return await script.send(ctx.channel)

    @rejoin.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin_clear(self, ctx: Context) -> Message:
        """Remove all rejoin messages."""

        query = "DELETE FROM system.rejoin WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No channels are receiving rejoin messages")

        return await ctx.approve("No longer sending rejoin messages")

    @rejoin.command(name="list")
    @has_permissions(manage_channels=True, manage_messages=True)
    async def rejoin_list(self, ctx: Context) -> Message:
        """View all channels receiving rejoin messages."""

        query = "SELECT * FROM system.rejoin WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`{script.format.upper()}`]"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
            and (script := Script(record["template"]))
        ]
        if not channels:
            return await ctx.warn("No channels are receiving rejoin messages")

        embed = Embed(title="Rejoin Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
