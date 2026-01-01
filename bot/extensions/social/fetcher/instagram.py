from __future__ import annotations
from logging import getLogger
from typing import Optional
from pydantic import ConfigDict
from bot.core import Context
from shared_api.wrapper import SharedAPI
from shared_api.wrapper.routes.model import InstagramUser as BaseUser


logger = getLogger("bot.instagram")


class InstagramUser(BaseUser):
    client: SharedAPI
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __str__(self) -> str:
        return self.full_name or self.username

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}/"

    @property
    def display_name(self) -> str:
        username = (
            f"{self.full_name} (@{self.username})"
            if self.full_name and self.full_name != self.username
            else f"@{self.username}"
        )
        if self.is_verified:
            username += " ☑️"

        if self.is_private:
            username += " 🔒"

        return username

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"
        
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Optional[InstagramUser]:
        if argument.startswith("https://www.instagram.com/"):
            argument = argument.split("/")[-2]

        argument = argument.lstrip("@")
        async with ctx.typing():
            try:
                user = await ctx.bot.api.instagram.user(argument)
            except ValueError as exc:
                raise ValueError(f"No Instagram user found for `{argument}`") from exc


            return cls(**user.model_dump(), client=ctx.bot.api)
