from __future__ import annotations
from datetime import datetime
from typing import Optional, TypedDict
from pydantic import BaseModel

from bot.core import Juno


class Bet(TypedDict):
    id: int
    user_id: int
    amount: float
    multiplier: float
    payout: float
    game: str
    created_at: datetime


class Rakeback(TypedDict):
    user_id: int
    last_claimed: Optional[datetime]
    amount: float


class EconomyUser(BaseModel):
    user_id: int
    last_daily: Optional[datetime] = None
    last_worked: Optional[datetime] = None
    experience: int = 0
    balance: float = 50
    wagered: float = 0
    net_profit: float = 0

    @classmethod
    async def fetch(cls, bot: Juno, user_id: int) -> EconomyUser:
        query = "SELECT * FROM economy.user WHERE user_id = $1"
        record = await bot.db.fetchrow(query, user_id)
        return cls(**record) if record else cls(user_id=user_id)

    async def save(self, bot: Juno) -> None:
        query = """
            INSERT INTO economy.user (user_id, last_daily, last_worked, experience, balance, wagered, net_profit)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id)
            DO UPDATE SET
                last_daily = EXCLUDED.last_daily,
                last_worked = EXCLUDED.last_worked,
                experience = EXCLUDED.experience,
                balance = EXCLUDED.balance,
                wagered = EXCLUDED.wagered,
                net_profit = EXCLUDED.net_profit
        """
        await bot.db.execute(
            query,
            self.user_id,
            self.last_daily,
            self.last_worked,
            self.experience,
            self.balance,
            self.wagered,
            self.net_profit,
        )