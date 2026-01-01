from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel


class Video(BaseModel):
    id: Optional[str]
    uploader: Optional[str]
    title: Optional[str]
    description: Optional[str]
    duration: Optional[timedelta]
    views: Optional[int] = 0
    is_short: Optional[bool] = False
    thumbnail_url: Optional[str]
    created_at: Optional[datetime]

    def __str__(self) -> Optional[str]:
        return self.title

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"
