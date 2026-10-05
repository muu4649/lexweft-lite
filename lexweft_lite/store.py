"""SQLite の保存先. 資料・段落・全文索引と、意味層 (型・概念・別名・根拠・関係) を持つ."""

from __future__ import annotations

import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'file',
    sha256 TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paragraphs_doc ON paragraphs(document_id, ordinal);

CREATE VIRTUAL TABLE IF NOT EXISTS paragraphs_fts USING fts5(
    text, heading, content='paragraphs', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS paragraphs_ai AFTER INSERT ON paragraphs BEGIN
    INSERT INTO paragraphs_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS paragraphs_ad AFTER DELETE ON paragraphs BEGIN
    INSERT INTO paragraphs_fts(paragraphs_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
END;

CREATE TABLE IF NOT EXISTS types (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#6b7280',
    ordinal INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL REFERENCES types(name) ON UPDATE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    PRIMARY KEY (concept_id, alias_norm)
);

CREATE TABLE IF NOT EXISTS evidence (
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (concept_id, paragraph_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_paragraph ON evidence(paragraph_id);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    dst_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    paragraph_id INTEGER REFERENCES paragraphs(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (src_id, dst_id, kind)
);

-- キーワードのつながり (keywords.py) 用: 資料ごとの語の出現数
CREATE TABLE IF NOT EXISTS doc_terms (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (document_id, term)
);
CREATE INDEX IF NOT EXISTS idx_doc_terms_term ON doc_terms(term);
CREATE TABLE IF NOT EXISTS doc_terms_done (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE
);

-- 取り込むフォルダの登録 (アプリ側の目録でだけ使う)
CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    last_run TEXT
);

-- この保存先の情報 (番号の範囲など)
CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# 資料・段落・概念・関係の番号は、保存先ごとに範囲を分ける (フォルダをまたいでも番号が重ならないように)
BASE = 10_000_000
ID_TABLES = ("documents", "paragraphs", "concepts", "relations")
# 番号を振り直すときに動かす列 (表, 列)
ID_COLUMNS = (("documents", "id"), ("paragraphs", "id"), ("paragraphs", "document_id"), ("concepts", "id"),
              ("aliases", "concept_id"), ("evidence", "concept_id"), ("evidence", "paragraph_id"),
              ("relations", "id"), ("relations", "src_id"), ("relations", "dst_id"), ("relations", "paragraph_id"),
              ("doc_terms", "document_id"), ("doc_terms_done", "document_id"),
              ("ldoc_clusters", "document_id"), ("ldoc_vectors", "document_id"),
              ("lpara_vectors", "paragraph_id"), ("lpara_vectors", "document_id"))

DEFAULT_TYPES = (
    ("課題", "資料が解こうとしている問題・困りごと", "#c2410c", 1),
    ("解決手段", "課題に対して資料が示す方法・構成・工夫", "#0e7490", 2),
)


def normalize(text: str) -> str:
    """表記ゆれを吸収した比較用の文字列 (全角半角・大文字小文字・空白)."""
    return "".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def nfc(text: str) -> str:
    """パスの表記をそろえる. macOS のファイル名は濁点・半濁点を分けて (NFD) 持つことがあるため、合成した形 (NFC) で比べる."""
    return unicodedata.normalize("NFC", text or "")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    """1 つの保存先 (アプリ側の目録、または登録したフォルダ 1 つ).

    root: フォルダの保存先なら、そのフォルダ. 資料の出所はフォルダからの相対パスで持つ (フォルダを動かしても壊れない)。
    store_no: 番号の範囲 (store_no × BASE から). アプリ側の目録は 0。
    """

    def __init__(self, path: str | Path, root: str | Path | None = None, store_no: int = 0, key: str = "central",
                 label: str = "", markdown_dir: str | Path | None = None):
        self.path = str(path)
        self.root = Path(root) if root else None
        self.key = key
        self.label = label or key
        self.markdown_dir = Path(markdown_dir) if markdown_dir else None
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # 接続は処理 (スレッド) ごとに分ける. 1 つの接続を画面の処理と裏の作り直しで同時に使うと壊れるため
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._closed = False
        self._lock = threading.RLock()
        first = self.conn
        # フォルダの中の保存先は 1 つのファイルにまとめる (フォルダをコピー・移動しても途中のデータが分かれない)
        first.execute("PRAGMA journal_mode = DELETE" if self.root else "PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        from .clusters import SCHEMA as LAYER_SCHEMA   # 自動の意味層 (まとまり・地図・ベクトル)

        self.conn.executescript(LAYER_SCHEMA)
        from .clusters import migrate as layer_migrate

        layer_migrate(self.conn)
        self._normalize_sources()
        with self.tx() as c:
            for name, desc, color, ordinal in DEFAULT_TYPES:
                c.execute("INSERT OR IGNORE INTO types(name, description, color, ordinal) VALUES (?, ?, ?, ?)", (name, desc, color, ordinal))
        self.store_no = self._ensure_range(store_no)

    # ---------------- 番号の範囲 ----------------
    def meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM store_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO store_meta(key, value) VALUES (?, ?)", (key, str(value)))

    def _ensure_range(self, store_no: int) -> int:
        """番号の範囲を決める. 前に別の番号で作った保存先 (別の PC から持ってきたフォルダなど) なら、番号を振り直す."""
        old = self.meta("store_no")
        if old is not None and int(old) != store_no:
            self.renumber(int(old), store_no)
        elif old is None:
            self.set_meta("store_no", str(store_no))
        if store_no:
            with self.tx() as c:
                for t in ID_TABLES:
                    row = c.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (t,)).fetchone()
                    floor = store_no * BASE
                    if row is None:
                        c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (t, floor))
                    elif int(row[0]) < floor:
                        c.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (floor, t))
        return store_no

    def renumber(self, old_no: int, new_no: int) -> None:
        """番号の範囲を old_no から new_no へ移す (資料・段落・概念・関係と、それを指す列すべて)."""
        delta = (new_no - old_no) * BASE
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.tx() as c:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                for t, col in ID_COLUMNS:
                    if t in tables:
                        c.execute(f"UPDATE {t} SET {col} = {col} + ? WHERE {col} IS NOT NULL", (delta,))
                c.execute("UPDATE sqlite_sequence SET seq = seq + ? WHERE name IN (%s)" % ",".join("?" * len(ID_TABLES)), (delta, *ID_TABLES))
                c.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES ('rebuild')")
                c.execute("INSERT OR REPLACE INTO store_meta(key, value) VALUES ('store_no', ?)", (str(new_no),))
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    # ---------------- 出所のパス (フォルダの保存先では相対パスで持つ) ----------------
    def _src_in(self, source: str) -> str:
        source = nfc(source)
        if self.root and not source.startswith(("http://", "https://", "text:")):
            try:
                return Path(source).relative_to(self.root).as_posix()
            except ValueError:
                return source
        return source

    def _src_out(self, stored: str) -> str:
        if self.root and stored and not stored.startswith(("/", "http://", "https://", "text:")) and ":" not in stored[:3]:
            return str(self.root / stored)
        return stored

    def _normalize_sources(self) -> None:
        """前の版で NFD のまま保存した出所を NFC にそろえる (同じ出所が NFC で既にあれば、古いほうを消す)."""
        rows = [(int(r[0]), r[1]) for r in self.conn.execute("SELECT id, source FROM documents") if nfc(r[1]) != r[1]]
        srcs = [r[0] for r in self.conn.execute("SELECT path FROM sources") if nfc(r[0]) != r[0]]
        if not rows and not srcs:
            return
        with self.tx() as c:
            for i, src in rows:
                if c.execute("SELECT 1 FROM documents WHERE source = ?", (nfc(src),)).fetchone():
                    c.execute("DELETE FROM documents WHERE id = ?", (i,))
                else:
                    c.execute("UPDATE documents SET source = ? WHERE id = ?", (nfc(src), i))
            for p in srcs:
                c.execute("UPDATE OR IGNORE sources SET path = ? WHERE path = ?", (nfc(p), p))
                c.execute("DELETE FROM sources WHERE path = ?", (p,))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    @property
    def conn(self) -> sqlite3.Connection:
        """この処理 (スレッド) 用の接続. 無ければ開く."""
        c = getattr(self._local, "conn", None)
        if c is None:
            if self._closed:
                raise RuntimeError("保存先は閉じています")
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA busy_timeout = 10000")   # アプリと Claude (MCP) が同時に使っても待って続ける
            self._local.conn = c
            with self._lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        self._closed = True
        with self._lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.ProgrammingError:   # 別の処理で開いた接続は、その処理が終わると閉じられる
                    pass
            self._conns.clear()

    # ---------------- 資料 ----------------
    def find_document_by_source(self, source: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE source = ?", (self._src_in(source),)).fetchone()

    def add_document(self, title: str, source: str, kind: str, sha256: str, meta: dict[str, Any],
                     paragraphs: list[tuple[str, str]]) -> int:
        """資料と段落 (見出し, 本文) を保存し、資料 ID を返す.

        同じ出所の資料があれば置き換える。そのとき、本文が変わっていない段落に付いていた根拠と関係は新しい段落に付け直す。
        """
        source = self._src_in(source)
        with self.tx() as c:
            old = c.execute("SELECT id FROM documents WHERE source = ?", (source,)).fetchone()
            carry_ev: list[sqlite3.Row] = []
            carry_rel: list[sqlite3.Row] = []
            if old is not None:
                carry_ev = c.execute("SELECT e.concept_id, e.note, e.created_at, p.text FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id"
                                     " WHERE p.document_id = ?", (old["id"],)).fetchall()
                carry_rel = c.execute("SELECT r.id, p.text FROM relations r JOIN paragraphs p ON p.id = r.paragraph_id WHERE p.document_id = ?",
                                      (old["id"],)).fetchall()
                c.execute("DELETE FROM documents WHERE id = ?", (old["id"],))
            doc_id = c.execute("INSERT INTO documents(title, source, kind, sha256, meta, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                               (title, source, kind, sha256, json.dumps(meta, ensure_ascii=False, default=str), now_iso())).lastrowid
            by_text: dict[str, int] = {}
            for i, (head, body) in enumerate(paragraphs):
                pid = c.execute("INSERT INTO paragraphs(document_id, ordinal, heading, text) VALUES (?, ?, ?, ?)", (doc_id, i, head, body)).lastrowid
                by_text.setdefault(body, int(pid))
            for r in carry_ev:
                if r["text"] in by_text:
                    c.execute("INSERT OR IGNORE INTO evidence(concept_id, paragraph_id, note, created_at) VALUES (?, ?, ?, ?)",
                              (r["concept_id"], by_text[r["text"]], r["note"], r["created_at"]))
            for r in carry_rel:
                if r["text"] in by_text:
                    c.execute("UPDATE relations SET paragraph_id = ? WHERE id = ?", (by_text[r["text"]], r["id"]))
        return int(doc_id)

    def delete_document(self, document_id: int) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM documents WHERE id = ?", (document_id,)).rowcount > 0

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        doc = dict(row)
        doc["meta"] = json.loads(doc["meta"] or "{}")
        doc["source"] = self._src_out(doc["source"])
        return doc

    def list_documents(self, query: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT d.id, d.title, d.source, d.kind, d.added_at,"
               " (SELECT COUNT(*) FROM paragraphs p WHERE p.document_id = d.id) AS paragraphs,"
               " (SELECT COUNT(DISTINCT e.concept_id) FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id WHERE p.document_id = d.id) AS concepts"
               " FROM documents d")
        params: list[Any] = []
        if query:
            sql += " WHERE d.title LIKE ? OR d.source LIKE ?"
            params += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY d.added_at DESC, d.id DESC LIMIT ?"
        params.append(-1 if limit is None else limit)
        out = [dict(r) for r in self.conn.execute(sql, params)]
        for d in out:
            d["source"] = self._src_out(d["source"])
        return out

    def count_documents(self, query: str | None = None) -> int:
        if query:
            return int(self.conn.execute("SELECT COUNT(*) FROM documents WHERE title LIKE ? OR source LIKE ?", (f"%{query}%", f"%{query}%")).fetchone()[0])
        return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def folders(self, depth: int = 4, limit: int = 40) -> list[dict[str, Any]]:
        """資料の出所をフォルダでまとめた件数 (depth はパスの先頭から数えた階層)."""
        from collections import Counter
        from pathlib import PurePath

        counts: Counter = Counter()
        for (src,) in self.conn.execute("SELECT source FROM documents"):
            src = self._src_out(src)
            if "://" in src or src.startswith("text:"):
                counts[src.split("://")[0] + "://" if "://" in src else "貼り付けた文章"] += 1
                continue
            parts = PurePath(src).parts[:-1]
            counts[str(PurePath(*parts[: max(1, min(depth, len(parts)))])) if parts else src] += 1
        return [{"folder": f, "documents": n} for f, n in counts.most_common(limit)]

    def delete_documents_under(self, prefix: str) -> list[int]:
        """出所がそのフォルダ (またはそのファイル) の資料をまとめて消し、消した資料 ID を返す."""
        prefix = nfc(prefix).rstrip("/") or "/"
        if self.root and (Path(prefix) == self.root):
            ids = [int(r[0]) for r in self.conn.execute("SELECT id FROM documents")]
            self.delete_documents(ids)
            return ids
        prefix = self._src_in(prefix)
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        ids = [int(r[0]) for r in self.conn.execute(
            "SELECT id FROM documents WHERE source = ? OR source LIKE ? ESCAPE '\\'", (prefix, like))]
        with self.tx() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                c.execute(f"DELETE FROM documents WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        return ids

    def delete_documents_under_preview(self, prefix: str) -> list[int]:
        """出所がそのフォルダの資料の ID (消さずに返す)."""
        prefix = nfc(prefix).rstrip("/") or "/"
        out = []
        for i, src in self.conn.execute("SELECT id, source FROM documents"):
            full = self._src_out(src)
            if full == prefix or full.startswith(prefix + "/"):
                out.append(int(i))
        return out

    def paragraphs_of(self, document_id: int, offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        """資料の段落を順に返す. offset は先頭からの段落数、limit は返す段落数 (None は最後まで)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT id, document_id, ordinal, heading, text FROM paragraphs WHERE document_id = ? ORDER BY ordinal LIMIT ? OFFSET ?",
            (document_id, -1 if limit is None else limit, max(0, offset)))]

    def get_paragraphs(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT p.id, p.document_id, p.ordinal, p.heading, p.text, d.title FROM paragraphs p JOIN documents d ON d.id = p.document_id WHERE p.id IN ({q})",
            ids).fetchall()
        return {int(r["id"]): dict(r) for r in rows}

    # ---------------- 登録したフォルダ ----------------
    def add_source(self, path: str) -> str:
        """フォルダを登録する. 別名のパス (/tmp → /private/tmp など) は、取り込んだ資料と同じ本当のパスにそろえる."""
        import os

        real = nfc(os.path.realpath(os.path.expanduser(path))).rstrip("/")
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO sources(path, added_at) VALUES (?, ?)", (real, now_iso()))
        return real

    def touch_source(self, path: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE sources SET last_run = ? WHERE path = ?", (now_iso(), nfc(path)))

    def remove_source(self, path: str) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM sources WHERE path = ?", (nfc(path),)).rowcount > 0

    def list_sources(self) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM sources ORDER BY added_at"):
            p = r["path"].rstrip("/")
            like = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            n = int(self.conn.execute("SELECT COUNT(*) FROM documents WHERE source = ? OR source LIKE ? ESCAPE '\\'", (p, like)).fetchone()[0])
            out.append({**dict(r), "documents": n, "exists": Path(p).exists()})
        return out

    def documents_outside_sources(self) -> list[int]:
        """登録したフォルダのどれにも入っていない、ファイルから取り込んだ資料 (貼り付けた文章・URL・画面から入れたファイルは除く)."""
        from pathlib import PurePath

        roots = [PurePath(nfc(r[0]).rstrip("/")) for r in self.conn.execute("SELECT path FROM sources")]
        files_dir = None
        try:
            from . import config

            files_dir = PurePath(nfc(str(config.home() / "files")))
        except Exception:  # noqa: BLE001
            pass
        out = []
        for i, src in self.conn.execute("SELECT id, source FROM documents"):
            if src.startswith(("text:", "http://", "https://")):
                continue
            p = PurePath(self._src_out(src))
            if files_dir and (p == files_dir or files_dir in p.parents):
                continue
            if any(p == r or r in p.parents for r in roots):
                continue
            out.append(int(i))
        return out

    def delete_documents(self, ids: list[int]) -> int:
        with self.tx() as c:
            for k in range(0, len(ids), 500):
                chunk = ids[k:k + 500]
                c.execute(f"DELETE FROM documents WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        return len(ids)

    def compact(self) -> None:
        """まとめて消したあとに、全文索引を作り直してファイルを詰める."""
        with self.tx() as c:
            c.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES ('rebuild')")
        self.conn.execute("VACUUM")

    def stats(self) -> dict[str, int]:
        one = lambda sql: int(self.conn.execute(sql).fetchone()[0])  # noqa: E731
        return {
            "documents": one("SELECT COUNT(*) FROM documents"),
            "paragraphs": one("SELECT COUNT(*) FROM paragraphs"),
            "concepts": one("SELECT COUNT(*) FROM concepts"),
            "relations": one("SELECT COUNT(*) FROM relations"),
            "evidence": one("SELECT COUNT(*) FROM evidence"),
            "documents_without_concepts": one(
                "SELECT COUNT(*) FROM documents d WHERE NOT EXISTS (SELECT 1 FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id WHERE p.document_id = d.id)"),
        }
