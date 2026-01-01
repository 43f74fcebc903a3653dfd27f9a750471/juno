from __future__ import annotations
from datetime import datetime
import re
from typing import Optional, Self
from pydantic import BaseModel
from aiohttp import ClientSession
from yarl import URL
from .price import fetch_price
from bot.core import Context


ADDRESS_PATTERNS = {
    "BTC": r"^bc1[a-zA-HJ-NP-Z0-9]{25,62}$",
    "ETH": r"^0x[a-fA-F0-9]{40}$",
    "LTC": r"^(L|ltc1)[a-zA-HJ-NP-Z0-9]{26,62}$",
}


class PartialTransaction(BaseModel):
    id: str
    currency: str
    amount: float
    received_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    confirmations: Optional[int] = None
    sent: bool = False
    usd_rate: float

    @property
    def url(self) -> str:
        if self.currency == "LTC":
            return f"https://litecoinspace.org/tx/{self.id}"
            
        return f"https://blockchain.com/{self.currency.lower()}/tx/{self.id}"

    @property
    def usd_amount(self) -> float:
        return self.amount * self.usd_rate

    @property
    def confirmations_text(self) -> str:
        if not self.confirmations:
            return "Unconfirmed"

        return f"{self.confirmations} confirmations"

    @property
    def short_id(self) -> str:
        return f"{self.id[:8]}..{self.id[-8:]}"


class Address(BaseModel):
    id: str
    currency: str
    received: float
    sent: float
    total_transactions: int
    transactions: list[PartialTransaction]
    unconfirmed_transactions: list[PartialTransaction] = []
    usd_rate: float

    @property
    def balance(self) -> float:
        return self.received - self.sent

    @property
    def usd_balance(self) -> float:
        return self.balance * self.usd_rate

    @property
    def usd_received(self) -> float:
        return self.received * self.usd_rate

    @property
    def usd_sent(self) -> float:
        return self.sent * self.usd_rate

    @staticmethod
    def decimal(currency: str) -> int:
        if currency in ("BTC", "LTC"):
            return 10**8

        if currency == "ETH":
            return 10**18

        return 1

    @property
    def url(self) -> str:
        if self.currency == "LTC":
            return f"https://litecoinspace.org/address/{self.id}"

        return f"https://blockchain.com/explorer/addresses/{self.currency.lower()}/{self.id}"

    @classmethod
    async def fetch(cls, currency: str, address: str) -> Optional[Self]:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="api.blockcypher.com",
                    path=f"/v1/{currency.lower()}/main/addrs/{address}",
                )
            ) as response:
                if not response.ok:
                    return None

                data = await response.json()
                usd_rate = await fetch_price(session, currency)
                return cls(
                    id=address,
                    currency=currency,
                    received=data["total_received"] / cls.decimal(currency),
                    sent=data["total_sent"] / cls.decimal(currency),
                    total_transactions=data["n_tx"],
                    transactions=[
                        PartialTransaction(
                            id=tx["tx_hash"],
                            currency=currency,
                            amount=tx["value"] / cls.decimal(currency),
                            confirmed_at=tx["confirmed"],
                            confirmations=tx["confirmations"],
                            sent=tx["tx_input_n"] != -1,
                            usd_rate=usd_rate,
                        )
                        for tx in sorted(
                            data.get("txrefs", []),
                            key=lambda tx: tx["confirmed"],
                            reverse=True,
                        )
                        if not tx.get("spent_by", False)
                    ],
                    unconfirmed_transactions=[
                        PartialTransaction(
                            id=tx["tx_hash"],
                            currency=currency,
                            amount=tx["value"] / cls.decimal(currency),
                            received_at=tx["received"],
                            sent=tx["tx_input_n"] != -1,
                            usd_rate=usd_rate,
                        )
                        for tx in data.get("unconfirmed_txrefs", [])
                    ],
                    usd_rate=usd_rate,
                )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        argument = argument.split("software/", 1)[-1]
        sliced = argument.split(":")
        if len(sliced) == 2:
            currency, address = sliced
            address = await cls.fetch(currency.upper(), address)
            if address:
                return address

            raise ValueError("The provided wallet address could not be found")

        for currency, pattern in ADDRESS_PATTERNS.items():
            if re.match(pattern, argument):
                address = await cls.fetch(currency, argument)
                if address:
                    return address

        raise ValueError(
            "The provided wallet address could not be found"
            "\nTry to specify the currency, e.g. `BTC:bc34Nu0lyju5372h..`"
        )
