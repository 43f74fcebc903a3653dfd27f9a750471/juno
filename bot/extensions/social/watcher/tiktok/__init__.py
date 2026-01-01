import asyncio
from asyncio import Lock
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
import random
import string
from typing import List, Literal, Optional

from discord import AllowedMentions, Embed, File, HTTPException
from discord.abc import MISSING
from discord.utils import utcnow

from bot.core import Juno
from bot.shared import retry
from bot.shared.formatter import plural, shorten
from bot.shared.script import Script

from ...fetcher.tiktok import TikTokUser
from .. import Record as BaseRecord
from .. import Watcher
from .model import Post


logger = getLogger("bot.tiktok")


class Record(BaseRecord):
    reposts: bool
    lives: bool
    live_template: Optional[str]


class TikTok(Watcher):
    lock: Lock

    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60)
        self.lock = Lock()

    def _build_query(self, user_id: str, limit: int = 5) -> dict[str, str]:
        return {
            "aid": "1988",
            "app_language": "en",
            "app_name": "tiktok_web",
            "browser_language": "en-US",
            "browser_name": "Mozilla",
            "browser_online": "true",
            "browser_platform": "Win32",
            "browser_version": "5.0 (Windows)",
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "count": "15",
            "cursor": "0",
            "device_id": str(random.randint(7250000000000000000, 7351147085025500000)),
            "device_platform": "web_pc",
            "focus_state": "true",
            "from_page": "user",
            "history_len": "2",
            "is_fullscreen": "false",
            "is_page_visible": "true",
            "language": "en",
            "os": "windows",
            "priority_region": "",
            "referer": "",
            "region": "US",
            "screen_height": "1080",
            "screen_width": "1920",
            "secUid": user_id,
            "type": "1",
            "tz_name": "UTC",
            "verifyFp": f'verify_{"".join(random.choices(string.hexdigits, k=7))}',
            "webcast_language": "en",
        }

    @retry(attempts=30, delay=1)
    async def fetch(
        self,
        user_id: str,
        limit: int = 5,
        mode: Literal["creator", "repost"] = "creator",
    ) -> List[Post]:
        response = await self.bot.session.get(
            f"https://www.tiktok.com/api/{mode}/item_list/",
            params=self._build_query(user_id, limit),
        )
        data = await response.json()
        if data["status_code"] != 0:
            return []

        posts: List[Post] = []
        for post in data.get("itemList", []):
            required = ("author", "stats", "video")
            if not all(key in post for key in required):
                continue

            post = Post(
                **post,
                images=[
                    image["imageURL"]["urlList"][-1]
                    for image in post.get("imagePost", {}).get("images", [])
                ],
            )
            posts.append(post)

        return posts

    async def check(self, user_id: str, records: list[Record]) -> None:
        username = records[0]["username"]
        user, posts, reposts = await asyncio.gather(
            TikTokUser.fetch(username),
            self.fetch(user_id),
            self.fetch(user_id, mode="repost"),
        )

        if user and user.live_id:
            if await self.bot.redis.sismember(self.key, user.live_id):
                return

            await self.bot.redis.sadd(self.key, user.live_id)
            await self.dispatch_live(user, records)

        for post in list(reversed(posts)) + list(reversed(reposts)):
            if utcnow() - post.created_at > timedelta(days=2):
                continue

            elif await self.bot.redis.sismember(self.key, post.id):
                continue

            await self.bot.redis.sadd(self.key, post.id)
            await self.dispatch(
                post,
                records,
                reposter=user if user and post.author.id != user.id else None,
            )

    async def dispatch(
        self,
        post: Post,
        records: List[Record],
        reposter: Optional[TikTokUser] = None,
    ) -> None:
        logger.info(
            f"Dispatching {'re' if reposter else ''}post {post.id} from {reposter or post.author} to {plural(len(records)):channel}"
        )

        embed = Embed(
            url=post.url,
            title=shorten(post.caption or "", 256),
            timestamp=post.created_at,
        )
        embed.set_author(
            url=post.author.url,
            name=str(post.author)
            if not reposter
            else f"{reposter} reposted {post.author}",
            icon_url=post.author.avatar_url,
        )
        embed.set_footer(
            text="TikTok" + (" Repost" if reposter else ""),
            icon_url="https://i.imgur.com/gOGXGVc.png",
        )

        buffer: Optional[BytesIO] = None
        if post.video.url and not post.images:
            buffer = await post.video.read(post.id)

        elif post.images:
            embed.set_image(url=post.images[0])

        for record in records:
            if not record["reposts"] and reposter:
                continue

            destination = record["channel"]
            too_large = (
                buffer and len(buffer.getbuffer()) >= destination.guild.filesize_limit
            )
            if too_large:
                embed.set_image(url=post.video.cover_url)

            script = Script(
                record["template"] or "",
                [
                    destination.guild,
                    destination,
                    ("user", post.author),
                    ("post", post),
                ],
            )
            with suppress(HTTPException):
                await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    file=(
                        File(buffer, filename=post.video.filename)
                        if buffer and not too_large
                        else MISSING
                    ),
                    allowed_mentions=AllowedMentions.all(),
                )

            if too_large:
                embed.set_image(url=None)

            if buffer:
                buffer.seek(0)

    async def dispatch_live(self, user: TikTokUser, records: List[Record]) -> None:
        logger.info(
            f"Dispatching live {user.live_id} from {user} to {plural(len(records)):channel}"
        )

        embed = Embed(
            url=f"{user.url}/live",
            title=f"{user} is live on TikTok!",
            description=f"Click [**HERE**]({user.url}/live) to watch the live stream",
            timestamp=utcnow(),
        )
        embed.set_author(
            url=user.url,
            name=str(user),
            icon_url=user.avatar_url,
        )
        embed.set_image(url=user.avatar_url)
        embed.set_footer(
            text="TikTok",
            icon_url="https://i.imgur.com/gOGXGVc.png",
        )

        for record in records:
            if not record["lives"]:
                continue

            destination = record["channel"]
            script = Script(
                record["live_template"] or "",
                [
                    destination,
                    destination.guild,
                    ("user", user),
                ],
            )

            with suppress(HTTPException):
                await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    allowed_mentions=AllowedMentions.all(),
                )
