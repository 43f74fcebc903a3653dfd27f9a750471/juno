from contextlib import suppress
from io import BytesIO
from json import dumps
from logging import getLogger
from typing import List, Optional

from discord import Embed, File, HTTPException
from discord.utils import as_chunks
from yarl import URL

from bot.core import Juno
from bot.shared.formatter import shorten

from .. import Record as BaseRecord
from .. import Watcher
from .model import Pin

logger = getLogger("bot.pinterest")


class Record(BaseRecord):
    board_id: str
    embeds: bool


class Pinterest(Watcher):
    def __init__(self, bot: Juno) -> None:
        super().__init__(bot, interval=60)

    async def fetch(self, username: str, board_id: Optional[str] = None) -> List[Pin]:
        resource = "UserPins" if not board_id else "BoardFeed"
        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="www.pinterest.com",
                path=f"/resource/{resource}Resource/get/",
            ),
            params={
                "source_url": f"/{username}/_saved/",
                "data": dumps(
                    {
                        "options": {
                            "add_vase": True,
                            "field_set_key": "mobile_grid_item"
                            if not board_id
                            else "react_grid_pin",
                            "is_own_profile_pins": False,
                            "page_size": 250,
                            "username": username,
                            **({"board_id": board_id or {}}),
                        },
                        "context": {},
                    }
                ),
            },
        )
        if not response.ok:
            return []

        data = await response.json()
        results: List[Pin] = []
        for pin in data["resource_response"]["data"]:
            if not pin.get("images") and not pin.get("videos"):
                continue

            results.append(
                Pin(
                    **pin,
                    image_url=list(pin["images"].items())[-1][1]["url"],
                )
            )

        return results

    async def check(self, user_id: str, records: list[Record]) -> None:
        username = records[0]["username"]
        pins = await self.fetch(username)

        await self.dispatch(pins[:12], records)

    async def dispatch(self, pins: List[Pin], records: List[Record]) -> None:
        buffers: List[tuple[Pin, BytesIO]] = []
        for pin in pins:
            response = await self.bot.session.get(pin.image_url)
            buffer = await response.read()
            buffers.append((pin, BytesIO(buffer)))

        for record in records:
            destination = self.get_channel(record)
            if not destination:
                self.scheduled_deletion.append(record)
                continue

            dest_key = f"{self.key}:{destination.id}"
            if record["embeds"]:
                for pin in pins:
                    if record["board_id"] != "0" and pin.board.id != record["board_id"]:
                        continue

                    elif await self.bot.redis.sismember(dest_key, pin.id):
                        continue

                    embed = Embed(
                        url=pin.url,
                        color=pin.color,
                        title=shorten(pin.title or "", 256),
                    )
                    embed.set_author(
                        url=pin.pinner.url,
                        name=pin.pinner.full_name or pin.pinner.username,
                        icon_url=pin.pinner.avatar_url,
                    )
                    embed.set_footer(
                        text="Pinterest",
                        icon_url="https://i.imgur.com/J44d2yk.png",
                    )
                    embed.set_image(url=pin.image_url)

                    with suppress(HTTPException):
                        await destination.send(embed=embed)
                        await self.bot.redis.sadd(dest_key, pin.id)

            else:
                for chunk in as_chunks(buffers, 6):
                    if record["board_id"] != "0":
                        chunk = [
                            (pin, buffer)
                            for pin, buffer in chunk
                            if pin.board.id == record["board_id"]
                        ]

                    chunk = [
                        (pin, buffer)
                        for pin, buffer in chunk
                        if not await self.bot.redis.sismember(dest_key, pin.id)
                    ]
                    if not chunk:
                        continue

                    with suppress(HTTPException):
                        await destination.send(
                            files=[
                                File(buffer, f"{pin.id}.jpg") for pin, buffer in chunk
                            ]
                        )
                        await self.bot.redis.sadd(
                            dest_key,
                            *[pin.id for pin, _ in chunk],
                        )

                    for _, buffer in chunk:
                        buffer.seek(0)

        for _, buffer in buffers:
            buffer.close()

        buffers.clear()
