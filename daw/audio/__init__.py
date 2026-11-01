"""Audio side: device discovery, file loading, mixing and the output stream. No Qt in here.

sounddevice only exposes ASIO when SD_ENABLE_ASIO is set before it is first
imported. Every module that imports sounddevice lives in this package, so
setting it here (the package __init__ always runs first) covers them all.
"""
import os

os.environ.setdefault("SD_ENABLE_ASIO", "1")
