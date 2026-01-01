from typing import Optional

from discord import Permissions
from pydantic import BaseModel, computed_field


class OAuthUser(BaseModel):
    id: int
    username: str
    global_name: Optional[str] = None
    public_flags: int
    avatar: Optional[str] = None

    @computed_field
    @property
    def avatar_url(self) -> str:
        if not self.avatar:
            return "https://cdn.discordapp.com/embed/avatars/0.png"

        return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar}.png"


class OAuthGuild(BaseModel):
    bot: bool
    id: int
    name: str
    icon: Optional[str] = None
    banner: Optional[str] = None
    owner: bool
    permissions: int
    features: list[str]

    @computed_field
    @property
    def icon_url(self) -> str:
        if not self.icon:
            return "https://cdn.discordapp.com/embed/avatars/0.png"

        return f"https://cdn.discordapp.com/icons/{self.id}/{self.icon}.png"

    @computed_field
    @property
    def banner_url(self) -> str:
        if not self.banner:
            return ""

        return f"https://cdn.discordapp.com/banners/{self.id}/{self.banner}.png"

    @computed_field
    @property
    def administrator(self) -> bool:
        return Permissions(self.permissions).administrator or self.owner
