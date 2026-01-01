from bot.core import Juno
from .. import Reposter


class Reddit(Reposter):
    def __init__(self, bot: Juno, *, add_listener: bool = True):
        super().__init__(
            bot,
            regex=[
                r"\<?(https?://(?:www\.)?reddit\.com/r/(?P<channel>[^/]+)/(?:comments|s)/(?P<id>[^/?#&]+))\>?"
            ],
            add_listener=add_listener,
        )
