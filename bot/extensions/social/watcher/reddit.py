import re
from asyncio import Task
from contextlib import suppress
from datetime import datetime, timezone
from html import unescape
from io import BytesIO
from logging import getLogger
from typing import List, Optional, cast

import validators
from asyncpraw import Reddit as Client
from asyncpraw.models.reddit.submission import Redditor, Submission
from asyncpraw.models.reddit.subreddit import Subreddit, SubredditStream
from discord import Embed, File, HTTPException

from bot.core import Juno
from bot.shared import retry
from bot.shared.formatter import plural, shorten
from config import config

from ..reposters.reddit import Reddit as RedditReposter
from . import Record, Watcher

logger = getLogger("bot.reddit")


class Reddit(Watcher):
    client: Client
    reposter: RedditReposter
    streams: dict[str, Task] = {}

    def __init__(self, bot: Juno):
        super().__init__(bot, interval=60 * 5)
        if not bot.reddit:
            bot.reddit = Client(
                client_id=config.api.reddit.client_id,
                client_secret=config.api.reddit.client_secret,
                user_agent=f"{config.version} by ethan",
            )

        self.client = bot.reddit
        self.reposter = RedditReposter(bot, add_listener=False)
        bot.loop.create_task(self.monitor())  # startup initial monitoring

    @retry(attempts=6, delay=30)
    async def get_records(self, name: str) -> List[Record]:
        query = f"SELECT * FROM {self.table} WHERE username = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, name))

        output: List[Record] = []
        for record in records:
            if record in self.scheduled_deletion:
                continue

            record = cast(Record, dict(record))
            channel = self.get_channel(record)
            if not channel:
                self.scheduled_deletion.append(record)
                continue

            record["channel"] = channel
            output.append(record)

        return output

    async def monitor(self) -> None:
        query = "SELECT ARRAY_AGG(username) FROM monitor.reddit"
        usernames = cast(List[str], await self.bot.db.fetchval(query) or [])
        for username in usernames:
            await self.check(username)

        for username, stream in list(self.streams.items()):
            if username not in usernames:
                logger.info(f"Stopping submission stream for r/{username}")
                stream.cancel()

    async def check(self, username: str):
        if username in self.streams:
            return

        self.streams[username] = stream = self.bot.loop.create_task(
            self.start_stream(username),
            name=f"subreddit-{username}",
        )
        stream.add_done_callback(lambda _: self.streams.pop(username))

    async def start_stream(self, name: str) -> None:
        subreddit: Subreddit = await self.client.subreddit(name)
        stream: SubredditStream = subreddit.stream

        logger.info(f"Started submission stream for r/{name}")
        async for submission in stream.submissions(skip_existing=True):
            records = await self.get_records(name)
            await self.dispatch(submission, records)

    async def dispatch(self, submission: Submission, records: list[Record]) -> None:
        logger.info(
            f"Dispatching submission {submission.id} from {submission.author} to {plural(len(records)):channel}"
        )

        author: Optional[Redditor] = submission.author
        if author:
            await author.load()

        embed = Embed(
            url=f"https://reddit.com{submission.permalink}",
            title=shorten(unescape(submission.title or ""), 256),
            description=shorten(unescape(submission.selftext or ""), 2048),
            timestamp=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
        )
        embed.set_author(
            name=f"u/{unescape(author.name if author else '[deleted]')}",
            url=f"https://reddit.com/u/{unescape(author.name)}" if author else None,
            icon_url=author.icon_img if author else None,
        )
        embed.set_footer(
            text=f"r/{unescape(submission.subreddit.display_name)}",
            icon_url="https://i.imgur.com/t90f67x.png",
        )

        buffer: Optional[BytesIO] = None
        extension: Optional[str] = None
        if submission.url:
            if (
                hasattr(submission, "is_gallery")
                and submission.is_gallery
                and submission.media_metadata
            ):
                media_url = list(submission.media_metadata.values())[0]["p"][-1]["u"]
                embed.set_image(url=media_url)

            elif (
                not submission.is_video
                and submission.permalink not in submission.url
                and validators.url(submission.url)
                and re.search(r"\.(jpg|jpeg|png|gif|webp)$", submission.url)
            ):
                embed.set_image(url=submission.url)

            else:
                data = await self.reposter.fetch(submission.url)
                if data and data.requested_downloads:
                    buffer = await data.requested_downloads[-1].read()
                    extension = data.ext

        for record in records:
            destination = record["channel"]

            if submission.over_18 and not destination.is_nsfw():
                continue

            with suppress(HTTPException):
                if not buffer:
                    await destination.send(embed=embed)
                    continue

                await destination.send(
                    embed=embed,
                    file=File(buffer, filename=f"{submission.id}.{extension}"),
                )
                buffer.seek(0)
