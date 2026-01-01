from typing import List, Optional, TypedDict, cast
from discord import Embed, HTTPException, Message, TextChannel, Thread
import discord
from discord.ext.commands import (
    Cog,
    Range,
    BucketType,
    group,
    flag,
    CurrentChannel,
    cooldown,
    has_permissions,
)
from bot.shared import codeblock
from bot.shared.converters import FlagConverter, Identifier
from bot.core import Juno, Context
from bot.shared.formatter import vowel
from bot.shared.paginator import Paginator
from bot.shared.script import Script


class Flags(FlagConverter):
    username: Optional[Range[str, 1, 80]] = flag(
        aliases=["name"],
        description="The name of the webhook.",
    )
    avatar_url: Optional[str] = flag(
        aliases=["avatar"],
        description="The avatar URL of the webhook.",
    )


class Record(TypedDict):
    identifier: str
    guild_id: int
    channel_id: int
    author_id: int
    webhook_id: int


class Webhook(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @group(aliases=("hook", "wh"), invoke_without_command=True)
    @has_permissions(manage_webhooks=True)
    async def webhook(self, ctx: Context) -> Message:
        """Forward messages to a webhook."""

        return await ctx.send_help(ctx.command)

    @webhook.command(name="create", aliases=("add", "new"))
    @has_permissions(manage_webhooks=True)
    @cooldown(6, 480, BucketType.guild)
    async def webhook_create(
        self,
        ctx: Context,
        channel: TextChannel | Thread = CurrentChannel,
        *,
        name: Optional[str],
    ) -> Message:
        """Create a webhook in the specified channel."""

        channel = getattr(channel, "parent", channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("This command only works in text channels")

        query = "SELECT webhook_id FROM webhook WHERE channel_id = $1"
        webhook: Optional[discord.Webhook] = None
        webhook_id = cast(
            Optional[int],
            await self.bot.db.fetchval(query, channel.id),
        )
        if webhook_id:
            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, id=webhook_id)

        identifier = Identifier.create()
        webhook = webhook or await channel.create_webhook(
            name=name or f"Webhook {identifier.id}",
            reason=f"Webhook created by {ctx.author} ({ctx.author.id})",
        )

        query = """
        INSERT INTO webhook (
            identifier,
            guild_id,
            channel_id,
            author_id,
            webhook_id
        ) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (channel_id, webhook_id) DO UPDATE
        SET identifier = EXCLUDED.identifier
        """
        await self.bot.db.execute(
            query,
            identifier.id,
            ctx.guild.id,
            channel.id,
            ctx.author.id,
            webhook.id,
        )

        return await ctx.approve(
            f"Webhook created in {channel.mention} with the identifier {identifier}"
        )

    @webhook.command(name="delete", aliases=("remove", "rm", "del"))
    @has_permissions(manage_webhooks=True)
    async def webhook_delete(self, ctx: Context, identifier: Identifier) -> Message:
        """Delete an existing webhook by its identifier."""

        query = """
        DELETE FROM webhook
        WHERE guild_id = $1 
        AND identifier = $2
        RETURNING channel_id, webhook_id
        """
        record = await self.bot.db.fetchrow(query, ctx.guild.id, identifier.id)
        if not record:
            return await ctx.warn(f"Webhook with the identifier {identifier} not found")

        channel = cast(
            Optional[TextChannel], self.bot.get_channel(record["channel_id"])
        )
        if channel:
            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, id=record["webhook_id"])
            if webhook:
                await webhook.delete(
                    reason=f"Webhook deleted by {ctx.author} ({ctx.author.id})"
                )

        return await ctx.approve(
            f"Webhook with the identifier {identifier} has been deleted"
        )

    @webhook.command(name="list")
    @has_permissions(manage_webhooks=True)
    async def webhook_list(self, ctx: Context) -> Message:
        """View all webhooks in the guild."""

        query = "SELECT * FROM webhook WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        webhooks = [
            f"{channel.mention} - {identifier} via <@{record['author_id']}>"
            for record in records
            if (channel := ctx.guild.get_channel(record["channel_id"]))
            and (identifier := Identifier(record["identifier"]))
        ]
        if not webhooks:
            return await ctx.warn("No webhooks have been created")

        embed = Embed(title="Webhooks")
        paginator = Paginator(ctx, webhooks, embed)
        return await paginator.start()

    @webhook.command(name="forward", aliases=("send", "fwd"), extras={"flags": Flags})
    @has_permissions(manage_webhooks=True)
    async def webhook_forward(
        self,
        ctx: Context,
        identifier: Identifier,
        *,
        script: Script,
    ) -> Optional[Message]:
        """Forward a message through a webhook."""

        script.template, flags = await Flags().find(ctx, script.template)
        if not script:
            return await ctx.send_help(ctx.command)

        query = """
        SELECT channel_id, webhook_id
        FROM webhook
        WHERE guild_id = $1
        AND identifier = $2
        """
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, ctx.guild.id, identifier.id),
        )
        if not record:
            return await ctx.warn(f"Webhook with the identifier {identifier} not found")

        channel = cast(
            Optional[TextChannel], self.bot.get_channel(record["channel_id"])
        )
        if not channel:
            return await ctx.warn("The channel for this webhook no longer exists")

        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, id=record["webhook_id"])
        if not webhook:
            return await ctx.warn("The webhook no longer exists")

        try:
            await script.send(
                webhook,
                wait=True,
                username=flags.username or (webhook.name or ctx.guild.name),
                avatar_url=flags.avatar_url or (webhook.avatar or ctx.guild.icon),
            )
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your script",
                codeblock(exc.text),
            )

        if channel != ctx.channel:
            return await ctx.approve(
                f"Forwarded {vowel(script.format)} message to {channel.mention} via webhook {identifier}"
            )

        return await ctx.add_check()
    
    @webhook.command(name="edit", aliases=("modify", "update"))
    @has_permissions(manage_webhooks=True)
    async def webhook_edit(self, ctx: Context, message: Message, *, script: Script) -> Optional[Message]:
        """Update a message sent through a webhook."""

        if message.guild != ctx.guild:
            return await ctx.warn("The message must be from this server")
        
        elif not message.webhook_id:
            return await ctx.warn("The message was not sent by a webhook")
        
        webhooks = await ctx.channel.webhooks()
        webhook = discord.utils.get(webhooks, id=message.webhook_id)
        if not webhook:
            return await ctx.warn("The webhook for that message no longer exists")

        try:
            await script.edit(message, webhook=webhook)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your script",
                codeblock(exc.text),
            )

        return await ctx.add_check()