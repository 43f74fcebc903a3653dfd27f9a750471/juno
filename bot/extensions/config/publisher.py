from contextlib import suppress

from discord import ChannelType, Embed, HTTPException, Message, TextChannel
from discord.ext.commands import Cog, group, has_permissions

from bot.core import Context, Juno
from bot.core.database.settings import Settings
from bot.shared.paginator import Paginator


class Publisher(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_message_without_command")
    async def publisher_listener(self, ctx: Context) -> None:
        """Publish announcements automatically."""

        if ctx.author.bot:
            return

        elif not isinstance(ctx.channel, TextChannel) or not ctx.channel.is_news():
            return

        elif not ctx.guild.me.guild_permissions.manage_messages:
            return

        settings = await Settings.fetch(self.bot, ctx.guild)
        if ctx.channel not in settings.publisher_channels:
            return

        key = f"publisher:{ctx.channel.id}"
        if await self.bot.redis.ratelimited(key, 10, 3600):
            return

        with suppress(HTTPException):
            await ctx.message.publish()

    @group(aliases=("announcement",), invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def publisher(self, ctx: Context) -> Message:
        """Publish announcements automatically."""

        return await ctx.send_help(ctx.command)

    @publisher.command(name="add", aliases=("create", "watch"))
    @has_permissions(manage_messages=True)
    async def publisher_add(self, ctx: Context, *, channel: TextChannel) -> Message:
        """Add a channel to publish messages in."""

        if not channel.is_news():
            try:
                await channel.edit(type=ChannelType.news)
            except HTTPException:
                return await ctx.warn("This channel is not a news channel")

        if channel in ctx.settings.publisher_channels:
            return await ctx.warn(
                f"Messages in {channel.mention} are already being published"
            )

        ctx.settings.record["publisher_channels"].append(channel.id)
        await ctx.settings.upsert()
        return await ctx.approve(
            f"Now automatically publishing messages in {channel.mention}"
        )

    @publisher.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    async def publisher_remove(self, ctx: Context, *, channel: TextChannel) -> Message:
        """Remove a channel from being monitored."""

        if channel not in ctx.settings.publisher_channels:
            return await ctx.warn(
                f"Messages in {channel.mention} are not being published"
            )

        ctx.settings.record["publisher_channels"].remove(channel.id)
        await ctx.settings.upsert()
        return await ctx.approve(
            f"No longer automatically publishing messages in {channel.mention}"
        )

    @publisher.command(name="clear")
    @has_permissions(manage_messages=True)
    async def publisher_clear(self, ctx: Context) -> Message:
        """Remove all channels from being monitored."""

        await ctx.prompt("Are you sure you want to remove all publisher channels?")

        ctx.settings.record["publisher_channels"].clear()
        await ctx.settings.upsert()
        return await ctx.approve("No longer monitoring any channels")

    @publisher.command(name="list")
    @has_permissions(manage_messages=True)
    async def publisher_list(self, ctx: Context) -> Message:
        """View all channels being monitored."""

        channels = [
            f"{channel.mention} [`{channel.id}`]"
            for channel in ctx.settings.publisher_channels
        ]
        if not channels:
            return await ctx.warn("No channels are being monitored")

        embed = Embed(title="Publisher Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
