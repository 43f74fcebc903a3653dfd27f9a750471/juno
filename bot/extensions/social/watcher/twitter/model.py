from __future__ import annotations

import re
from contextlib import suppress
from datetime import datetime
from html import unescape
from io import BytesIO
from json import dumps
from logging import getLogger
from typing import List, Literal, Optional, Self

from aiohttp import ClientSession
from pydantic import BaseModel, Field
from yarl import URL

from bot.core import Context

logger = getLogger("bot.twitter")
headers = {
    "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
    "X-Csrf-Token": "",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Cookie": '',
}


class User(BaseModel):
    id: str
    name: str
    username: str = Field(alias="screen_name")
    profile_image_url_https: str
    following: bool = False
    blocked_by: bool = False
    protected: bool = False

    def __str__(self) -> str:
        return self.name or self.username

    @property
    def url(self) -> str:
        return f"https://twitter.com/{self.username}"

    @property
    def avatar_url(self) -> str:
        return self.profile_image_url_https.replace("_normal", "")

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[User]:
        """Fetch a Twitter user by their username."""

        username = username.lstrip("@")
        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="x.com",
                    path="/i/api/graphql/-0XdHI-mrHWBQd8-oLo1aA/ProfileSpotlightsQuery",
                ),
                headers=headers,
                params={"variables": dumps({"screen_name": username})},
            )
            if not response.ok:
                return None

            data = await response.json()
            if "user_result_by_screen_name" not in data["data"]:
                return None

            user = data["data"]["user_result_by_screen_name"]["result"]
            return cls(
                **user["legacy"],
                id=user["rest_id"],
                profile_image_url_https="/",
            )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> User:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No Twitter user found for `{argument}`")

            return user


class Media(BaseModel):
    url: str
    short_url: str
    style: Literal["photo", "video", "animated_gif"] = Field(alias="type")

    @property
    def name(self) -> str:
        return self.url.split("/")[-1].split("?")[0]

    async def read(self) -> BytesIO:
        async with ClientSession() as session:
            async with session.request("GET", self.url) as resp:
                buffer = await resp.read()
                return BytesIO(buffer)


class Link(BaseModel):
    url: str
    display_url: str
    expanded_url: str

    def __str__(self) -> str:
        return f"[{self.display_url}]({self.expanded_url})"


class Tweet(BaseModel):
    id: str = Field(alias="id_str")
    url: str
    user: User
    full_text: Optional[str]
    media: List[Media] = []
    links: List[Link] = []
    parent: Optional[Tweet] = None
    quoted: Optional[Tweet] = None
    possibly_sensitive: Optional[bool] = False
    created_at: datetime = Field(alias="posted_at")
    source_html: str = Field(alias="source")

    def __str__(self) -> str:
        return self.text

    @property
    def is_retweet(self) -> bool:
        return self.text.startswith("RT")

    @property
    def is_reply(self) -> bool:
        return bool(self.parent)

    @property
    def text(self) -> str:
        text = self.full_text or ""
        for media in self.media:
            text = text.replace(media.short_url, "")

        for link in self.links:
            text = text.replace(link.url, str(link))

        for _ in ("ampt;", "amp;"):
            text = text.replace(_, "")

        text = re.sub(r"@(\w+)", r"[@\1](https://twitter.com/\1)", text)
        text = re.sub(r"#(\w+)", r"[\1](https://twitter.com/hashtag/\1)", text)
        return unescape(text.strip()).rstrip(":")

    @property
    def source(self) -> str:
        return (
            self.source_html.split(">")[1]
            .split("<")[0]
            .replace("advertiser-interface", "Advertisement")
        )


class RateLimit(BaseModel):
    limit: int
    remaining: int
    reset: datetime


