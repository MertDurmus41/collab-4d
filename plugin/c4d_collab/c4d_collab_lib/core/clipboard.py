"""Put text on the system clipboard (Cinema 4D's Python can read the clipboard but not write it).

Windows: the clipboard API through ctypes (Unicode text, works on Python 2.7 and 3).
macOS: pbcopy. Linux: wl-copy, xclip or xsel when one is installed.
Returns False instead of raising: a missing clipboard must never break a button.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import subprocess
import sys
import time

from .compat import is_fatal, to_text

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def set_text(text):
    text = to_text(text)
    try:
        if sys.platform == "win32":
            return _set_windows(text)
        if sys.platform == "darwin":
            return _pipe(["pbcopy"], text)
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if _pipe(command, text):
                return True
        return False
    except BaseException as exc:
        if is_fatal(exc):
            raise
        return False


def _pipe(command, text):
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
    except (OSError, ValueError):
        return False
    process.communicate(text.encode("utf-8"))
    return process.returncode == 0


def _set_windows(text):
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    # 64-bit Windows: handles and pointers do not fit the default C int
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    data = (text + "\0").encode("utf-16-le")
    for _attempt in range(10):   # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)   # the clipboard owns the memory only after a successful call
            return False
        return True
    finally:
        user32.CloseClipboard()
