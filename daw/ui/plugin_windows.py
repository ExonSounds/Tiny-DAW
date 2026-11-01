"""Photographing a plugin's window, for the effects window's patch panel (plugin_pictures.py).
Windows only; elsewhere available() is False and nothing is photographed.

capture() takes what a window shows inside its frame, also while it is fully transparent (an
invisible window opened only to be photographed: plugin_host.photograph) and for plugins that draw
with the graphics card. Coordinates are physical screen pixels (Win32).
"""
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.GetDC.restype = wintypes.HDC
    _gdi32 = ctypes.windll.gdi32
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    for _name, _args in (("CreateCompatibleDC", (wintypes.HDC,)), ("CreateCompatibleBitmap", (wintypes.HDC, ctypes.c_int, ctypes.c_int)),
                         ("SelectObject", (wintypes.HDC, wintypes.HGDIOBJ)), ("DeleteObject", (wintypes.HGDIOBJ,)),
                         ("DeleteDC", (wintypes.HDC,)),
                         ("GetDIBits", (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                        ctypes.c_void_p, wintypes.UINT))):
        getattr(_gdi32, _name).argtypes = _args
    _user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    _user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2  # (full content: also plugins drawn with the GPU)


def available():
    return sys.platform == "win32"


def capture(hwnd):
    """A picture of a window's inside (without its title bar): (width, height, pixels as 32-bit BGRX rows,
    top to bottom), or None if it can't be taken (gone, minimized, empty)."""
    r = wintypes.RECT()
    if _user32.IsIconic(hwnd) or not _user32.GetClientRect(hwnd, ctypes.byref(r)) or r.right < 2 or r.bottom < 2:
        return None
    width, height = r.right, r.bottom
    screen = _user32.GetDC(hwnd)
    memory = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, width, height)
    try:
        _gdi32.SelectObject(memory, bitmap)
        if not _user32.PrintWindow(hwnd, memory, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32)  # (negative: top row first)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not _gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0):
            return None
        return width, height, pixels.raw
    finally:
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(hwnd, screen)
