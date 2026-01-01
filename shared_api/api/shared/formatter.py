def shorten(value: str, length: int = 24, remove_chars: bool = True) -> str:
    if remove_chars:
        BROKEN_HYPERLINK = ["[", "]", "(", ")"]
        for char in BROKEN_HYPERLINK:
            value = value.replace(char, "")

    value = value.replace("\n", " ")

    if len(value) <= length:
        return value

    return value[: length - 2] + ".."