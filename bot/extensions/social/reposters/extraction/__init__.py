from logging import getLogger
from typing import Optional

from jishaku.functools import executor_function
from yt_dlp import DownloadError, YoutubeDL

from .model import Information

logger = getLogger("bot.reposter")
# logger.setLevel("CRITICAL")


@executor_function
def download(url: str, options: dict = {}, **kwargs) -> Optional[Information]:
    # if "download" not in kwargs:
    #     kwargs["download"] = False

    YDL_OPTS = {
        "logger": logger,
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "final_ext": "mp4",
        "age_limit": 18,
        "concurrent_fragment_downloads": 12,
        "playlistend": 6,
        "format": "bv*[filesize<100M][height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]",
        "http_chunk_size": 1048576,
        "noplaylist": True,
        "restrictfilenames": True,
        "cookiefile": "cookies.txt",
        "download": True,
        "outtmpl": "/tmp/juno/%(id)s.%(ext)s",
        **options,
    }
    if "youtu" in url:
        YDL_OPTS["proxy"] = "socks5://127.0.0.1:40000"
        YDL_OPTS["format"] = "bv*[vcodec=vp9][filesize<100M][height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]"

    with YoutubeDL(YDL_OPTS) as ydl:
        try:
            info = ydl.extract_info(url, **kwargs)
        except DownloadError:
            return

        if info:
            return Information(**info)
