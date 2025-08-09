from typing import NamedTuple


class File(NamedTuple):
    key: str  # could be path or id
    name: str
    extension: str
    size: float
