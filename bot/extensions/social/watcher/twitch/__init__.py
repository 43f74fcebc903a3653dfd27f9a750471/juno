import asyncio
from collections import defaultdict
from contextlib import suppress
from logging import getLogger
from typing import List, Literal, cast, no_type_check

from aiohttp.web import Request, Response
from discord import AllowedMentions, Embed, HTTPException

from bot.core import Juno
from bot.extensions.social.fetcher.twitch import TwitchUser
from bot.shared.formatter import plural
from bot.shared.script import Script
from config import config

from .. import Record, Watcher
from .http import TWITCH_SUB_BASE, Headers

backend = config.backend
logger = getLogger("bot.twitch")


class Twitch(Watcher):
    received_events: list[str] = []

    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60 * 5)
        self.received_events = []
        self.bot.backend.router.add_post(
            f"/pubsub/{backend.pubsub_key}/twitch",
            self.receive_callback,
        )

    async def headers(self) -> dict[str, str]:
        access_token = await config.api.twitch.get_token(self.bot.session)
        return {
            "Content-Type": "application/json",
            "Client-ID": config.api.twitch.client_id,
            "Authorization": f"Bearer {access_token}",
        }

    async def subscribe(
        self,
        user_id: str,
        event: Literal["stream.online", "stream.offline"],
    ) -> None:
        callback = f"{backend.public_url}/pubsub/{backend.pubsub_key}/twitch"
        headers = await self.headers()
        data = {
            "type": event,
            "version": "1",
            "condition": {
                "broadcaster_user_id": user_id,
            },
            "transport": {
                "method": "webhook",
                "callback": callback,
                "secret": backend.pubsub_key,
            },
        }

        response = await self.bot.session.post(
            TWITCH_SUB_BASE,
            headers=headers,
            json=data,
        )
        if response.status == 202:
            logger.info(f"Subscribed to {event} events for {user_id}")
        else:
            data = await response.json()
            logger.error(f"Received a {data['error']} error for {user_id}")

    async def receive_callback(self, request: Request) -> Response:
        data = await request.json()
        try:
            headers = Headers.parse_obj(request.headers)
        except Exception:
            logger.error("Received invalid headers from Twitch")
            return Response(text="Invalid headers", status=400)

        if headers.message_id in self.received_events:
            logger.warning(f"Received duplicate event {headers.message_id}")
            return Response(text="Duplicate event")

        self.received_events.append(headers.message_id)
        if headers.message_type == "webhook_callback_verification":
            user_id = data["subscription"]["condition"]["broadcaster_user_id"]
            logger.info(
                f"Received subscription {data['subscription']['type']} for {user_id}"
            )
            await self.bot.db.execute(
                """
                INSERT INTO monitor.pubsub (id, platform)
                VALUES ($1, $2) ON CONFLICT (id)
                DO NOTHING
                """,
                user_id,
                "twitch",
            )
            return Response(text=data["challenge"])

        elif headers.message_type == "notification":
            if data["subscription"]["type"] == "stream.online":
                user_id = data["event"]["broadcaster_user_id"]
                query = f"SELECT * FROM {self.table} WHERE user_id = $1"
                records = cast(
                    List[Record],
                    await self.bot.db.fetch(query, user_id),
                )
                if records:
                    asyncio.create_task(self.dispatch(user_id, records))

            elif data["subscription"]["type"] == "stream.offline":
                user_id = data["event"]["broadcaster_user_id"]
                await self.archive_messages(user_id)

            else:
                logger.warning(
                    f"Received unknown event type {data['subscription']['type']}"
                )

            return Response(text="OK")

        elif headers.message_type == "revocation":
            user_id = data["subscription"]["condition"]["broadcaster_user_id"]
            logger.info(f"Subscription for {user_id} has been revoked")

            query = "DELETE FROM monitor.pubsub WHERE user_id = $1"
            await self.bot.db.execute(query, user_id)
            return Response(text="OK")

        return Response(text="OK")

    async def check(self, user_id: str, records: List[Record]) -> None:
        for event in ("stream.online", "stream.offline"):
            await self.subscribe(user_id, event)

    async def get_records(self) -> dict[str, List[Record]]:
        query = f"""
        SELECT DISTINCT ON (user_id) user_id
        FROM {self.table}
        WHERE user_id NOT IN (
            SELECT id
            FROM monitor.pubsub
            WHERE platform = 'twitch'
        )
        """
        records = cast(List[Record], await self.bot.db.fetch(query))
        output: dict[str, List[Record]] = defaultdict(list)
        for record in records:
            output[record["user_id"]].append(record)

        return output

    async def dispatch(self, user_id: str, records: List[Record]) -> None:
        users = await TwitchUser.fetch(self.bot.session, [int(user_id)])
        if not users:
            return

        user = users[0]
        stream = await user.stream(self.bot.session)
        if not stream:
            return

        logger.info(
            f"Dispatching stream {stream.id} from {user.display_name} to {plural(len(records)):channel}"
        )

        embed = Embed(
            url=stream.url,
            title=stream.title,
            timestamp=stream.started_at,
        )
        embed.set_author(
            url=user.url,
            name=f"{user.display_name} is now live!",
            icon_url=user.avatar_url,
        )
        embed.set_footer(
            text="Twitch",
            icon_url="https://i.imgur.com/NbYOyh6.png",
        )
        embed.set_image(url=stream.thumbnail_url)

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
                    ("stream", stream),
                ],
            )
            with suppress(HTTPException):
                message = await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    allowed_mentions=AllowedMentions.all(),
                )
                await self.bot.redis.sadd(
                    f"{self.key}:{user_id}",
                    f"{destination.guild.id}.{destination.id}.{message.id}",
                )

    @no_type_check
    async def archive_messages(self, user_id: str) -> None:
        keys = await self.bot.redis.smembers(f"{self.key}:{user_id}")
        if not keys:
            return

        users = await TwitchUser.fetch(self.bot.session, [int(user_id)])
        if not users:
            return

        user = users[0]
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
                if (
                    not embed.url
                    or not embed.author
                    or "is now live" not in embed.author.name
                ):
                    continue

                embed.url = embed.url + "/videos"
                embed.set_author(
                    url=embed.author.url,
                    name=embed.author.name.replace("is now live", "was live"),
                    icon_url=embed.author.icon_url,
                )
                if user.offline_image:
                    embed.set_image(url=user.offline_image)

                await message.edit(content=None, embed=embed)

        await self.bot.redis.delete(f"{self.key}:{user_id}")
        logger.info(
            f"Archived {plural(len(keys)):message} for {user.display_name} ({user_id})"
        )
