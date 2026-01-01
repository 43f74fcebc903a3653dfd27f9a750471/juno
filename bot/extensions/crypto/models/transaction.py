from __future__ import annotations
from datetime import datetime
import re
from typing import Optional, Self
from pydantic import BaseModel
from aiohttp import ClientSession
from yarl import URL
from .price import fetch_price
from bot.core import Context


TX_PATTERNS = {
    "BTC": r"^[0-9a-fA-F]{64}$",
    "ETH": r"^0x[a-fA-F0-9]{64}$",
    "LTC": r"^[a-fA-F0-9]{64}$",
}


class Address(BaseModel):
    id: str
    amount: float
    usd_rate: float

    @property
    def usd_amount(self) -> float:
        return self.amount * self.usd_rate


class Transaction(BaseModel):
    id: str
    currency: str
    created_at: datetime
    confirmed_at: Optional[datetime]
    confirmations: int
    inputs: list[Address]
    outputs: list[Address]
    amount: float
    fee: float
    size: int
    virtual_size: int
    usd_rate: float

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
            return f"https://litecoinspace.org/tx/{self.id}"

        return f"https://blockchain.com/explorer/transactions/{self.currency.lower()}/{self.id}"

    @property
    def confirmed(self) -> bool:
        return self.confirmations >= 1
    
    @property
    def usd_amount(self) -> float:
        return self.amount * self.usd_rate

    @property
    def usd_fee(self) -> float:
        return (self.fee / self.decimal(self.currency)) * self.usd_rate

    @property
    def fee_type(self) -> str:
        fees = {
            "BTC": "sats",
            "ETH": "gwei",
            "LTC": "lits",
            "XRP": "drops",
            "XLM": "stroops",
            "DOGE": "dogetoshi",
        }
        return fees.get(self.currency, "unknown")
        
    @property
    def pretty_fee(self) -> str:
        v = 8 if self.currency in ("BTC", "LTC") else 12
        fee_value = self.fee / self.decimal(self.currency)
        
        return f"{fee_value:,.{v}f} {self.fee_type}"
    
    @classmethod
    async def fetch(cls, currency: str, tx_id: str) -> Optional[Self]:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="api.blockcypher.com",
                    path=f"/v1/{currency.lower()}/main/txs/{tx_id}",
                ),
            ) as response:
                if not response.ok:
                    return None

                data = await response.json()
                usd_rate = await fetch_price(session, currency)
                return cls(
                    id=tx_id,
                    currency=currency,
                    created_at=data["received"],
                    confirmed_at=data.get("confirmed"),
                    confirmations=data["confirmations"],
                    inputs=[
                        Address(
                            id=_input["addresses"][0],
                            amount=_input.get("output_value", data["total"]) / cls.decimal(currency),
                            usd_rate=usd_rate,
                        )
                        for _input in data["inputs"]
                    ],
                    outputs=[
                        Address(
                            id=output["addresses"][0],
                            amount=output["value"] / cls.decimal(currency),
                            usd_rate=usd_rate,
                        )
                        for output in data["outputs"]
                    ],
                    amount=data["total"] / cls.decimal(currency),
                    fee=data["fees"],
                    size=data["size"],
                    virtual_size=data.get("vsize", 0),
                    usd_rate=usd_rate,
                )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        argument = argument.split("software/", 1)[-1]
        sliced = argument.split(":")
        if len(sliced) == 2:
            currency, tx_id = sliced
            transaction = await cls.fetch(currency.upper(), tx_id)
            if transaction:
                return transaction

            raise ValueError("The provided transaction ID could not be found")

        for currency, pattern in TX_PATTERNS.items():
            if re.match(pattern, argument):
                transaction = await cls.fetch(currency, argument)
                if transaction:
                    return transaction

        raise ValueError(
            "The provided transaction could not be found"
            "\nTry to specify the currency, e.g. `LTC:4u0lyju572h..`"
        )
