"""画面・MCP・コマンドが共有する保存先."""

from __future__ import annotations

from pathlib import Path

from . import config
from .library import Library
from .store import Store

_library: Library | None = None


def library() -> Library:
    global _library
    if _library is None:
        _library = Library(config.home())
    return _library


def store() -> Store:
    """アプリ側の目録 (フォルダの外の資料). フォルダの資料は library().store_for_folder() で開く."""
    return library().catalog


def markdown_dir() -> Path:
    return config.markdown_dir()


def reset() -> None:
    """テスト用: 保存先を開き直す."""
    global _library
    if _library is not None:
        _library.close()
    _library = None
