from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Union

from pydantic import BaseModel, Field


class Media(BaseModel):
    filename: str = Field(..., title='Filename')
    url: str = Field(..., title='Url')


class SimpleUser(BaseModel):
    id: str = Field(..., title='Id')
    username: str = Field(..., title='Username')
    full_name: Optional[str] = Field(None, title='Full Name')
    avatar: Media = Field(..., description='Redistributed avatar image.')
    is_verified: Optional[bool] = Field(False, title='Is Verified')
    is_private: Optional[bool] = Field(False, title='Is Private')


class Statistics(BaseModel):
    likes: int = Field(..., title='Likes')
    comments: int = Field(..., title='Comments')
    views: int = Field(..., title='Views')
    shares: int = Field(..., title='Shares')


class Story(BaseModel):
    id: str = Field(..., title='Id')
    taken_at: datetime = Field(..., title='Taken At')
    media_type: int = Field(..., title='Media Type')
    media: Media = Field(..., description='Redistributed media file.')
    sponsor_tags: Optional[List[SimpleUser]] = Field([], title='Sponsor Tags')
    url: str = Field(..., title='Url')
    is_video: bool = Field(..., title='Is Video')


class TikTokEvent(BaseModel):
    id: str = Field(..., title='Id')
    title: str = Field(..., title='Title')
    starts_at: datetime = Field(..., title='Starts At')
    url: str = Field(..., title='Url')


class ValidationError(BaseModel):
    loc: List[Union[str, int]] = Field(..., title='Location')
    msg: str = Field(..., title='Message')
    type: str = Field(..., title='Error Type')


class ApiRoutesInstagramModelsUserBioLink(BaseModel):
    url: str = Field(..., title='Url')
    link_id: Optional[str] = Field(None, title='Link Id')
    lynx_url: Optional[str] = Field(None, title='Lynx Url')
    title: Optional[str] = Field(None, title='Title')
    pretty_url: str = Field(..., title='Pretty Url')


class ApiRoutesInstagramModelsUserUserStatistics(BaseModel):
    posts: Optional[int] = Field(0, title='Posts')
    followers: Optional[int] = Field(0, title='Followers')
    following: Optional[int] = Field(0, title='Following')


class ApiRoutesTiktokModelsUserBioLink(BaseModel):
    url: str = Field(..., title='Url')
    pretty_url: str = Field(..., title='Pretty Url')


class ApiRoutesTiktokModelsUserUserStatistics(BaseModel):
    followers: Optional[int] = Field(0, title='Followers')
    following: Optional[int] = Field(0, title='Following')
    likes: Optional[int] = Field(0, title='Likes')
    videos: Optional[int] = Field(0, title='Videos')


class Author(BaseModel):
    id: str = Field(..., title='Id')
    sec_uid: Optional[str] = Field(..., title='Sec Uid')
    username: str = Field(..., title='Username')
    full_name: str = Field(..., title='Full Name')
    avatar: Media = Field(..., description='Redistributed avatar image.')
    biography: Optional[str] = Field(None, title='Biography')
    url: str = Field(..., title='Url')


class HTTPValidationError(BaseModel):
    detail: Optional[List[ValidationError]] = Field(None, title='Detail')


class Highlight(BaseModel):
    id: str = Field(..., title='Id')
    title: str = Field(..., title='Title')
    cover: Media = Field(..., description='Redistributed media file.')
    user: Optional[SimpleUser] = Field(
        None, description='This is provided via /highlight.'
    )
    items: Optional[List[Story]] = Field(
        [], description='This is provided via /highlight.', title='Items'
    )
    url: str = Field(..., title='Url')

class InstagramUser(BaseModel):
    id: str = Field(..., title='Id')
    username: str = Field(..., title='Username')
    full_name: Optional[str] = Field(None, title='Full Name')
    avatar: Media = Field(..., description='Redistributed avatar image.')
    is_verified: Optional[bool] = Field(False, title='Is Verified')
    is_private: Optional[bool] = Field(False, title='Is Private')
    biography: Optional[str] = Field(None, title='Biography')
    statistics: ApiRoutesInstagramModelsUserUserStatistics
    links: Optional[List[ApiRoutesInstagramModelsUserBioLink]] = Field(
        [], title='Links'
    )
    highlights: Optional[List[Highlight]] = Field([], title='Highlights')
    url: str = Field(..., title='Url')


class TikTokPost(BaseModel):
    id: str = Field(..., title='Id')
    author: Author
    caption: Optional[str] = Field('..', title='Caption')
    statistics: Statistics
    video: Optional[Media] = None
    images: Optional[List[Media]] = Field([], title='Images')
    created_at: Optional[datetime] = Field(None, title='Created At')
    url: str = Field(..., title='Url')


class TikTokUser(BaseModel):
    id: str = Field(..., title='Id')
    sec_uid: str = Field(..., title='Sec Uid')
    username: str = Field(..., title='Username')
    nickname: str = Field(..., title='Nickname')
    biography: str = Field(..., title='Biography')
    avatar: Media = Field(..., description='Redistributed avatar image.')
    is_verified: Optional[bool] = Field(False, title='Is Verified')
    is_private: Optional[bool] = Field(False, title='Is Private')
    live_id: Optional[str] = Field(None, title='Live Id')
    link: Optional[ApiRoutesTiktokModelsUserBioLink] = None
    events: Optional[List[TikTokEvent]] = Field(None, title='Events')
    statistics: ApiRoutesTiktokModelsUserUserStatistics
    url: str = Field(..., title='Url')


class InstagramStoryResponse(BaseModel):
    user: InstagramUser
    stories: List[Story] = Field(..., title='Stories')


class TikTokPostsResponse(BaseModel):
    user: TikTokUser
    posts: List[TikTokPost] = Field(..., title='Posts')
