from aiohttp import ClientSession
from cashews import cache
from yarl import URL

@cache(ttl="10m", key="{currency}")
async def fetch_price(session: ClientSession, currency: str) -> float:
    """Fetch the USD price of a cryptocurrency."""

    async with session.get(
        URL.build(
            scheme="https",
            host="min-api.cryptocompare.com",
            path="/data/price",
        ),
        params={
            "fsym": currency.upper(),
            "tsyms": "USD",
        },
    ) as response:
        if not response.ok:
            raise ValueError("No response was received from the API")

        data = await response.json()
        return data.get("USD", 0.0)