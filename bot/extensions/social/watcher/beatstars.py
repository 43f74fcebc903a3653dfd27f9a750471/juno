from contextlib import suppress
from datetime import timedelta
from logging import getLogger

from discord import AllowedMentions, Embed, HTTPException
from discord.utils import utcnow

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.script import Script

from ..fetcher.beatstars import BeatStarsTrack, BeatStarsUser
from . import Record, Watcher

logger = getLogger("bot.beatstars")


class BeatStars(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60 * 5)

    async def check(self, user_id: str, records: list[Record]) -> None:
        tracks = await BeatStarsUser.tracks(user_id)
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
        user: BeatStarsUser,
        track: BeatStarsTrack,
        records: list[Record],
    ) -> None:
        logger.info(
            f"Dispatching beat {track.id} from {user} to {plural(len(records)):channel}"
        )

        embed = Embed(url=track.url, title=track.title)
        embed.set_author(
            url=user.url,
            name=f"{user.display_name} just posted a new track!",
            icon_url=user.avatar.url,
        )
        embed.set_image(url=track.artwork.url)
        embed.set_footer(
            text=" • ".join(
                [
                    f"${track.price}" if track.price else "FREE",
                    f"BPM: {track.metadata.bpm}",
                    f"{' '.join(track.metadata.tags)}",
                ]
            ),
            icon_url="https://i.imgur.com/IZ2cGrc.png",
        )

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            script = Script(
                record["template"] or "",
                [
                    destination,
                    destination.guild,
                    ("user", user),
                    ("track", track),
                    ("url", track.url),
                ],
            )

            with suppress(HTTPException):
                await destination.send(
                    content=script.content,
                    embeds=script.embeds or [embed],
                    allowed_mentions=AllowedMentions.all(),
                )
