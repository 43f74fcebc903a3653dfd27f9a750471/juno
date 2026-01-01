from __future__ import annotations
from typing import List, Optional
from aiohttp import ClientSession, FormData
from pydantic import BaseModel, Field
from yarl import URL

class PinterestLens(BaseModel):
    id: str
    repin_count: int
    description: Optional[str]
    image_url: str = Field(..., alias="image_large_url")

    @property
    def url(self) -> str:
        return f"https://www.pinterest.com/pin/{self.id}"
    
    @classmethod
    async def search(cls, buffer: bytes) -> List[PinterestLens]:
        """Reverse image search with Pinterest Lens."""

        async with ClientSession() as client:
            response = await client.put(
                URL.build(
                    scheme="https",
                    host="api.pinterest.com",
                    path="/v3/visual_search/extension/image/",
                ),
                data=FormData({
                    "image": buffer,
                    "page_size": "100",
                    "camera_type": "0",
                    "search_type": "0",
                    "source_type": "0",
                    "crop_source": "5",
                    "x": "0",
                    "y": "0",
                    "w": "1",
                    "h": "1",
                }),
            )
            if not response.ok:
                return []
            
            data = await response.json()
            items: list = data.get("data", [])
            items.sort(key=lambda item: item["repin_count"], reverse=True)

            return [cls(**item) for item in items]