class Timeline(BaseModel):
    tweets: List[Tweet] = []
    ratelimit: RateLimit

    @classmethod
    async def fetch(cls) -> Self:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="x.com",
                    path="/i/api/graphql/DiTkXJgLqBBxCs7zaYsbtA/HomeLatestTimeline",
                ),
                headers=headers,
                params={
                    "variables": dumps(
                        {
                            "count": 200,
                            "includePromotedContent": False,
                            "latestControlAvailable": True,
                            "withHighlightedLabel": True,
                            "withTweetQuoteCount": True,
                            "withCommunity": True,
                            "requestContext": "launch",
                        }
                    ),
                    "features": dumps(
                        {
                            "rweb_tipjar_consumption_enabled": True,
                            "responsive_web_graphql_exclude_directive_enabled": True,
                            "verified_phone_label_enabled": False,
                            "creator_subscriptions_tweet_preview_api_enabled": True,
                            "responsive_web_graphql_timeline_navigation_enabled": True,
                            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                            "communities_web_enable_tweet_community_results_fetch": True,
                            "c9s_tweet_anatomy_moderator_badge_enabled": True,
                            "articles_preview_enabled": True,
                            "responsive_web_edit_tweet_api_enabled": True,
                            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                            "view_counts_everywhere_api_enabled": True,
                            "longform_notetweets_consumption_enabled": True,
                            "responsive_web_twitter_article_tweet_consumption_enabled": True,
                            "tweet_awards_web_tipping_enabled": False,
                            "creator_subscriptions_quote_tweet_preview_enabled": False,
                            "freedom_of_speech_not_reach_fetch_enabled": True,
                            "standardized_nudges_misinfo": True,
                            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                            "rweb_video_timestamps_enabled": True,
                            "longform_notetweets_rich_text_read_enabled": True,
                            "longform_notetweets_inline_media_enabled": True,
                            "responsive_web_enhance_cards_enabled": False,
                        }
                    ),
                },
            ) as response:
                ratelimit = RateLimit(
                    limit=int(response.headers["x-rate-limit-limit"]),
                    remaining=int(response.headers["x-rate-limit-remaining"]),
                    reset=datetime.fromtimestamp(
                        int(response.headers["x-rate-limit-reset"])
                    ),
                )
                if not response.ok:
                    print(response.status, await response.text())
                    return cls(tweets=[], ratelimit=ratelimit)

                elif ratelimit.remaining <= 50:
                    logger.warning(
                        f"We're running very low on Twitter API requests. {ratelimit.remaining}/{ratelimit.limit} remaining."
                    )

                data = await response.json()
                try:
                    timeline = data["data"]["home"]["home_timeline_urt"]
                except KeyError:
                    logger.error(f"Failed to fetch timeline {data}")
                    return cls(tweets=[], ratelimit=ratelimit)

                tweets: List[Tweet] = []
                for instruction in timeline["instructions"]:
                    if "entries" not in instruction:
                        continue

                    elif instruction["type"] != "TimelineAddEntries":
                        continue

                    for entry in instruction["entries"]:
                        if not entry["entryId"].startswith(
                            ("tweet", "home-conversation")
                        ):
                            continue

                        with suppress(KeyError, ValueError):
                            reply: Optional[dict] = None
                            if entry["entryId"].startswith("home-conversation"):
                                tweet = cls.extract_reply(entry)
                                reply = cls.extract_reply(entry, parent=True)
                            else:
                                tweet = entry["content"]["itemContent"][
                                    "tweet_results"
                                ]["result"]

                            parsed = cls.parse(tweet)
                            if reply:
                                parsed.parent = cls.parse(reply)
                            elif "quoted_status_result" in tweet:
                                parsed.quoted = cls.parse(
                                    tweet["quoted_status_result"]["result"]
                                )

                            tweets.append(parsed)

                return cls(tweets=tweets, ratelimit=ratelimit)

    @classmethod
    def parse(cls, tweet: dict) -> Tweet:
        user = tweet["core"]["user_results"]["result"]
        return Tweet(
            **tweet["legacy"],
            user=User(**user["legacy"], id=user["rest_id"]),
            media=[
                Media(
                    type=media["type"],
                    short_url=media["url"],
                    url=media["media_url_https"]
                    if not media.get("video_info")
                    else media["video_info"]["variants"][-1]["url"],
                )
                for media in tweet["legacy"]
                .get("extended_entities", {})
                .get("media", [])
            ],
            links=[
                Link(**link)
                for link in tweet["legacy"].get("entities", {}).get("urls", [])
            ],
            source=tweet["source"],
            url=f"https://twitter.com/{user['legacy']['screen_name']}/status/{tweet['legacy']['id_str']}",
            posted_at=datetime.strptime(
                tweet["legacy"]["created_at"], "%a %b %d %H:%M:%S %z %Y"
            ),
        )

    @classmethod
    def extract_reply(cls, entry: dict, parent: bool = False) -> dict:
        index = 0 if parent else -1
        tweet = entry["content"]["items"][index]["item"]["itemContent"][
            "tweet_results"
        ]["result"]
        return tweet

    @classmethod
    async def follow(cls, user_id: str) -> bool:
        async with ClientSession() as session:
            async with session.post(
                URL.build(
                    scheme="https",
                    host="x.com",
                    path="/i/api/1.1/friendships/create.json",
                ),
                headers={
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                params={
                    "include_profile_interstitial_type": "1",
                    "include_blocking": "1",
                    "include_blocked_by": "1",
                    "include_followed_by": "1",
                    "include_want_retweets": "1",
                    "include_mute_edge": "1",
                    "include_can_dm": "1",
                    "include_can_media_tag": "1",
                    "include_ext_has_nft_avatar": "1",
                    "include_ext_is_blue_verified": "1",
                    "include_ext_verified_type": "1",
                    "include_ext_profile_image_shape": "1",
                    "skip_status": "1",
                    "user_id": str(user_id),
                },
            ) as response:
                data = await response.json()
                if not response.ok:
                    logger.warning(f"Failed to follow user {user_id} {data}")
                    return False

                logger.info(f"Now following {data['name']} (@{data['screen_name']})")
                return True
