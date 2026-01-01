from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Optional, cast
from discord import TextChannel as OriginalTextChannel, Webhook
import discord
from discord.abc import Messageable, MISSING
from cashews import cache
from bot.shared.client.context import GuildReskin
from logging import getLogger
if TYPE_CHECKING:
    from bot.core import Juno

logger = getLogger("bot.reskin")


class TextChannel:
    @staticmethod
    async def send(channel: OriginalTextChannel, *args, **kwargs):
        bot = cast("Juno", channel._state._get_client())
        reskin = await GuildReskin.fetch(channel.guild)
        if reskin and reskin.status:
            webhook = await TextChannel.reskin_webhook(channel)
            if webhook:
                try:
                    response = await webhook.send(
                        content=kwargs.get("content", args[0] if args else None),
                        username=reskin.username,
                        avatar_url=reskin.avatar_url,
                        tts=kwargs.get("tts", False),
                        ephemeral=kwargs.get("ephemeral", False),
                        file=kwargs.get("file", MISSING),
                        files=kwargs.get("files", MISSING),
                        embed=kwargs.get("embed", MISSING),
                        embeds=kwargs.get("embeds", MISSING),
                        allowed_mentions=kwargs.get("allowed_mentions", MISSING),
                        view=kwargs.get("view", MISSING),
                        wait=True,
                        suppress_embeds=kwargs.get("suppress_embeds", False),
                        silent=kwargs.get("silent", False),
                    )
                except discord.HTTPException as exc:
                    if exc.code == 10015:  # Unknown Webhook
                        logger.warning(
                            f"Webhook for {channel.id} is unknown, deleting from database"
                        )
                        query = "DELETE FROM reskin.webhook WHERE channel_id = $1"
                        await asyncio.gather(
                            *[
                                bot.db.execute(query, channel.id),
                                cache.delete_match(
                                    f"reskin:webhook:{channel.guild.id}:{channel.id}"
                                ),
                            ]
                        )
                    else:
                        logger.warning(
                            "Failed to send message via webhook, falling back to client",
                            exc_info=exc,
                        )

                    return await Messageable.send(channel, *args, **kwargs)

                if kwargs.get("delete_after"):
                    await response.delete(delay=kwargs["delete_after"])

                return response

        return await Messageable.send(channel, *args, **kwargs)

    @staticmethod
    @cache(ttl="2h", key="reskin:webhook:{channel.guild.id}:{channel.id}")
    async def reskin_webhook(channel: OriginalTextChannel) -> Optional[Webhook]:
        bot = cast("Juno", channel._state._get_client())
        query = "SELECT webhook_id FROM reskin.webhook WHERE status = TRUE AND channel_id = $1"
        webhook_id = await bot.db.fetchval(query, channel.id)
        if not webhook_id:
            return

        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, id=webhook_id)
        if webhook:
            return webhook

        query = "DELETE FROM reskin.webhook WHERE channel_id = $1"
        await bot.db.execute(query, channel.id)

        async def clear_cache():
            await asyncio.sleep(0.2)
            await cache.delete(f"reskin:webhook:{channel.guild.id}:{channel.id}")

        asyncio.create_task(clear_cache())


OriginalTextChannel.send = TextChannel.send  # type: ignore
