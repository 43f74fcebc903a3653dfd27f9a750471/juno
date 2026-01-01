from .user import TikTokUser
from .post import TikTokPost
from random import randint, choices
from string import hexdigits

def query(sec_uid: str, limit: int = 5) -> dict[str, str]:
    return {
        "aid": "1988",
        "app_language": "en",
        "app_name": "tiktok_web",
        "browser_language": "en-US",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "browser_platform": "Win32",
        "browser_version": "5.0 (Windows)",
        "channel": "tiktok_web",
        "cookie_enabled": "true",
        "count": "15",
        "cursor": "0",
        "device_id": str(randint(7250000000000000000, 7351147085025500000)),
        "device_platform": "web_pc",
        "focus_state": "true",
        "from_page": "user",
        "history_len": "2",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language": "en",
        "os": "windows",
        "priority_region": "",
        "referer": "",
        "region": "US",
        "screen_height": "1080",
        "screen_width": "1920",
        "secUid": sec_uid,
        "type": "1",
        "tz_name": "UTC",
        "verifyFp": f'verify_{"".join(choices(hexdigits, k=7))}',
        "webcast_language": "en",
    }
    
__all__ = ("TikTokUser", "TikTokPost", "query")