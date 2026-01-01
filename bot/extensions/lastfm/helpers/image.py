from io import BytesIO

from jishaku.functools import executor_function
from PIL import Image


@executor_function
def pixelate(buffer: bytes, level: int = 40) -> BytesIO:
    image = Image.open(BytesIO(buffer))
    small = image.resize(
        (image.width // level, image.height // level),
        resample=Image.NEAREST,  # type: ignore
    )
    image = small.resize(image.size, Image.NEAREST)  # type: ignore

    output = BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    image.close()

    return output
