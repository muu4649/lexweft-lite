"""資料を入れるフォルダ: これから資料を入れていくフォルダを 1 つ決める.

決めたフォルダは登録して「変わったら自動で取り込む」にする。画面に落としたファイルや貼り付けた文章も、このフォルダ
(またはその中のグループ = サブフォルダ) にファイルとして保存してから取り込む。資料はいつも利用者のフォルダの中にあり、
Finder でも見える。すでにある別のフォルダを登録して意味層を作ることも、これまでどおりできる。
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

from .library import LAYER_DIR, Library, cloud_reason, real
from .loaders import SUPPORTED_SUFFIXES, excluded_by_rules

KEY = "inbox"
DEFAULT_NAME = "LeXWeftの資料"
MAX_FILE_BYTES = 200_000_000   # 1 ファイルの上限
_BAD_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")


def suggestion() -> str:
    """おすすめの場所 (書類フォルダの中)."""
    docs = Path.home() / "Documents"
    return str((docs if docs.is_dir() else Path.home()) / DEFAULT_NAME)


def _allowed(p: Path) -> bool:
    """作ってよい・決めてよい場所か. ホームフォルダの下と、外付けのディスクだけ (システムの場所には作らない)."""
    home = Path(real(str(Path.home())))
    if p == home:
        return False
    if p.is_relative_to(home):
        return True
    # 確かめや録画のために、許可する場所を足せる (ふだんは使わない)
    extra = [Path(real(x)) for x in os.environ.get("LEXWEFT_INBOX_ROOTS", "").split(os.pathsep) if x.strip()]
    if any(p != r and p.is_relative_to(r) for r in extra):
        return True
    system = platform.system()
    if system == "Darwin":
        return p.is_relative_to("/Volumes") and len(p.parts) > 3
    if system == "Windows":
        blocked = [os.environ.get(k, "") for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
        return len(p.parts) > 1 and not any(b and p.is_relative_to(b) for b in blocked)
    return any(p.is_relative_to(r) for r in ("/mnt", "/media")) and len(p.parts) > 3


def path(lib: Library) -> str | None:
    p = lib.catalog.meta(KEY)
    return p or None


def info(lib: Library) -> dict[str, Any]:
    p = path(lib)
    out: dict[str, Any] = {"path": p, "suggestion": suggestion(), "exists": False, "groups": [], "files": 0}
    if not p:
        out["reason"] = cloud_reason(real(out["suggestion"]))
        return out
    root = Path(p)
    out["exists"] = root.is_dir()
    out["reason"] = cloud_reason(p)
    if out["exists"]:
        def count(d: Path) -> int:
            return sum(1 for f in d.rglob("*") if f.is_file() and LAYER_DIR not in f.parts and not excluded_by_rules(f))

        out["groups"] = sorted(({"name": d.name, "files": count(d)} for d in root.iterdir()
                                if d.is_dir() and not d.name.startswith((".", "_"))), key=lambda g: g["name"])
        out["files"] = count(root)
    src = lib.source(p)
    out["registered"] = bool(src)
    out["auto"] = bool(src and src.get("auto"))
    return out


def choose(lib: Library, target: str) -> dict[str, Any]:
    """資料を入れるフォルダを決める. 無ければ作り、登録して「変わったら自動で取り込む」にする."""
    raw = (target or "").strip()
    if not raw:
        raise ValueError("フォルダの場所を入れてください")
    p = Path(real(raw))
    if not p.is_absolute():
        raise ValueError("フォルダの場所は、/ から始まる形 (Windows は C:\\ など) で入れてください")
    if not _allowed(p):
        raise ValueError("ホームフォルダの中 (書類フォルダなど) か、外付けのディスクを選んでください")
    if p.exists() and not p.is_dir():
        raise ValueError("同じ名前のファイルがあります。別の名前にしてください")
    p.mkdir(parents=True, exist_ok=True)
    lib.register(str(p))
    lib.set_auto(str(p), True)
    lib.catalog.set_meta(KEY, str(p))
    return info(lib)


def _root(lib: Library) -> Path:
    p = path(lib)
    if not p or not Path(p).is_dir():
        raise ValueError("先に「資料を入れるフォルダ」を決めてください")
    return Path(p)


def safe_name(name: str, what: str = "名前") -> str:
    """ファイル名・フォルダ名として安全な名前にする (区切り文字・制御文字を除き、隠しファイルや .. を断る)."""
    raw = (name or "").strip()
    n = _BAD_CHARS.sub("_", raw).strip(" .")
    if not n or raw.startswith((".", "_")) or n.startswith((".", "_")) or n == LAYER_DIR:
        raise ValueError(f"その{what}は使えません")
    return n[:120]


def group_dir(lib: Library, group: str | None) -> Path:
    root = _root(lib)
    if not group:
        return root
    d = root / safe_name(group, "グループの名前")
    if not d.is_dir():
        raise ValueError(f"グループ「{group}」がありません")
    return d


def add_group(lib: Library, name: str) -> dict[str, Any]:
    d = _root(lib) / safe_name(name, "グループの名前")
    d.mkdir(exist_ok=True)
    return info(lib)


def _free(dest: Path) -> Path:
    """同じ名前のファイルがあれば「名前 (2).md」のようにずらす (上書きしない)."""
    if not dest.exists():
        return dest
    for i in range(2, 10000):
        cand = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not cand.exists():
            return cand
    raise ValueError("同じ名前のファイルが多すぎます")


def save_file(lib: Library, filename: str, data: bytes, group: str | None = None) -> Path:
    name = safe_name(Path(filename or "").name, "ファイル名")
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"「{name}」は取り込めない形式です (md / txt / html / pdf / docx / csv)")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"「{name}」は大きすぎます ({MAX_FILE_BYTES // 1_000_000} MB まで)")
    dest = _free(group_dir(lib, group) / name)
    dest.write_bytes(data)
    return dest


def save_text(lib: Library, title: str, text: str, group: str | None = None) -> Path:
    title = (title or "").strip() or "メモ"
    body = text if text.lstrip().startswith("#") else f"# {title}\n\n{text}"
    stem = safe_name(title, "題名")[:80]
    dest = _free(group_dir(lib, group) / f"{stem}.md")
    dest.write_text(body.rstrip() + "\n", encoding="utf-8")
    return dest


def open_in_finder(lib: Library, group: str | None = None) -> str:
    """資料を入れるフォルダ (かその中のグループ) を Finder / エクスプローラーで開く. ほかの場所は開かない."""
    d = group_dir(lib, group)
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["/usr/bin/open", str(d)], check=False, timeout=20)
    elif system == "Windows":
        os.startfile(str(d))  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.run(["xdg-open", str(d)], check=False, timeout=20)  # noqa: S607
    return str(d)
