"""ファイル・フォルダ・URL を本文に変換する.

対応: .md / .txt / .html / .pdf / .docx / .csv、フォルダ (中のファイルを再帰で)、URL (http/https)。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
HTML_SUFFIXES = {".html", ".htm"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | {".pdf", ".docx", ".csv"}


@dataclass
class Loaded:
    title: str
    source: str
    text: str
    kind: str
    meta: dict[str, Any] = field(default_factory=dict)
    paragraphs: list[tuple[str, str]] | None = None  # 取り込み側で分割済みのとき (見出し, 本文)


def _html_to_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    # 見出しは Markdown の見出しにして、段落分割の境界に使う
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            h.replace_with(f"\n\n{'#' * level} {h.get_text(' ', strip=True)}\n\n")
    for p in soup.find_all(["p", "li", "tr", "br"]):
        p.append("\n\n" if p.name != "br" else "\n")
    lines = [ln.strip() for ln in soup.get_text().splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return title, text


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Markdown 先頭の YAML front matter を読む. 無ければ空の辞書と本文をそのまま返す."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    try:
        import yaml

        meta = yaml.safe_load(text[3:end].strip("\n")) or {}
    except Exception:  # noqa: BLE001
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, datetime):
            v = v.strftime("%Y-%m-%d")
        elif hasattr(v, "isoformat"):
            v = v.isoformat()[:10]
        out[str(k)] = v
    return out, text[end + 4:].lstrip("\n")


def load_text(path: Path) -> Loaded:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(raw)
    title = str(meta.get("title") or "")
    if not title:
        title = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), path.stem)
    return Loaded(title=title, source=str(path.resolve()), text=body, kind="markdown" if path.suffix.lower() in {".md", ".markdown"} else "text", meta=meta)


def load_html(path: Path) -> Loaded:
    title, text = _html_to_text(path.read_text(encoding="utf-8", errors="replace"))
    return Loaded(title=title or path.stem, source=str(path.resolve()), text=text, kind="html")


def load_pdf(path: Path) -> Loaded:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    title = ""
    try:
        title = (reader.metadata.title or "").strip() if reader.metadata else ""
    except Exception:  # noqa: BLE001
        title = ""
    text = "\n\n".join(f"[p.{i + 1}]\n{t}" for i, t in enumerate(pages) if t)
    return Loaded(title=title or path.stem, source=str(path.resolve()), text=text, kind="pdf", meta={"pages": len(pages)})


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def load_docx(path: Path) -> Loaded:
    """Word (.docx) の本文を読む. 見出しスタイルの段落は Markdown の見出しにする. 追加のライブラリは使わない."""
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines: list[str] = []
    for p in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()
        if not text:
            continue
        style = p.find(f"{_W}pPr/{_W}pStyle")
        sval = style.get(f"{_W}val", "") if style is not None else ""
        m = re.search(r"(?:Heading|見出し)\s*(\d)", sval, re.I)
        lines.append(f"{'#' * int(m.group(1))} {text}" if m else text)
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), path.stem)
    return Loaded(title=title, source=str(path.resolve()), text="\n\n".join(lines), kind="docx")


def load_csv(path: Path, rows_per_paragraph: int = 25) -> Loaded:
    """CSV は「列の説明」と「行のまとまり」を段落にする."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    headers = reader.fieldnames or []
    rows = list(reader)
    paragraphs = [("列", f"表「{path.stem}」({len(rows)} 行)\n列: " + ", ".join(headers))]
    for i in range(0, len(rows), rows_per_paragraph):
        batch = rows[i: i + rows_per_paragraph]
        body = "\n".join("; ".join(f"{k}={v}" for k, v in r.items() if v not in (None, "")) for r in batch)
        paragraphs.append((f"行 {i + 1}〜{i + len(batch)}", body))
    return Loaded(title=path.stem, source=str(path.resolve()), text="\n\n".join(b for _, b in paragraphs), kind="csv",
                  meta={"columns": headers, "rows": len(rows)}, paragraphs=paragraphs)


