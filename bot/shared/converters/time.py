import re
from datetime import timedelta
from typing import List, Optional

from discord.ext.commands import Converter
from humanfriendly import format_timespan

from bot.core import Context

DURATION_PATTERN = r"\s?".join(
    [
        r"((?P<years>\d+?)\s?(years?|y))?",
        r"((?P<months>\d+?)\s?(months?|mo))?",
        r"((?P<weeks>\d+?)\s?(weeks?|w))?",
        r"((?P<days>\d+?)\s?(days?|d))?",
        r"((?P<hours>\d+?)\s?(hours?|hrs|hr?))?",
        r"((?P<minutes>\d+?)\s?(minutes?|mins?|m(?!o)))?",
        r"((?P<seconds>\d+?)\s?(seconds?|secs?|s))?",
    ]
)


class Duration(Converter[timedelta]):
    min: Optional[timedelta]
    max: Optional[timedelta]
    units: List[str]

    def __init__(
        self,
        min: Optional[timedelta] = None,
        max: Optional[timedelta] = None,
        units: Optional[List[str]] = None,
    ):
        self.min = min
        self.max = max
        self.units = units or ["weeks", "days", "hours", "minutes", "seconds"]

    async def convert(self, ctx: Context, argument: str) -> timedelta:
        matches = re.fullmatch(DURATION_PATTERN, argument, re.IGNORECASE)
        if not matches:
            raise ValueError("The duration provided is invalid, e.g. `2d3h`")

        units = {
            unit: int(amount) for unit, amount in matches.groupdict().items() if amount
        }
        for unit in units:
            if unit not in self.units:
                raise ValueError(f"The unit `{unit}` is not allowed for this command")

        try:
            duration = timedelta(**units)
        except OverflowError as exc:
            raise ValueError("The duration provided is too large") from exc

        if self.min and duration < self.min:
            raise ValueError(
                f"The duration provided is too short, minimum is `{format_timespan(self.min)}`"
            )

        if self.max and duration > self.max:
            raise ValueError(
                f"The duration provided is too long, maximum is `{format_timespan(self.max)}`"
            )

        return duration
