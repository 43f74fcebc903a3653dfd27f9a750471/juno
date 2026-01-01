from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Sequence, cast

from asyncpg import Record
from discord.utils import format_dt, sleep_until, utcnow

if TYPE_CHECKING:
    from bot.core import Juno


class Timer:
    bot: Juno
    id: int
    expires_at: datetime
    created_at: datetime
    record: Record

    def __init__(self, bot: Juno, record: Record):
        self.bot = bot
        self.id = record["id"]
        self.expires_at = record["expires_at"]
        self.created_at = record["created_at"]
        self.record = record

    def __repr__(self) -> str:
        return f"<Timer id={self.id} event={self.record['event']} expires_at={self.expires_at}>"

    def __eq__(self, value: object) -> bool:
        return isinstance(value, Timer) and value.id == self.id

    @property
    def event(self) -> str:
        return f"{self.record['event']}_timer_complete"

    @property
    def expired(self) -> bool:
        return utcnow() > self.expires_at

    @property
    def remaining(self) -> timedelta:
        return self.expires_at - utcnow()

    @property
    def payload(self) -> dict[str, Any]:
        return self.record["payload"]

    @property
    def args(self) -> Sequence[Any]:
        return self.payload.get("args", [])

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.payload.get("kwargs", {})

    @property
    def human_timestamp(self, style: str = "R") -> str:
        return format_dt(self.expires_at, style)  # type: ignore

    async def short_optimization(self) -> None:
        await sleep_until(self.expires_at)
        self.bot.dispatch(self.event, self)

    @classmethod
    async def create(
        cls,
        bot: Juno,
        event: str,
        expires_at: datetime,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Timer:
        timer = cls(
            bot,
            {
                "id": 0,
                "event": event,
                "expires_at": expires_at,
                "created_at": utcnow(),
                "payload": {"args": args, "kwargs": kwargs},
            },
        )
        if timer.remaining.total_seconds() <= 120:
            bot.loop.create_task(timer.short_optimization())
            return timer

        query = """
        INSERT INTO timer.task (event, expires_at, payload)
        VALUES ($1, $2, $3)
        RETURNING id;
        """

        timer.id = cast(
            int,
            await bot.db.fetchval(
                query,
                event,
                expires_at,
                timer.payload,
            ),
        )
        return timer
