from contextlib import suppress
from datetime import datetime, timedelta
from logging import getLogger
from typing import no_type_check

from discord import AllowedMentions, Embed, HTTPException

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.script import Script

from ..fetcher.kick import KickUser
from . import Record, Watcher

logger = getLogger("bot.kick")


class Kick(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60)

    async def check(self, user_id: str, records: list[Record]) -> None:
        username = records[0]["username"]
        user = await KickUser.fetch(username)
        if not user:
            return

        elif not user.stream:
            return await self.archive_messages(user)

        elif datetime.now() - user.stream.started_at > timedelta(hours=2):
            return

        elif await self.bot.redis.sismember(self.key, user.stream.id):
            return

        await self.bot.redis.sadd(self.key, user.stream.id)
        await self.dispatch(user, records)

    async def dispatch(self, user: KickUser, records: list[Record]) -> None:
        assert user.stream
        logger.info(
            f"Dispatching stream {user.stream.id} from {user.display_name} to {plural(len(records)):channel}"
        )

        embed = Embed(
            url=user.url,
            title=user.stream.title,
            timestamp=user.stream.started_at,
        )
        embed.set_author(
            url=user.url,
            name=f"{user.display_name} is now live!",
            icon_url=user.avatar_url,
        )
        embed.set_footer(
            text="Kick",
            icon_url="https://i.imgur.com/vorAnE0.png",
        )
        embed.set_image(url=user.stream.thumbnail.dynamic_url)

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            script = Script(
                record["template"] or "",
                [
                    destination,
                    destination.guild,
                    ("user", user),
                    ("stream", user.stream),
                ],
            )
            with suppress(HTTPException):
                message = await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    allowed_mentions=AllowedMentions.all(),
                )
                await self.bot.redis.sadd(
                    f"{self.key}:{user.id}",
                    f"{destination.guild.id}.{destination.id}.{message.id}",
                )

    @no_type_check
    async def archive_messages(self, user: KickUser) -> None:
        keys = await self.bot.redis.smembers(f"{self.key}:{user.id}")
        if not keys:
            return

        for key in keys:
            guild_id, channel_id, message_id = key.split(".")
            channel = self.get_channel(
                {
                    "guild_id": int(guild_id),
                    "channel_id": int(channel_id),
                }
            )
            if not channel:
                continue

            with suppress(HTTPException):
                message = await channel.fetch_message(int(message_id))
                if not message.embeds:
                    continue

                embed = message.embeds[0]
                embed.set_author(
                    url=embed.author.url,
                    name=embed.author.name.replace("is now live", "was live"),
                    icon_url=embed.author.icon_url,
                )
                if user.offline_banner_image:
                    embed.set_image(url=user.offline_banner_image)

                await message.edit(content=None, embed=embed)

        await self.bot.redis.delete(f"{self.key}:{user.id}")
        logger.info(
            f"Archived {plural(len(keys)):message} for {user.display_name} ({user.id})"
        )
