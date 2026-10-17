"""独立身份 OPPO 录音客户端。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("oconnect-client")
except PackageNotFoundError:
    __version__ = "0+uninstalled"
