from logging import DEBUG, Formatter

from rich.highlighter import NullHighlighter
from rich.logging import RichHandler

def setup_rich_logging():
    return Formatter(
        "%(asctime)s %(levelname)s %(name)s -> %(message)s",
        datefmt="[%X]",
    ), RichHandler(
        DEBUG,
        rich_tracebacks=True,
        highlighter=NullHighlighter(),
        markup=True,
    )