def load_file(path: Path) -> Loaded | None:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return load_text(path)
    if suffix in HTML_SUFFIXES:
        return load_html(path)
    if suffix == ".pdf":
        return load_pdf(path)
    if suffix == ".docx":
        return load_docx(path)
    if suffix == ".csv":
        return load_csv(path)
    return None


# フォルダを読むときに入らないフォルダ (隠しフォルダ、開発・アプリの部品、OS のフォルダ)
SKIP_DIRS = {
    "node_modules", "site-packages", "dist-packages", "__pycache__", "venv", "env", "build", "dist", "target", "vendor",
    "Library", "Applications", "Pictures", "Music", "Movies", "Public", "Trash",
}
SKIP_FILE_PREFIXES = ("license", "licence", "copying", "notice", "changelog", "authors", "contributors")
MAX_FILE_BYTES = 50 * 1024 * 1024


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS or name.endswith((".app", ".dist-info", ".egg-info", ".framework", ".bundle"))


def excluded_by_rules(path: str | Path) -> bool:
    """フォルダから読むときの規則 (隠しフォルダ・開発用のフォルダ・ライセンス文・対応外の形式) に当たるファイルか."""
    p = Path(path)
    if any(_skip_dir(part) for part in p.parts[1:-1]):
        return True
    return p.name.startswith(".") or p.name.lower().startswith(SKIP_FILE_PREFIXES) or p.suffix.lower() not in SUPPORTED_SUFFIXES


def iter_files(root: str | Path) -> Iterator[Path]:
    """フォルダの中の、取り込める形式のファイルを順に返す. 隠しフォルダや開発用のフォルダには入らない."""
    import os

    root = Path(root).expanduser()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        for name in sorted(filenames):
            if name.startswith(".") or name.lower().startswith(SKIP_FILE_PREFIXES):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield p


def scan(root: str | Path, limit: int = 100000) -> dict[str, Any]:
    """取り込む前に、フォルダの中の取り込める件数を数える."""
    root = Path(root).expanduser()
    if not root.exists():
        raise FileNotFoundError(str(root))
    if root.is_file():
        return {"path": str(root), "files": 1 if root.suffix.lower() in SUPPORTED_SUFFIXES else 0, "by_suffix": {root.suffix.lower(): 1}, "truncated": False}
    by: dict[str, int] = {}
    n = 0
    for p in iter_files(root):
        n += 1
        by[p.suffix.lower()] = by.get(p.suffix.lower(), 0) + 1
        if n >= limit:
            break
    return {"path": str(root), "files": n, "by_suffix": dict(sorted(by.items(), key=lambda kv: -kv[1])), "truncated": n >= limit}


def load_path(path: str | Path) -> Iterator[Loaded]:
    """ファイルまたはフォルダ (再帰) を読む. 対応していない形式のファイルや、隠しフォルダ・開発用のフォルダは飛ばす."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_dir():
        for p in iter_files(path):
            try:
                doc = load_file(p)
            except Exception:  # noqa: BLE001  壊れたファイル 1 件で全体を止めない
                continue
            if doc and doc.text.strip():
                yield doc
        return
    doc = load_file(path)
    if doc is None:
        raise ValueError(f"対応していない形式です: {path.suffix}")
    yield doc


def load_url(url: str) -> Loaded:
    import httpx

    resp = httpx.get(url, follow_redirects=True, timeout=30.0, headers={"User-Agent": "lexweft-lite/0.1"})
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as f:
            f.write(resp.content)
            f.flush()
            doc = load_pdf(Path(f.name))
        doc.source = url
        doc.kind = "url"
        return doc
    if "html" in ctype:
        title, text = _html_to_text(resp.text)
    else:
        title, text = "", resp.text
    return Loaded(title=title or url, source=url, text=text, kind="url")
