from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

TWITCH_HELIX_BASE = "https://api.twitch.tv/helix"
TWITCH_EVENT_BASE = TWITCH_HELIX_BASE + "/eventsub"
TWITCH_SUB_BASE = TWITCH_EVENT_BASE + "/subscriptions"
TWITCH_ID_BASE = "https://id.twitch.tv"
TWITCH_BASE = "https://twitch.tv"


class Headers(BaseModel):
    message_id: str = Field(alias="Twitch-Eventsub-Message-Id")
    message_retry: int = Field(alias="Twitch-Eventsub-Message-Retry")
    message_type: str = Field(alias="Twitch-Eventsub-Message-Type")
    signature: str = Field(alias="Twitch-Eventsub-Message-Signature")
    subscription_type: str = Field(alias="Twitch-Eventsub-Subscription-Type")
    subscription_version: str = Field(alias="Twitch-Eventsub-Subscription-Version")
    timestamp: datetime = Field(alias="Twitch-Eventsub-Message-Timestamp")
    raw_timestamp: str = Field(alias="Twitch-Eventsub-Message-Timestamp")
