from __future__ import annotations

import logging
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("astrbot")


class AstrBotConfig(dict):
    """替身：AstrBot 的配置对象本质就是 dict 子类。"""

    def save_config(self) -> None:  # pragma: no cover
        pass


__all__ = ["AstrBotConfig", "logger"]
