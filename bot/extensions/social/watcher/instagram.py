from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
import random
from typing import List, Optional

from discord import AllowedMentions, Embed, File, HTTPException
from discord.utils import utcnow

from bot.core import Juno
from bot.shared.formatter import plural
from .. import Record as BaseRecord
from .. import Watcher
from shared_api.wrapper.routes.model import InstagramUser, Story
logger = getLogger("bot.instagram")


class Record(BaseRecord):
    posts: bool
    stories: bool
    full_name: str
    avatar_url: Optional[str]


class Instagram(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=120, sleep=4, background=True)

    async def get_records(self) -> dict[str, List[Record]]:
        records = await super().get_records()
        records = list(records.items())
        random.shuffle(records)
        return dict(records)  # type: ignore

    async def check(
        self,
        user_id: str,
        records: List[Record],
    ) -> None:
        username = records[0]["username"]
        try:
            data = await self.bot.api.instagram.story(username)
        except ValueError:
            return

        for item in sorted(data.stories, key=lambda x: x.taken_at)[:6]:
            if utcnow() - item.taken_at > timedelta(days=2):
                continue

            elif await self.bot.redis.sismember(self.key, item.id):
                continue

            await self.bot.redis.sadd(self.key, item.id)
            await self.dispatch(data.user, item, records)

    async def dispatch(
        self,
        user: InstagramUser,
        story: Story,
        records: List[Record],
    ) -> None:
        logger.info(
            f"Dispatching story {story.id} from {user.username} to {plural(len(records)):channel}"
        )

        embed = Embed(timestamp=story.taken_at)
        embed.set_author(
            url=user.url,
            name=user.full_name or user.username,
            icon_url=user.avatar.url,
        )
        embed.set_footer(
            text="Instagram Story",
            icon_url="https://i.imgur.com/U31ZVlK.png",
        )

        buffer: Optional[bytes] = None
        if not story.is_video:
            embed.set_image(url=story.media.url)
        else:
            response = await self.bot.session.get(story.media.url)
            buffer = await response.read()

        for record in records:
            destination = record["channel"]
            too_large = len(buffer) >= destination.guild.filesize_limit if buffer else False
            if too_large:
                continue

            with suppress(HTTPException):
                await destination.send(
                    embed=embed,
                    file=(
                        File(BytesIO(buffer), filename=story.media.filename)
                        if buffer
                        else None
                    ),
                    allowed_mentions=AllowedMentions.all(),
                )
