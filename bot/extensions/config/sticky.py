import asyncio
from typing import Optional, TypedDict, cast

from discord import Embed, HTTPException, Message, TextChannel, Thread
from discord.ext.commands import Cog, group, has_permissions
from discord.utils import utcnow
from xxhash import xxh32_intdigest

from bot.core import Context, Juno
from bot.shared import Paginator, Script, codeblock, quietly_delete
from bot.shared.formatter import vowel


class Record(TypedDict):
    guild_id: int
    channel_id: int
    message_id: int
    template: str


class Sticky(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_message")
    async def sticky_listener(self, message: Message) -> None:
        """Stick a message to the bottom of the channel."""

        if (
            not message.guild
            or not isinstance(message.channel, (TextChannel, Thread))
        ):
            return

        query = "SELECT * FROM sticky WHERE guild_id = $1 AND channel_id = $2"
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, message.guild.id, message.channel.id),
        )
        if not record:
            return
        
        elif record["message_id"] == message.id:
            return

        key = f"sticky:{xxh32_intdigest(f'{message.guild.id}:{message.channel.id}')}"
        locked = await self.bot.redis.get(key)
        if locked:
            return

        await self.bot.redis.set(key, 1, 6)
        last_message = message.channel.get_partial_message(record["message_id"])
        time_since = utcnow() - last_message.created_at
        time_to_wait = 6 - time_since.total_seconds()
        if time_to_wait > 1:
            await asyncio.sleep(time_to_wait)

        script = Script(
            record["template"],
            [message.guild, message.channel, message.author],
        )

        try:
            new_message = await script.send(message.channel)
        except HTTPException:
            query = "DELETE FROM sticky WHERE guild_id = $1 AND channel_id = $2"
            await self.bot.db.execute(query, message.guild.id, message.channel.id)
        else:
            query = "UPDATE sticky SET message_id = $3 WHERE guild_id = $1 AND channel_id = $2"
            await self.bot.db.execute(
                query,
                message.guild.id,
                message.channel.id,
                new_message.id,
            )
        finally:
            await self.bot.redis.delete(key)
            await quietly_delete(last_message)

    @group(aliases=("stickymessage", "stickymsg", "sm"), invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def sticky(self, ctx: Context) -> None:
        """Stick a message to the bottom of the channel."""

        return await ctx.send_help(ctx.command)

    @sticky.command(name="add", aliases=("create",))
    @has_permissions(manage_messages=True)
    async def sticky_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        script: Script,
    ) -> Message:
        """Add a sticky message to a channel."""

        message = await script.send(channel)
        await self.bot.db.execute(
            """
            INSERT INTO sticky (
                guild_id,
                channel_id,
                message_id,
                template
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET
                message_id = EXCLUDED.message_id,
                template = EXCLUDED.template
            """,
            ctx.guild.id,
            channel.id,
            message.id,
            script.template,
        )

        return await ctx.approve(
            f"Added {vowel(script.format)} sticky message to {channel.mention}"
        )

    @sticky.command(name="existing", aliases=("from",))
    @has_permissions(manage_messages=True)
    async def sticky_existing(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        message: Message,
    ) -> Message:
        """Add a sticky message from an existing message."""

        script = Script.from_message(message)
        if not script:
            return await ctx.warn(
                f"That [`message`]({message.jump_url}) doesn't have any content"
            )

        return await self.sticky_add(ctx, channel, script=script)

    @sticky.command(name="update", aliases=("edit",))
    @has_permissions(manage_messages=True)
    async def sticky_update(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        script: Script,
    ) -> Message:
        """Update an existing sticky message."""

        query = "SELECT message_id FROM sticky WHERE guild_id = $1 AND channel_id = $2"
        message_id = cast(
            Optional[int],
            await self.bot.db.fetchval(query, ctx.guild.id, channel.id),
        )
        if not message_id:
            return await self.sticky_add(ctx, channel, script=script)

        message = channel.get_partial_message(message_id)
        await quietly_delete(message)

        message = await script.send(channel)
        await self.bot.db.execute(
            """
            UPDATE sticky SET
                message_id = $3,
                template = $4
            WHERE guild_id = $1
            """,
            ctx.guild.id,
            channel.id,
            script.template,
        )

        return await ctx.approve(f"Updated sticky message in {channel.mention}")

    @sticky.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    async def sticky_remove(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """Remove an existing sticky message."""

        query = """
            DELETE FROM sticky
            WHERE guild_id = $1
            AND channel_id = $2
            RETURNING message_id
        """
        message_id = cast(
            Optional[int],
            await self.bot.db.fetchval(query, ctx.guild.id, channel.id),
        )
        if not message_id:
            return await ctx.warn(f"No sticky message was found for {channel.mention}")

        message = channel.get_partial_message(message_id)
        await quietly_delete(message)

        return await ctx.approve(f"Removed the sticky message from {channel.mention}")

    @sticky.command(name="view", aliases=("script", "template"))
    @has_permissions(manage_messages=True)
    async def sticky_view(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """View the sticky message for a channel."""

        query = "SELECT template FROM sticky WHERE channel_id = $1"
        template = cast(Optional[str], await self.bot.db.fetchval(query, channel.id))
        if not template:
            return await ctx.warn(f"No sticky message was found for {channel.mention}")

        script = Script(template, [ctx.guild, channel])
        await ctx.reply(codeblock(script.template, "yaml"))
        return await script.send(ctx.channel)

    @sticky.command(name="list")
    @has_permissions(manage_messages=True)
    async def sticky_list(self, ctx: Context) -> Message:
        """View all channels with a sticky message."""

        query = "SELECT * FROM sticky WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`MESSAGE`]({message.jump_url})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
            and isinstance(channel, (TextChannel, Thread))
            and (message := channel.get_partial_message(record["message_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels are receving sticky messages")

        embed = Embed(title="Sticky Messages")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
