from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timedelta
from logging import getLogger
from typing import List, cast

import xmltodict
from aiohttp.web import Request, Response
from discord import AllowedMentions, HTTPException
from discord.utils import utcnow

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.script import Script
from config import config

from .. import Record as BaseRecord
from .. import Watcher

backend = config.backend
logger = getLogger("bot.youtube")


class Record(BaseRecord):
    shorts: bool


class YouTube(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60 * 5)
        self.bot.backend.router.add_get(
            f"/pubsub/{backend.pubsub_key}/youtube",
            self.receive_subscription,
        )
        self.bot.backend.router.add_post(
            f"/pubsub/{backend.pubsub_key}/youtube",
            self.receive_callback,
        )

    async def subscribe(self, user_id: str) -> None:
        callback = f"{backend.public_url}/pubsub/{backend.pubsub_key}/youtube"
        topic = f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={user_id}"

        response = await self.bot.session.post(
            "https://pubsubhubbub.appspot.com/subscribe",
            data={
                "hub.callback": callback,
                "hub.topic": topic,
                "hub.mode": "subscribe",
                "hub.verify": "async",
                "hub.verify_token": backend.pubsub_key,
                "hub.lease_seconds": "432000",
            },
        )
        logger.info(f"Subscribed to {user_id} with status {response.status}")

    async def receive_subscription(self, request: Request) -> Response:
        topic = request.query.get("hub.topic")
        lease = request.query.get("hub.lease_seconds")
        if topic and lease:
            user_id = topic.split("=")[-1]
            expires_at = utcnow() + timedelta(seconds=int(lease))

            await self.bot.db.execute(
                """
                INSERT INTO monitor.pubsub (id, platform, expires_at)
                VALUES ($1, $2, $3) ON CONFLICT (id)
                DO UPDATE SET expires_at = EXCLUDED.expires_at
                """,
                user_id,
                "youtube",
                expires_at,
            )
            logger.debug(f"Received subscription for {user_id} with lease {lease}")

        challenge = request.query.get("hub.challenge")
        return Response(text=challenge or "OK")

    async def receive_callback(self, request: Request) -> Response:
        body = await request.text()
        if "deleted-entry" in body:
            return Response(text="deleted", status=204)

        data = xmltodict.parse(body)["feed"]
        if not (entry := data.get("entry")):
            return Response(text="no", status=204)

        username, user_id, video_id, published = (
            entry["author"]["name"],
            entry["yt:channelId"],
            entry["yt:videoId"],
            entry.get("published"),
        )
        if (
            published
            and datetime.fromisoformat(published) < utcnow() - timedelta(hours=2)
        ) or await self.bot.redis.sismember("pubsub", video_id):
            return Response(text="old", status=204)

        await self.bot.redis.sadd("pubsub", video_id, ex=86400)
        query = f"SELECT * FROM {self.table} WHERE user_id = $1"
        records = cast(
            List[Record],
            await self.bot.db.fetch(query, user_id),
        )
        if records:
            self.bot.loop.create_task(self.dispatch(username, video_id, records))

        return Response(text="OK")

    async def check(self, user_id: str, records: List[Record]) -> None:
        await self.subscribe(user_id)

    async def get_records(self) -> dict[str, List[Record]]:
        """Get all channels that are not leased in the pubsub."""

        query = """
        SELECT DISTINCT ON (user_id) user_id
        FROM monitor.youtube
        WHERE user_id NOT IN (
            SELECT id
            FROM monitor.pubsub
            WHERE platform = 'youtube'
            AND expires_at > NOW()
        )
        """
        records = cast(List[Record], await self.bot.db.fetch(query))
        output: dict[str, List[Record]] = defaultdict(list)
        for record in records:
            output[record["user_id"]].append(record)

        return output

    async def dispatch(
        self,
        username: str,
        video_id: str,
        records: List[Record],
    ) -> None:
        logger.info(
            f"Dispatching video {video_id} from {username} to {plural(len(records)):channel}"
        )

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            script = Script(
                record["template"] or "**{user}** just posted a new video!\n{url}",
                [
                    destination,
                    destination.guild,
                    ("user", username),
                    ("video_id", video_id),
                    ("url", f"https://www.youtube.com/watch?v={video_id}"),
                ],
            )
            with suppress(HTTPException):
                await script.send(
                    destination,
                    allowed_mentions=AllowedMentions.all(),
                )
