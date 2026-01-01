from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from aiohttp import ClientSession
from cashews import cache
from discord import Guild

if TYPE_CHECKING:
    from bot.core import Juno


@dataclass
class Version:
    major: int
    minor: int
    summary: str

    def __str__(self) -> str:
        return f"juno/v{self.major}.{self.minor}"


@dataclass
class OAuth2Config:
    client_id: int
    client_secret: str
    redirect_uri: str


@dataclass
class AzureConfig:
    region: str
    service: str
    key: str
    service_key: str


@dataclass
class LastfmConfig:
    public_key: str
    secret_key: str
    keys: List[str]


@dataclass
class ArchiveConfig:
    access_key: str
    secret_key: str

    @property
    def authorization(self) -> str:
        return f"LOW {self.access_key}:{self.secret_key}"


@dataclass
class TwitchConfig:
    client_id: str
    client_secret: str

    @cache(ttl="60h")
    async def get_token(self, session: ClientSession) -> str:
        async with session.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        ) as resp:
            data = await resp.json()
            return data["access_token"]


@dataclass
class SpotifyConfig:
    client_id: str
    client_secret: str


@dataclass
class RedditConfig:
    client_id: str
    client_secret: str

@dataclass
class InstagramConfig:
    username: str
    password: str

@dataclass
class APIConfig:
    shared: str
    notsobot: str
    youtube: List[
        str
    ]  # https://console.cloud.google.com/apis/api/youtube.googleapis.com
    fnbr: str  # https://fnbr.co/
    psn: str # https://github.com/isFakeAccount/psnawp?tab=readme-ov-file#getting-started
    tmdb: str  # https://www.themoviedb.org/documentation/api
    virus_total: str
    lastfm: LastfmConfig
    azure: Optional[AzureConfig]
    twitch: TwitchConfig
    spotify: SpotifyConfig
    reddit: RedditConfig
    instagram: InstagramConfig
    wolfram: str
    weather: str
    shuttle: str
    clever: str
    shodan: str
    clarifai: str  # https://clarifai.com/clarifai/main/models/moderation-recognition


@dataclass
class TixteConfig:
    domain: str
    token: str

    @property
    def public_url(self) -> str:
        return f"https://{self.domain}/r"


@dataclass
class PostgresConfig:
    user: str
    password: str
    host: str
    port: int
    database: str

    @property
    def dsn(self) -> str:
        return f"postgres://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class RedisConfig:
    user: str
    password: str
    host: str
    port: int
    database: int

    @property
    def url(self) -> str:
        return f"redis://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class BackendConfig:
    host: str
    port: int
    public_url: str
    pubsub_key: str

@dataclass
class PSNEmojis:
    bronze: str
    silver: str
    gold: str
    platinum: str

@dataclass
class BadgeEmojis:
    hypesquad: str
    hypesquad_balance: str
    hypesquad_bravery: str
    hypesquad_brilliance: str
    bug_hunter: str
    partner: str
    early_supporter: str
    boost: str
    nitro: str
    staff: str
    certified_moderator: str
    early_verified_bot_developer: str
    bug_hunter_level_2: str
    active_developer: str


@dataclass
class AudioEmojis:
    youtube: str
    spotify: str
    soundcloud: str
    applemusic: str


@dataclass
class PaginatorEmojis:
    previous: str
    next: str
    navigate: str
    cancel: str


@dataclass
class Emojis:
    on: str
    off: str
    vbucks: str
    psn: PSNEmojis
    audio: AudioEmojis
    paginator: PaginatorEmojis
    badges: Optional[BadgeEmojis]


@dataclass
class SupportServer:
    id: int
    invite: str

    def __str__(self) -> str:
        return self.invite

    def guild(self, bot: Juno) -> Optional[Guild]:
        return bot.get_guild(self.id)


@dataclass
class AntinukeWorker:
    id: int
    token: str


@dataclass
class Config:
    token: str
    prefixes: list[str]
    owner_ids: List[int]
    blacklist: List[int]
    http_proxy: Optional[
        str
    ]  # this is extremely important otherwise people will be able to see your IP address
    antinuke_worker: Optional[AntinukeWorker]
    version: Version
    website_url: str
    support: SupportServer
    oauth: OAuth2Config
    api: APIConfig
    backend: BackendConfig
    tixte: TixteConfig
    postgres: PostgresConfig
    redis: RedisConfig
    emojis: Emojis
    log_level: str
