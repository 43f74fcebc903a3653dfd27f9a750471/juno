from api.shared import Media, BaseModel, Field, computed_field
from datetime import datetime
from typing import Optional, List
from .user import SimpleUser, InstagramUser

class Story(BaseModel):
    id: str
    taken_at: datetime
    media_type: int
    media: Media = Field(description="Redistributed media file.")
    sponsor_tags: Optional[List[SimpleUser]] = Field(default=[])
    user: SimpleUser | InstagramUser = Field(exclude=True)

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/stories/{self.user.username}/{self.id}"

    @computed_field
    @property
    def is_video(self) -> bool:
        return self.media_type == 2

class Highlight(BaseModel):
    id: str
    title: str
    cover: Media = Field(description="Redistributed media file.")
    user: Optional[SimpleUser] = Field(default=None, description="This is provided via /highlight.")
    items: Optional[List[Story]] = Field(default=[], description="This is provided via /highlight.")

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/stories/highlights/{self.id}"