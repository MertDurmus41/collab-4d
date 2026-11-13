"""Python 2.7 / 3.x helpers used by the core."""
from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import time

PY2 = sys.version_info[0] == 2

if PY2:  # pragma: no cover - exercised only inside old Cinema 4D versions
    import Queue as queue  # noqa: N813
    text_type = unicode  # noqa: F821
    string_types = (str, unicode)  # noqa: F821
    integer_types = (int, long)  # noqa: F821
else:
    import queue  # noqa: F401
    text_type = str
    string_types = (str,)
    integer_types = (int,)

number_types = integer_types + (float,)


def to_bytes(value, encoding="utf-8"):
    if isinstance(value, bytes):
        return value
    return value.encode(encoding)


def to_text(value, encoding="utf-8"):
    if isinstance(value, text_type):
        return value
    return value.decode(encoding)


def error_text(exc):
    """Readable text for any exception.

    On Python 2 with a non-English Windows (e.g. Turkish), OS error messages are
    bytes in the local code page; formatting them into unicode would raise
    UnicodeDecodeError and turn a simple 'cannot connect' into a crash.
    """
    if not PY2:
        return str(exc)
    try:  # pragma: no cover - Python 2 only
        return unicode(exc)  # noqa: F821
    except UnicodeError:  # pragma: no cover
        pass
    try:  # pragma: no cover
        raw = str(exc)
    except Exception:  # pragma: no cover
        raw = repr(exc)
    import locale  # pragma: no cover
    for encoding in (locale.getpreferredencoding(False), "mbcs", "utf-8"):  # pragma: no cover
        try:
            return raw.decode(encoding)
        except (UnicodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")  # pragma: no cover


def is_fatal(exc):
    """True for exceptions that must never be swallowed. Cinema 4D reports some failures (a missing
    plugin type, a dead object) as a bare BaseException, so safety nets catch BaseException and
    re-raise only these."""
    return isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit))


def monotonic():
    """Monotonic clock in seconds (time.monotonic does not exist on 2.7)."""
    clock = getattr(time, "monotonic", None)
    if clock is not None:
        return clock()
    return time.time()  # pragma: no cover


def iteritems(mapping):
    return mapping.items() if not PY2 else mapping.iteritems()  # noqa
