from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import List, Literal, Optional, Union, cast

from aiohttp import ClientSession
from discord import Color, Embed, File
from discord.ext.commands import CommandError
from pydantic import BaseModel, Field
from yarl import URL

from bot.core import Context
from bot.shared import quietly_delete
from config import config

FNBR_API = URL.build(
    scheme="https",
    host="fnbr.co",
    path="/api",
)
RARITY_COLORS = {
    "frozen": 0xC4DFF7,
    "lava": 0xD19635,
    "legendary": 0xE67E22,
    "dark": 0xFF42E7,
    "marvel": 0x761B1B,
    "dc": 0x243461,
    "star_wars": 0x081737,
    "gaming_legends": 0x312497,
    "icon_series": 0x3FB8C7,
}


class MOTD(BaseModel):
    id: str
    title: str
    body: str
    image: str

    @classmethod
    async def fetch(cls) -> List[MOTD]:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="fortnite-api.com",
                    path="/v2/news/br",
                )
            ) as response:
                data = await response.json()
                data = data["data"]

        return [cls(**motd) for motd in data["motds"]]


class Map(BaseModel):
    image: str
    blank_image: str
    pois: List[str]

    async def file(self, style: Literal["blank", "pois"] = "pois") -> File:
        url = self.blank_image if style == "blank" else self.image
        async with ClientSession() as session:
            async with session.get(url) as response:
                buffer = await response.read()

        return File(BytesIO(buffer), filename="map.png")

    @classmethod
    async def fetch(cls) -> Map:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="fortnite-api.com",
                    path="/v1/map",
                )
            ) as response:
                data = await response.json()
                data = data["data"]

        return cls(
            image=data["images"]["pois"],
            blank_image=data["images"]["blank"],
            pois=[poi.get("name") or poi["id"] for poi in data["pois"]],
        )


class CosmeticImages(BaseModel):
    icon: Optional[str]
    gallery: Optional[str] | Literal[False]
    featured: Optional[str] | Literal[False]
    resize_available: Optional[bool] = Field(False, alias="resizeAvailable")


class CosmeticHistory(BaseModel):
    occurrences: int
    first_seen: datetime = Field(alias="firstSeen")
    last_seen: datetime = Field(alias="lastSeen")
    dates: List[datetime]


class Cosmetic(BaseModel):
    id: str
    name: str
    description: str | Literal[False]
    type: str
    rarity: str
    price: str
    images: CosmeticImages
    history: Union[Literal[False], CosmeticHistory]
    price_icon: Union[Literal[False], str] = Field(alias="priceIcon")
    price_icon_url: Optional[str] | Literal[False] = Field(alias="priceIconLink")

    def __str__(self) -> str:
        return f"[**{self.name}**]({self.url}) ({self.pretty_type})"

    def is_lego(self) -> bool:
        return self.type.startswith("lego")

    @property
    def url(self) -> str:
        return f"https://fnbr.co/cosmetics/{self.id}"

    @property
    def pretty_type(self) -> str:
        return self.type.replace("_", " ").title()

    @property
    def color(self) -> Color:
        try:
            return Color(RARITY_COLORS[self.rarity])
        except KeyError:
            return Color.dark_embed()

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Cosmetic:
        data = (
            await cls.fetch(argument)
            if ctx.command.name != "equip"
            else await cls.fetch_fnapi(argument)
        )
        if not data:
            raise CommandError(
                f"No cosmetics were found for `{argument}`",
                "Please make sure that you're using the [correct name](https://fnbr.co/list)",
            )

        if len(data) > 1:
            embed = Embed(
                title="Multiple cosmetics are available",
                description=(
                    "Please select the cosmetic from the list below\n"
                    + "\n".join(
                        f"> `{str(index).zfill(2)}` {item}"
                        for index, item in enumerate(data, start=1)
                    )
                ),
            )
            embed.set_footer(text="Reply with the cosmetic identifier")
            prompt = await ctx.reply(embed=embed)
            cosmetic = cast(Cosmetic, await ctx.choose_option(data))
            await quietly_delete(prompt)
            return cosmetic

        return data[0]

    @classmethod
    async def fetch(cls, argument: str) -> List[Cosmetic]:
        async with ClientSession() as session:
            async with session.get(
                FNBR_API / "images",
                params={"search": argument, "limit": 5},
                headers={"x-api-key": config.api.fnbr},
            ) as response:
                data = await response.json()
                data = data.get("data", [])
                if not data:
                    raise CommandError(
                        f"No cosmetics were found for `{argument}`",
                        "Please make sure that you're using the [correct name](https://fnbr.co/list)",
                    )

        return list(
            filter(
                lambda item: not item.is_lego(),
                [cls(**item) for item in data],
            )
        )

    @classmethod
    async def fetch_fnapi(cls, argument: str) -> List[Cosmetic]:
        argument = argument.replace("purple", "").replace("pink", "").strip()
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="fortnite-api.com",
                    path="/v2/cosmetics/br/search",
                ),
                params={"name": argument},
            ) as response:
                data = await response.json()
                if not data["status"] == 200:
                    return []

                item = data["data"]

        return [
            cls(
                id=item["id"],
                name=item["name"],
                description=item["description"],
                type=item["type"]["displayValue"],
                rarity=item["rarity"]["value"],
                price="",
                images=CosmeticImages(
                    icon=item["images"]["icon"],
                    gallery=False,
                    featured=False,
                    resizeAvailable=False,
                ),
                history=False,
                priceIcon=False,
                priceIconLink=False,
            )
        ]


class Shop(BaseModel):
    date: datetime
    cosmetics: List[Cosmetic]
    cosmetic_ids: List[str]

    @classmethod
    async def fetch(cls) -> Shop:
        async with ClientSession() as session:
            async with session.get(
                FNBR_API / "shop",
                headers={"x-api-key": config.api.fnbr},
            ) as response:
                if not response.ok:
                    raise ValueError(
                        f"The Fortnite API returned an error (`{response.status}`)"
                    )

                data = await response.json()
                data = data["data"]

        return cls(
            date=data["date"],
            cosmetics=data["featured"] + data["daily"],
            cosmetic_ids=[
                item_id for section in data["sections"] for item_id in section["items"]
            ],
        )
