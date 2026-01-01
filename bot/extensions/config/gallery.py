import asyncio
import re
from contextlib import suppress
from typing import List, Optional, TypedDict, cast

from asyncpg import UniqueViolationError
from discord import Embed, HTTPException, Message, TextChannel, Thread
from discord.ext.commands import Cog, group, has_permissions
from xxhash import xxh32_intdigest

from bot.core import Context, Juno
from bot.shared import Paginator, quietly_delete

pattern = re.compile(
    r"(?:([^:/?#]+):)?(?://([^/?#]*))?([^?#]*\.(?:png|jpe?g|gif))(?:\?([^#]*))?(?:#(.*))?"
)


class Record(TypedDict):
    guild_id: int
    channel_id: int


class Gallery(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_message")
    async def gallery_listener(self, message: Message) -> None:
        """Delete messages which don't have attachments."""

        if (
            not message.guild
            or message.author.bot
            or not isinstance(message.channel, (TextChannel, Thread))
        ):
            return

        elif message.attachments or pattern.match(message.content):
            return

        query = "SELECT 1 FROM gallery WHERE channel_id = $1"
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, message.channel.id),
        )
        if not record:
            return

        key = f"gallery:{xxh32_intdigest(f'{message.guild.id}:{message.channel.id}')}"
        if not await self.bot.redis.ratelimited(key, 6, 10):
            return await quietly_delete(message)

        locked = await self.bot.redis.get(key)
        if locked:
            return

        await self.bot.redis.set(key, 1, 15)
        await asyncio.sleep(15)
        with suppress(HTTPException):
            await message.channel.purge(
                limit=200,
                check=lambda m: (
                    not m.author.bot
                    and not m.attachments
                    and not pattern.match(m.content)
                ),
            )

    @group(invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def gallery(self, ctx: Context) -> Message:
        """Restrict channels to only allow attachments."""

        return await ctx.send_help(ctx.command)

    @gallery.command(name="add", aliases=("create", "watch"))
    @has_permissions(manage_messages=True)
    async def gallery_add(
        self,
        ctx: Context,
        *,
        channel: TextChannel | Thread,
    ) -> Message:
        """Add a channel which only allows attachments."""

        query = "INSERT INTO gallery (guild_id, channel_id) VALUES ($1, $2)"
        try:
            await self.bot.db.execute(query, ctx.guild.id, channel.id)
        except UniqueViolationError:
            return await ctx.warn("This channel is already set as a gallery channel")

        return await ctx.approve(
            f"Now restricting {channel.mention} to only allow attachments"
        )

    @gallery.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    async def gallery_remove(
        self,
        ctx: Context,
        *,
        channel: TextChannel | Thread,
    ) -> Message:
        """Remove a channel from the gallery restriction."""

        query = "DELETE FROM gallery WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn("This channel is not set as a gallery channel")

        return await ctx.approve(
            f"No longer restricting {channel.mention} to only allow attachments"
        )

    @gallery.command(name="clear")
    @has_permissions(manage_messages=True)
    async def gallery_clear(self, ctx: Context) -> Message:
        """Remove all channels from the gallery restriction."""

        await ctx.prompt("Are you sure you want to remove all gallery channels?")

        query = "DELETE FROM gallery WHERE guild_id = $1"
        await self.bot.db.execute(query, ctx.guild.id)

        return await ctx.approve("No longer restricting any channels")

    @gallery.command(name="list")
    async def gallery_list(self, ctx: Context) -> Message:
        """View all gallery channels."""

        query = "SELECT channel_id FROM gallery WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`{channel.id}`]"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels are set as gallery channels")

        embed = Embed(title="Gallery Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
