"""保存先の決定. 環境変数 LEXWEFT_HOME があればそこ、無ければ ~/LeXWeftLite."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def distribution() -> dict[str, Any]:
    """配布版ごとの設定 (感想フォームの URL など). 配布用 ZIP を作るときに書き込む."""
    try:
        data = json.loads((Path(__file__).parent / "distribution.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    url = str(data.get("feedback_url") or "")
    return {"channel": str(data.get("channel") or "dev"), "feedback_url": url if url.startswith("https://") else ""}
