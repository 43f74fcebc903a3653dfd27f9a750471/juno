from contextlib import suppress
from datetime import timedelta
from logging import getLogger

from discord import AllowedMentions, HTTPException
from discord.utils import utcnow

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.script import Script

from ..fetcher.soundcloud import SoundCloudTrack, SoundCloudUser
from . import Record, Watcher

logger = getLogger("bot.soundcloud")


class SoundCloud(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60 * 5)

    async def check(self, user_id: str, records: list[Record]) -> None:
        tracks = await SoundCloudUser.tracks(user_id)
        if not tracks:
            return

        for track in reversed(tracks):
            if utcnow() - track.created_at > timedelta(hours=12):
                continue

            elif await self.bot.redis.sismember(self.key, track.id):
                continue

            await self.bot.redis.sadd(self.key, track.id)
            await self.dispatch(track.user, track, records)

    async def dispatch(
        self,
        user: SoundCloudUser,
        track: SoundCloudTrack,
        records: list[Record],
    ) -> None:
        logger.info(
            f"Dispatching {track.kind} {track.id} from {user} to {plural(len(records)):channel}"
        )

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            script = Script(
                record["template"]
                or "**{user}** just posted a new {track.kind}!\n{track.url}",
                [
                    destination,
                    destination.guild,
                    ("user", user),
                    ("track", track),
                    ("url", track.url),
                ],
            )
            with suppress(HTTPException):
                await script.send(
                    destination,
                    allowed_mentions=AllowedMentions.all(),
                )
