"""保存先の決定. 環境変数 LEXWEFT_HOME があればそこ、無ければ ~/LeXWeftLite."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    raw = os.environ.get("LEXWEFT_HOME", "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / "LeXWeftLite"
    path.mkdir(parents=True, exist_ok=True)
    (path / "markdown").mkdir(exist_ok=True)
    return path


def db_path() -> Path:
    return home() / "lexweft.sqlite3"


def markdown_dir() -> Path:
    return home() / "markdown"
