from typing import Optional

from discord import Color
from pydantic import BaseModel, Field


class Pinner(BaseModel):
    id: str
    username: str
    full_name: Optional[str] = None
    image_small_url: str

    def __str__(self) -> str:
        return self.full_name or self.username

    @property
    def url(self) -> str:
        return f"https://www.pinterest.com/{self.username}/"

    @property
    def avatar_url(self) -> str:
        return self.image_small_url


class Board(BaseModel):
    id: str
    name: str
    image_url: str = Field(alias="image_thumbnail_url")


class Pin(BaseModel):
    id: str
    dominant_color: Optional[str] = None
    image_url: str
    title: Optional[str] = None
    pinner: Pinner
    board: Board

    @property
    def url(self) -> str:
        return f"https://www.pinterest.com/pin/{self.id}/"

    @property
    def color(self) -> Color:
        if not self.dominant_color:
            return Color.dark_embed()
        
        return Color(int(self.dominant_color[1:], 16))
