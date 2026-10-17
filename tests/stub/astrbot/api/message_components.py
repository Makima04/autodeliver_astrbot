from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Plain:
    text: str


@dataclass
class Image:
    path: str = ""
    url: str = ""

    @classmethod
    def fromFileSystem(cls, path: str) -> Image:
        return cls(path=path)

    @classmethod
    def fromURL(cls, url: str) -> Image:
        return cls(url=url)


@dataclass
class At:
    qq: str


__all__ = ["Plain", "Image", "At"]
