from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from aiohttp import ClientSession
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context


class Asset(BaseModel):
    fitInUrl: Optional[str] = None
    sizes: dict[str, str]

    @property
    def url(self) -> Optional[str]:
        if self.fitInUrl:
            return self.fitInUrl

        elif self.sizes:
            return next(iter(self.sizes.values()))


class TrackMetadata(BaseModel):
    bpm: int
    free: bool
    tags: List[str]

    @property
    def variable(self) -> str:
        return "metadata"


class BeatStarsTrack(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    slug: str
    title: str
    price: float
    created_at: datetime = Field(..., alias="releaseDate")
    artwork: Asset
    metadata: TrackMetadata
    user: BeatStarsUser = Field(..., alias="profile")

    def __str__(self) -> str:
        return self.title

    @property
    def variable(self) -> str:
        return "track"

    @property
    def url(self) -> str:
        return f"https://www.beatstars.com/beat/{self.slug}"


class BeatStarsUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(..., alias="memberId")
    username: str
    display_name: str = Field(None, alias="displayName")
    biography: Optional[str] = Field(None, alias="bio")
    location: Optional[str] = None
    avatar: Asset
    followers_count: int = 0
    plays_count: int = 0

    def __str__(self) -> str:
        return self.display_name

    @property
    def variable(self) -> str:
        return "user"

    @property
    def url(self) -> str:
        return f"https://www.beatstars.com/{self.username}"

    @property
    def avatar_url(self) -> Optional[str]:
        return self.avatar.url

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[BeatStarsUser]:
        """Fetch a BeatStars user by their username."""

        username = username.lstrip("@")
        async with ClientSession() as client:
            response = await client.post(
                URL.build(
                    scheme="https",
                    host="core.prod.beatstars.net",
                    path="/graphql",
                ),
                params={"op": "getMemberProfileByUsername"},
                json={
                    "operationName": "getMemberProfileByUsername",
                    "variables": {"username": username},
                    "query": "query getMemberProfileByUsername($username: String!) {\n  profileByUsername(username: $username) {\n    ...memberProfileInfo\n    __typename\n  }\n}\n\nfragment memberProfileInfo on Profile {\n  ...partialProfileInfo\n  location\n  bio\n  tags\n  badges\n  achievements\n  profileInventoryStatsWithUserContents {\n    ...mpGlobalMemberProfileUserContentStatsDefinition\n    __typename\n  }\n  socialInteractions(actions: [LIKE, FOLLOW, REPOST])\n  avatar {\n    assetId\n    fitInUrl(width: 200, height: 200)\n    sizes {\n      small\n      medium\n      large\n      mini\n      __typename\n    }\n    __typename\n  }\n  socialLinks {\n    link\n    network\n    profileName\n    __typename\n  }\n  activities {\n    follow\n    play\n    __typename\n  }\n  __typename\n}\n\nfragment partialProfileInfo on Profile {\n  displayName\n  username\n  memberId\n  location\n  v2Id\n  avatar {\n    assetId\n    sizes {\n      mini\n      __typename\n    }\n    __typename\n  }\n  __typename\n}\n\nfragment mpGlobalMemberProfileUserContentStatsDefinition on ProfileInventoryStats {\n  playlists\n  __typename\n}\n",
                },
            )
            if not response.ok:
                return None

            data = await response.json()
            user = data["data"]["profileByUsername"]
            return cls(
                **user,
                followers_count=user["activities"]["follow"],
                plays_count=user["activities"]["play"],
            )

    @classmethod
    async def tracks(cls, user_id: str) -> List[BeatStarsTrack]:
        """Fetch a BeatStars user's tracks by their ID."""

        async with ClientSession() as client:
            response = await client.post(
                URL.build(
                    scheme="https",
                    host="core.prod.beatstars.net",
                    path="/graphql",
                ),
                params={"op": "getProfileContentTrackList"},
                json={
                    "operationName": "getProfileContentTrackList",
                    "variables": {"memberId": user_id, "page": 0, "size": 12},
                    "query": "query getProfileContentTrackList($memberId: String!, $page: Int, $size: Int) {\n  profileTracks(memberId: $memberId, page: $page, size: $size) {\n    content {\n      ...MpPartialTrackV3Data\n      __typename\n    }\n    __typename\n  }\n}\n\nfragment MpPartialTrackV3Data on Track {\n  id\n  description\n  releaseDate\n  hasContracts\n  status\n  title\n  v2Id\n  seoMetadata {\n    slug\n    __typename\n  }\n  bundle {\n    date\n    hls {\n      url\n      type\n      signedUrl\n      duration\n      __typename\n    }\n    stream {\n      url\n      type\n      signedUrl\n      duration\n      __typename\n    }\n    __typename\n  }\n  profile {\n    memberId\n    badges\n    displayName\n    username\n    v2Id\n    avatar {\n      sizes {\n        mini\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n  price\n  metadata {\n    itemCount\n    tags\n    bpm\n    free\n    offerOnly\n    __typename\n  }\n  artwork {\n    ...MpItemArtwork\n    __typename\n  }\n  socialInteractions(actions: [LIKE])\n  __typename\n}\n\nfragment MpItemArtwork on Image {\n  fitInUrl(width: 700, height: 700)\n  sizes {\n    small\n    medium\n    mini\n    __typename\n  }\n  assetId\n  __typename\n}\n",
                },
            )
            if not response.ok:
                return []

            data = await response.json()
            return [
                BeatStarsTrack(**track, slug=track["seoMetadata"]["slug"])
                for track in data["data"]["profileTracks"]["content"]
            ]

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> BeatStarsUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No BeatStars user found for `{argument}`")

            return user
