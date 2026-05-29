from __future__ import annotations

import logging
import sys


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("pilates_review")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger

logger = setup_logging()
