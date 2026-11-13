"""Logging helpers. The library never configures the root logger."""
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import logging.handlers
import sys

ROOT_NAME = "c4d_collab"


class _NullHandler(logging.Handler):
    def emit(self, record):
        pass


def get_logger(name=None):
    logger = logging.getLogger(ROOT_NAME if not name else "%s.%s" % (ROOT_NAME, name))
    root = logging.getLogger(ROOT_NAME)
    if not root.handlers:
        root.addHandler(_NullHandler())
    return logger


MAX_LOG_BYTES = 5 * 1024 * 1024   # the log of a long session reached 700 thousand lines: it is kept at 5 MB (+ 2 older files)


def log_to_file(path, level=logging.INFO):
    # Python 3: UTF-8, so a file name with a non-ASCII letter is not lost under a Windows code page. Python 2 (R19)
    # writes its byte strings as they are: an encoding there would fail on every message that holds non-ASCII bytes.
    encoding = "utf-8" if sys.version_info[0] >= 3 else None
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=2, encoding=encoding)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger(ROOT_NAME)
    root.addHandler(handler)
    root.setLevel(level)
    return handler
