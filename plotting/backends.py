from enum import Enum


class Backend(str, Enum):
    """Available plotting backends."""

    MATPLOTLIB = "matplotlib"
    ORIGIN = "origin"
    BOTH = "both"


def wants_matplotlib(backend: str | Backend) -> bool:
    """Return ``True`` if Matplotlib output is requested."""

    b = str(backend).lower()
    return b in (Backend.MATPLOTLIB.value, Backend.BOTH.value)


def wants_origin(backend: str | Backend) -> bool:
    """Return ``True`` if Origin output is requested."""

    b = str(backend).lower()
    return b in (Backend.ORIGIN.value, Backend.BOTH.value)
