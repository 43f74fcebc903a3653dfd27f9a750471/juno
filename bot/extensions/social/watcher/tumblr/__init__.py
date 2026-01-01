from contextlib import suppress
from datetime import timedelta
from logging import getLogger
from typing import List

from discord import HTTPException
from discord.utils import utcnow
from yarl import URL

from bot.core import Juno
from bot.shared.formatter import plural

from .. import Record, Watcher
from .model import Post, TumblrResponse

logger = getLogger("bot.tumblr")


class Tumblr(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60)

    async def fetch(self, username: str) -> List[Post]:
        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="api.tumblr.com",
                path=f"/v2/blog/{username}/posts",
            ),
            params={
                "fields[blogs]": "name,avatar,title,url,blog_view_url,is_adult,?is_member,description_npf,uuid,can_be_followed,?followed,?advertiser_name,theme,?primary,?is_paywall_on,?paywall_access,?subscription_plan,tumblrmart_accessories,can_show_badges,?can_be_booped,share_likes,share_following,can_subscribe,subscribed,ask,?can_submit,?is_blocked_from_primary,?is_blogless_advertiser,is_password_protected,updated,first_post_timestamp,posts,description,?top_tags_all",
                "npf": "true",
                "reblog_info": "true",
                "context": "archive",
            },
            headers={
                "Authorization": "Bearer aIcXSOoTtqrzR8L8YEIOmBeW94c3FmbSNSWAUbxsny9KKx5VFh"
            },
        )
        if not response.ok:
            return []

        data = await response.json()
        parsed = TumblrResponse.model_validate(data)
        return parsed.posts

    async def check(self, user_id: str, records: list[Record]) -> None:
        posts = await self.fetch(user_id)
        if not posts:
            return

        for post in reversed(posts):
            if utcnow() - post.created_at > timedelta(hours=12):
                continue

            elif await self.bot.redis.sismember(self.key, post.id):
                continue

            await self.bot.redis.sadd(self.key, post.id)
            await self.dispatch(post, records)

    async def dispatch(self, post: Post, records: list[Record]) -> None:
        logger.info(
            f"Dispatching post {post.id} from {post.blog} to {plural(len(records)):channel}"
        )

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            with suppress(HTTPException):
                await destination.send(post.url)
