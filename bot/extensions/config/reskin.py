import asyncio
from contextlib import suppress
from io import BytesIO
from random import choice
from secrets import token_urlsafe
from typing import List, Optional, TypedDict, cast

from cashews import cache
from discord import (
    AuditLogEntry,
    CategoryChannel,
    Color,
    HTTPException,
    Message,
    TextChannel,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    cooldown,
    group,
    has_permissions,
    parameter,
)

from bot.core import Context, Juno
from bot.core import Reskin as ReskinConfig
from bot.shared.client.context import GuildReskin
from bot.shared import codeblock
from bot.shared.converters import PartialAttachment
from bot.shared.formatter import plural

FILTERED_CHANNELS = ("ticket", "log", "audit", "staff", "mod")
FORBIDDEN_NAMES = ("discord", "clyde", "bleed", "haunt")


class Record(TypedDict):
    status: bool
    guild_id: int
    channel_id: int
    webhook_id: int


class Reskin(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_audit_log_entry_webhook_delete")
    async def reskin_webhook_delete(self, entry: AuditLogEntry) -> None:
        if not (webhook := entry.target):
            return

        try:
            channel = cast(TextChannel, entry.before.channel)
        except AttributeError:
            return

        query = "DELETE FROM reskin.webhook WHERE webhook_id = $1"
        await self.bot.db.execute(query, webhook.id)
        await cache.delete_match(f"reskin:webhook:{entry.guild.id}:{channel.id}")

    @group(aliases=("custombot", "cb", "skin"), invoke_without_command=True)
    async def reskin(self, ctx: Context) -> Message:
        """Customize the bot's appearance."""

        return await ctx.send_help(ctx.command)

    @reskin.command(name="setup", aliases=("webhooks", "webhook", "wh"))
    @has_permissions(manage_webhooks=True)
    @cooldown(1, 120, BucketType.guild)
    async def reskin_setup(self, ctx: Context) -> Message:
        """Set up the necessary webhooks."""

        query = """
        UPDATE reskin.webhook
        SET status = TRUE
        WHERE guild_id = $1
        RETURNING channel_id, webhook_id
        """
        webhooks: List[tuple[TextChannel, int]] = [
            (channel, record["webhook_id"])
            for record in cast(
                List[Record],
                await self.bot.db.fetch(query, ctx.guild.id),
            )
            if (
                channel := cast(
                    Optional[TextChannel],
                    ctx.guild.get_channel(record["channel_id"]),
                )
            )
        ]

        channels = [
            channel
            for channel in ctx.guild.text_channels
            if channel not in [c for c, _ in webhooks]
            and not any(name in channel.name.lower() for name in FILTERED_CHANNELS)
            and not any(
                name in getattr(channel.category, "name", "")
                for name in FILTERED_CHANNELS
            )
        ]
        if not channels:
            await cache.delete_match(f"reskin:webhook:{ctx.guild.id}:*")
            return await ctx.warn(
                "There aren't any channels which need to be updated",
                "Reskin is now enabled for all channels across the server",
            )

        message = await ctx.respond(
            "Setting up the reskin relay webhooks...",
            "This is expected to take a while, please be patient",
        )
        async with ctx.typing():
            for channel in channels[:20]:
                try:
                    webhook = await asyncio.wait_for(
                        channel.create_webhook(
                            name="juno reskin",
                            reason=f"Webhook relay for reskin in {channel}",
                        ),
                        timeout=15,
                    )
                except asyncio.TimeoutError:
                    await ctx.warn(
                        "Webhook creation timed out while creating webhooks",
                        "This is due to Discord's strict rate limits, try again later",
                    )
                    break
                except HTTPException as exc:
                    await ctx.warn(
                        f"Webhook creation failed in {channel.mention}",
                        codeblock(exc.text),
                    )
                    break

                webhooks.append((channel, webhook.id))

        await self.bot.db.executemany(
            """
            INSERT INTO reskin.webhook (guild_id, channel_id, webhook_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, channel_id) DO UPDATE
            SET webhook_id = EXCLUDED.webhook_id
            """,
            [
                (ctx.guild.id, channel.id, webhook_id)
                for channel, webhook_id in webhooks
            ],
        )
        await cache.delete_match(f"reskin:webhook:{ctx.guild.id}:*")

        with suppress(HTTPException):
            await message.delete()

        return await ctx.approve(
            f"Reskin relay webhooks have been set up for {plural(len(webhooks), '`'):channel}",
            f"You can disable reskin at any time with the `{ctx.clean_prefix}reskin disable` command",
        )

    @reskin.command(name="disable", aliases=("off", "stop"))
    @has_permissions(manage_webhooks=True)
    async def reskin_disable(
        self,
        ctx: Context,
        *,
        channel: Optional[TextChannel | CategoryChannel],
    ) -> Message:
        """Disable the reskin relay."""

        # if it's a category we want to disable the webhooks for every channel in the category

        query = """
        UPDATE reskin.webhook
        SET status = FALSE
        WHERE guild_id = $1
        AND channel_id = ANY($2::bigint[])
        RETURNING channel_id, webhook_id
        """
        webhooks: List[tuple[TextChannel, int]] = [
            (channel, record["webhook_id"])
            for record in cast(
                List[Record],
                await self.bot.db.fetch(
                    query,
                    ctx.guild.id,
                    (
                        channel.id
                        if isinstance(channel, TextChannel)
                        else (
                            [channel.id for channel in channel.text_channels]
                            if isinstance(channel, CategoryChannel)
                            else None
                        )
                    ),
                ),
            )
            if (
                channel := cast(
                    Optional[TextChannel], ctx.guild.get_channel(record["channel_id"])
                )
            )
        ]
        if not webhooks:
            return await ctx.warn("There aren't any webhooks to disable")

        await cache.delete_match(f"reskin:webhook:{ctx.guild.id}:*")
        return await ctx.approve(
            f"Reskin relay webhooks have been disabled for {plural(len(webhooks), '`'):channel}",
            f"You can re-enable reskin at any time with the `{ctx.clean_prefix}reskin setup` command",
        )

    @reskin.command(name="username", aliases=("name",))
    async def reskin_username(
        self,
        ctx: Context,
        *,
        username: Range[str, 1, 32],
    ) -> Message:
        """Set your personal reskin username."""

        if any(forbidden in username.lower() for forbidden in FORBIDDEN_NAMES):
            return await ctx.warn(
                "The username is either reserved or forbidden",
                "Attempting to bypass this will result in a blacklist",
            )

        ctx.reskin = await ReskinConfig.update(ctx, username=username)
        return await ctx.approve(f"Your reskin username has been set to `{username}`")

    @reskin.command(name="avatar", aliases=("icon", "pfp", "av"))
    @cooldown(1, 6, BucketType.user)
    async def reskin_avatar(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Set your personal reskin avatar."""

        if attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        filename = f"{token_urlsafe(32)}.{attachment.extension}"
        avatar_url = await self.bot.tixte.upload(
            f"reskin_{filename}", BytesIO(attachment.buffer)
        )

        ctx.reskin = await ReskinConfig.update(ctx, avatar_url=avatar_url)
        return await ctx.approve("Your reskin avatar has been updated")

    @reskin.group(name="color", aliases=("colour",), invoke_without_command=True)
    async def reskin_color(self, ctx: Context, color: Color) -> Message:
        """Set your personal reskin embed color."""

        ctx.reskin = await ReskinConfig.update(ctx, embed_color=color.value)
        return await ctx.approve("Your reskin embed color has been updated")

    @reskin_color.command(name="random", aliases=("rand",))
    async def reskin_color_random(self, ctx: Context) -> Message:
        """Set your personal reskin embed color to a random color."""

        ctx.reskin = await ReskinConfig.update(ctx, embed_color=1337)
        return await ctx.approve("Now using a random color for your reskin embeds")

    @reskin.command(name="toggle", aliases=("status",))
    async def reskin_toggle(self, ctx: Context) -> Message:
        """Relay your responses with your personal reskin."""

        status = not ctx.reskin.status if ctx.reskin else True
        ctx.reskin = await ReskinConfig.update(ctx, status=status)

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} relaying your responses through a webhook",
            (
                f"You can customize your reskin with the `{ctx.clean_prefix}reskin {choice(['username', 'avatar'])}` command"
                if status
                else f"You can remove your reskin with the `{ctx.clean_prefix}reskin remove` command"
            ),
        )

    @reskin.command(name="remove", aliases=("delete", "del", "rm"))
    async def reskin_remove(self, ctx: Context) -> Message:
        """Remove your personal reskin."""

        if not ctx.reskin:
            return await ctx.warn("You haven't set up a reskin yet")

        await self.bot.db.execute(
            "DELETE FROM reskin.config WHERE user_id = $1",
            ctx.author.id,
        )
        await cache.delete(f"reskin:config:{ctx.author.id}")

        return await ctx.approve("Your reskin settings have been removed")

    @reskin.group(
        name="server",
        aliases=("guild", "events", "event"),
        invoke_without_command=True,
    )
    @has_permissions(administrator=True)
    async def reskin_server(self, ctx: Context) -> Message:
        """Customize the server's reskin appearance.

        This will apply to event messages such as welcome, etc."""

        return await ctx.send_help(ctx.command)

    @reskin_server.command(name="username", aliases=("name",))
    @has_permissions(administrator=True)
    async def reskin_server_username(
        self,
        ctx: Context,
        *,
        username: Range[str, 1, 32],
    ) -> Message:
        """Set the server's reskin username."""

        if any(forbidden in username.lower() for forbidden in FORBIDDEN_NAMES):
            return await ctx.warn(
                "The username is either reserved or forbidden",
                "Attempting to bypass this will result in a blacklist",
            )

        await GuildReskin.update(ctx.guild, username=username)
        return await ctx.approve(
            f"The server's reskin username has been set to `{username}`"
        )

    @reskin_server.command(name="avatar", aliases=("icon", "pfp", "av"))
    @has_permissions(administrator=True)
    async def reskin_server_avatar(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Set the server's reskin avatar."""

        if attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        filename = f"{token_urlsafe(32)}.{attachment.extension}"
        avatar_url = await self.bot.tixte.upload(
            f"reskin_{filename}", BytesIO(attachment.buffer)
        )

        await GuildReskin.update(ctx.guild, avatar_url=avatar_url)
        return await ctx.approve("The server's reskin avatar has been updated")

    @reskin_server.command(
        name="color",
        aliases=("colour",),
        invoke_without_command=True,
    )
    @has_permissions(administrator=True)
    async def reskin_server_color(self, ctx: Context, color: Color) -> Message:
        """Set the server's reskin embed color."""

        await GuildReskin.update(ctx.guild, embed_color=color.value)
        return await ctx.approve("The server's reskin embed color has been updated")

    @reskin_server.command(name="toggle", aliases=("status",))
    @has_permissions(administrator=True)
    async def reskin_server_toggle(self, ctx: Context) -> Message:
        """Relay the server's responses with the server's reskin."""

        reskin = await GuildReskin.fetch(ctx.guild)
        status = not reskin.status if reskin else True
        await GuildReskin.update(ctx.guild, status=status)

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} relaying the server's responses through a webhook",
            (
                f"You can customize the server's reskin with the `{ctx.clean_prefix}reskin server {choice(['username', 'avatar'])}` command"
                if status
                else f"You can remove the server's reskin with the `{ctx.clean_prefix}reskin server remove` command"
            ),
        )

    @reskin_server.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(administrator=True)
    async def reskin_server_remove(self, ctx: Context) -> Message:
        """Remove the server's reskin."""

        await self.bot.db.execute(
            "DELETE FROM reskin.guild_config WHERE guild_id = $1",
            ctx.guild.id,
        )
        await cache.delete(f"reskin:config:{ctx.guild.id}")

        return await ctx.approve("The server's reskin settings have been removed")
