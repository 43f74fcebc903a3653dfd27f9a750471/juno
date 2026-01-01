from collections import defaultdict
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
from typing import List, Literal, Optional, cast

from discord import AllowedMentions, Embed, File, HTTPException
from discord.abc import MISSING
from discord.utils import get, utcnow

from bot.core import Juno
from bot.shared import retry
from bot.shared.formatter import plural
from bot.shared.script import Script

from .. import Record as BaseRecord
from .. import Watcher
from .model import Timeline, Tweet

logger = getLogger("bot.twitter")


class Record(BaseRecord):
    retweets: bool
    replies: bool
    quotes: bool


class Twitter(Watcher):
    def __init__(self, bot: Juno):
        super().__init__(bot, interval=60)

    async def schedule(self, interval: float) -> None:
        query = f"SELECT COUNT(DISTINCT user_id) FROM {self.table}"
        amount = cast(int, await self.bot.db.fetchval(query))
        logger.info(f"Scheduled {plural(amount):user} to be monitored for tweets")
        return await super().schedule(interval)

    @retry(attempts=6, delay=30)
    async def get_records(self, user_ids: list[str]) -> dict[str, List[Record]]:
        query = f"SELECT * FROM {self.table} WHERE user_id = ANY($1::TEXT[])"
        records = cast(List[Record], await self.bot.db.fetch(query, user_ids))

        output: dict[str, List[Record]] = defaultdict(list)
        for record in records:
            if record in self.scheduled_deletion:
                continue

            record = cast(Record, dict(record))
            channel = self.get_channel(record)
            if not channel:
                self.scheduled_deletion.append(record)
                continue

            record["channel"] = channel
            output[record["user_id"]].append(record)

        return output

    async def monitor(self) -> None:
        timeline = await Timeline.fetch()
        if not timeline.tweets:
            return

        user_ids = list({tweet.user.id for tweet in timeline.tweets})
        records = await self.get_records(user_ids)
        for user_id, records in records.items():
            tweets = [tweet for tweet in timeline.tweets if tweet.user.id == user_id]
            self.bot.loop.create_task(self.check(tweets, records))

    async def check(self, tweets: List[Tweet], records: List[Record]) -> None:
        tweets = sorted(tweets, key=lambda x: x.id, reverse=True)
        for tweet in reversed(tweets[:5]):
            if utcnow() - tweet.created_at > timedelta(hours=2):
                continue

            elif await self.bot.redis.sismember(self.key, tweet.id):
                continue

            await self.bot.redis.sadd(self.key, tweet.id)
            await self.dispatch(tweet, records)

    def include_reference(
        self,
        embed: Embed,
        style: Literal["reply", "quote"],
        reference: Tweet,
    ) -> Embed:
        embed.description = (embed.description or "").split(")", 1)[-1]
        reference_phrase = "Replying to" if style == "reply" else "Quoting"

        if reference.text:
            embed.add_field(
                name=f"{reference_phrase} {reference.user}",
                value=f">>> {reference.text}",
            )
        else:
            embed.description = (
                f"-# {reference_phrase} [@{reference.user.username}]({reference.url})\n"
                + embed.description
            )

        if not embed.image and reference.media:
            for media in reference.media:
                if media.style in ("photo", "animated_gif"):
                    embed.set_image(url=media.url)
                    break

        return embed

    async def dispatch(self, tweet: Tweet, records: List[Record]) -> None:
        logger.info(
            f"Dispatching tweet {tweet.id} from {tweet.user} to {plural(len(records)):channel}"
        )

        embed = Embed(timestamp=tweet.created_at)
        embed.description = tweet.text
        embed.set_author(
            url=tweet.url,
            name=str(tweet.user),
            icon_url=tweet.user.avatar_url,
        )
        embed.set_footer(
            text=tweet.source,
            icon_url="https://abs.twimg.com/icons/apple-touch-icon-192x192.png",
        )
        for media in tweet.media:
            if media.style in ("photo", "animated_gif"):
                embed.set_image(url=media.url)
                break

        if tweet.parent:
            embed = self.include_reference(embed, "reply", tweet.parent)

        elif tweet.quoted:
            embed = self.include_reference(embed, "quote", tweet.quoted)

        buffer: Optional[BytesIO] = None
        if media := get(tweet.media, style="video"):
            buffer = await media.read()

        for record in records:
            destination = record["channel"]
            too_large = (
                buffer and len(buffer.getbuffer()) >= destination.guild.filesize_limit
            )

            if tweet.is_retweet and not record["retweets"]:
                continue

            elif tweet.is_reply and not record["replies"]:
                continue

            elif tweet.quoted and not record["quotes"]:
                continue

            elif tweet.possibly_sensitive and not destination.is_nsfw():
                continue

            script = Script(
                record["template"] or "",
                [
                    destination,
                    destination.guild,
                    ("user", tweet.user),
                    ("tweet", tweet),
                ],
            )
            with suppress(HTTPException):
                if too_large:
                    await destination.send(content=tweet.url)
                    continue

                await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    file=(
                        File(buffer, filename=media.name)
                        if media and buffer and not too_large
                        else MISSING
                    ),
                    allowed_mentions=AllowedMentions.all(),
                )

            if buffer:
                buffer.seek(0)
