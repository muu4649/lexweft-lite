"""画面・MCP・コマンドが共有する保存先."""

from __future__ import annotations

from pathlib import Path

from . import config
from .store import Store

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.db_path())
    return _store


def markdown_dir() -> Path:
    return config.markdown_dir()


def reset() -> None:
    """テスト用: 保存先を開き直す."""
    global _store
    if _store is not None:
        _store.close()
    _store = None
