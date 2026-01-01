from contextlib import suppress
from datetime import timedelta
from logging import getLogger

from discord import AllowedMentions, HTTPException
from discord.utils import utcnow

from bot.core import Juno
from bot.shared.formatter import plural
from bot.shared.script import Script

from .. import Record, Watcher
from .model import LetterboxdRanking

logger = getLogger("bot.leterboxd")


class Letterboxd(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60 * 15)

    async def fetch(self, username: str) -> list[LetterboxdRanking]:
        response = await self.bot.session.get(f"https://letterboxd.com/{username}/rss/")
        if not response.ok:
            return []

        data = await response.text()
        return LetterboxdRanking.from_xml(data)

    async def check(self, user_id: str, records: list[Record]) -> None:
        username = records[0]["username"]
        rankings = await self.fetch(username)

        for ranking in rankings:
            if utcnow() - ranking.created_at > timedelta(hours=12):
                continue

            elif await self.bot.redis.sismember(self.key, ranking.id):
                continue

            await self.bot.redis.sadd(self.key, ranking.id)
            await self.dispatch(ranking, records)

    async def dispatch(self, ranking: LetterboxdRanking, records: list[Record]) -> None:
        logger.info(
            f"Dispatching {ranking.film.title} from {ranking.creator} to {plural(len(records)):channel}"
        )

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            script = Script(
                record["template"]
                or "{creator} just rated {film} {ranking.stars}\n{url}",
                [
                    destination,
                    destination.guild,
                    ("creator", ranking.creator),
                    ("film", ranking.film),
                    ("ranking", ranking),
                ],
            )
            with suppress(HTTPException):
                await script.send(
                    destination,
                    allowed_mentions=AllowedMentions.all(),
                )
