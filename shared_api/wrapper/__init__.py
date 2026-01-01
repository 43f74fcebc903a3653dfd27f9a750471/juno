from typing import Literal
from aiohttp import ClientSession
from .routes import Instagram


class SharedAPI:
    """A high performance API for retrieving Social Media data."""

    api_key: str
    session: ClientSession
    instagram: Instagram

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = ClientSession(
            base_url="https://shared.egirl.software", headers={"Authorization": api_key}
        )
        self.instagram = Instagram(self)

    async def request(self, method: Literal["GET", "POST"], endpoint: str, **kwargs):
        """Submit a request to the API."""

        async with self.session.request(method, endpoint, **kwargs) as response:
            try:
                data = await response.json()
            except Exception as exc:
                raise ValueError("The API returned an invalid response") from exc

            if "error" in data:
                raise ValueError(data["error"])

            return data

    async def close(self) -> None:
        """Close the API session."""

        await self.session.close()
