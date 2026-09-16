"""Logging setup.

Every call turn, every validation rejection, and the final collected payload
go to stdout (which Railway/Render capture) and to logs/app.log for local
inspection.
"""

import logging
import os
import sys

from app.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    try:
        os.makedirs("logs", exist_ok=True)
        handlers.append(logging.FileHandler("logs/app.log", encoding="utf-8"))
    except OSError:
        # Read-only filesystem on some hosts — stdout alone is fine.
        pass

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=_FORMAT,
        handlers=handlers,
        force=True,
    )
    # These are chatty and add nothing.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
