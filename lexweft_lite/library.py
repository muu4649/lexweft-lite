"""登録したフォルダごとの保存先をまとめて扱う.

  アプリ側の目録 (~/LeXWeftLite/lexweft.sqlite3)
      登録したフォルダの一覧と、フォルダの外の資料 (画面から入れたファイル・貼り付けた文章・URL)
  フォルダごとの保存先
      ふつうは、そのフォルダの中の _LeXWeft/ に置く (資料・段落・意味層・課題と解決手段)
      クラウド同期のフォルダや書き込めないフォルダは、アプリ側 (~/LeXWeftLite/stores/) に置き、理由を残す

資料・段落・概念・関係の番号は、保存先ごとに範囲を分けている (store.BASE)。番号から保存先が分かる。
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .store import BASE, Store, nfc, now_iso

LAYER_DIR = "_LeXWeft"
CENTRAL = "central"
CENTRAL_LABEL = "フォルダの外の資料"
# クラウド同期のフォルダと見なすパスの一部 (データベースが同期で壊れやすい)
CLOUD_MARKERS = ("/Library/CloudStorage/", "/Library/Mobile Documents/", "/iCloud Drive", "/Dropbox", "/OneDrive", "/Google Drive",
                 "/GoogleDrive", "/My Drive", "/Box Sync", "/Box/", "/pCloud", "\\OneDrive", "\\Dropbox", "\\Google Drive")

CATALOG_COLUMNS = (("store_no", "INTEGER"), ("location", "TEXT NOT NULL DEFAULT 'folder'"), ("reason", "TEXT NOT NULL DEFAULT ''"),
                   ("auto", "INTEGER NOT NULL DEFAULT 1"), ("scan_sig", "TEXT"), ("last_auto", "TEXT"))


def real(path: str) -> str:
    return nfc(os.path.realpath(os.path.expanduser(path))).rstrip("/") or "/"


def cloud_reason(path: str) -> str:
    p = path + "/"
    for m in CLOUD_MARKERS:
        if m in p:
            return "クラウド同期のフォルダなので、同期で意味層のデータが壊れないよう、アプリ側に保存します"
    return ""


class Library:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.catalog = Store(self.home / "lexweft.sqlite3", key=CENTRAL, label=CENTRAL_LABEL, markdown_dir=self.home / "markdown")
        self._stores: dict[str, Store] = {}
        self._lock = threading.RLock()
        have = {r[1] for r in self.catalog.conn.execute("PRAGMA table_info(sources)")}
        legacy = "store_no" not in have
        for col, typ in CATALOG_COLUMNS:
            if col not in have:
                self.catalog.conn.execute(f"ALTER TABLE sources ADD COLUMN {col} {typ}")
        self.catalog.conn.commit()
        if legacy:
            self._migrate_legacy()

    # ---------------- 目録 ----------------
    def sources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.catalog.conn.execute("SELECT * FROM sources ORDER BY added_at")]

    def source(self, path: str) -> dict[str, Any] | None:
        row = self.catalog.conn.execute("SELECT * FROM sources WHERE path = ?", (real(path),)).fetchone()
        return dict(row) if row else None

    def _location(self, path: str) -> tuple[str, str]:
        reason = cloud_reason(path)
        if reason:
            return "central", reason
        try:
            (Path(path) / LAYER_DIR).mkdir(exist_ok=True)
            probe = Path(path) / LAYER_DIR / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            return "central", "このフォルダには書き込めないので、アプリ側に保存します"
        return "folder", ""

    def register(self, path: str) -> dict[str, Any]:
        """フォルダを登録する (保存先を決めて作る). 既に登録してあれば、その情報を返す."""
        p = real(path)
        if not Path(p).is_dir():
            raise ValueError("フォルダが見つかりません")
        if Path(p) in (Path.home(), Path("/"), Path(Path(p).anchor)):
            raise ValueError("ホームフォルダや PC 全体は登録できません。資料の入ったフォルダを選んでください")
        with self._lock:
            row = self.source(p)
            if row:
                return row
            location, reason = self._location(p)
            used = {int(r[0]) for r in self.catalog.conn.execute("SELECT store_no FROM sources WHERE store_no IS NOT NULL")}
            # 別の PC から持ってきたフォルダなら、前の番号が空いていればそのまま使う
            wanted = None
            existing = Path(p) / LAYER_DIR / "lexweft.sqlite3"
            if location == "folder" and existing.exists():
                try:
                    import sqlite3

                    con = sqlite3.connect(existing)
                    r = con.execute("SELECT value FROM store_meta WHERE key = 'store_no'").fetchone()
                    con.close()
                    wanted = int(r[0]) if r else None
                except Exception:  # noqa: BLE001
                    wanted = None
            no = wanted if wanted and wanted not in used else max(used | {0}) + 1
            with self.catalog.tx() as c:
                c.execute("INSERT INTO sources(path, added_at, store_no, location, reason) VALUES (?, ?, ?, ?, ?)",
                          (p, now_iso(), no, location, reason))
            self.store_for_folder(p)
            return self.source(p)

    def unregister(self, path: str, delete_data: bool = False) -> dict[str, Any]:
        """登録を外す. delete_data のときは、そのフォルダの意味層のデータ (_LeXWeft またはアプリ側の保存先) も消す. 元の資料は消さない."""
        p = real(path)
        row = self.source(p)
        if not row:
            return {"removed": False, "deleted": False}
        with self._lock:
            st = self._stores.pop(p, None)
            if st:
                st.close()
            with self.catalog.tx() as c:
                c.execute("DELETE FROM sources WHERE path = ?", (p,))
            deleted = False
            if delete_data:
                d = self._data_dir(row)
                if d.exists() and d.name in (LAYER_DIR, f"{row['store_no']}"):
                    shutil.rmtree(d)
                    deleted = True
        return {"removed": True, "deleted": deleted}

    def set_auto(self, path: str, auto: bool) -> None:
        with self.catalog.tx() as c:
            c.execute("UPDATE sources SET auto = ? WHERE path = ?", (1 if auto else 0, real(path)))

    def touch(self, path: str, scan_sig: str | None = None, auto: bool = False) -> None:
        with self.catalog.tx() as c:
            c.execute("UPDATE sources SET last_run = ? WHERE path = ?", (now_iso(), real(path)))
            if scan_sig is not None:
                c.execute("UPDATE sources SET scan_sig = ? WHERE path = ?", (scan_sig, real(path)))
            if auto:
                c.execute("UPDATE sources SET last_auto = ? WHERE path = ?", (now_iso(), real(path)))

    # ---------------- 保存先を開く ----------------
    def _data_dir(self, row: dict[str, Any]) -> Path:
        if row["location"] == "folder":
            return Path(row["path"]) / LAYER_DIR
        return self.home / "stores" / str(row["store_no"])

    def store_for_folder(self, path: str) -> Store:
        p = real(path)
        with self._lock:
            if p in self._stores:
                return self._stores[p]
            row = self.source(p)
            if not row:
                raise KeyError(f"登録していないフォルダです: {p}")
            d = self._data_dir(row)
            label = "/".join(Path(p).parts[-2:]) if len(Path(p).parts) >= 2 else p
            st = Store(d / "lexweft.sqlite3", root=p, store_no=int(row["store_no"]), key=p, label=label, markdown_dir=d / "markdown")
            if row["location"] == "folder":
                readme = d / "これは何？.txt"
                if not readme.exists():
                    readme.write_text("このフォルダは LeXWeft Lite が作った、このフォルダの資料の意味層のデータです。\n"
                                      "消すと、まとまり・地図・Claude が書いた課題と解決手段も消えます（元の資料は消えません）。\n"
                                      "フォルダごと移動・コピーすると、意味層も一緒に付いていきます。\n", encoding="utf-8")
            self._stores[p] = st
            return st

    def stores(self, include_central: bool = True) -> list[Store]:
        """開ける保存先すべて (アプリ側の目録 + 登録したフォルダ). 見つからないフォルダは飛ばす."""
        out = [self.catalog] if include_central else []
        for row in self.sources():
            if Path(row["path"]).exists() or row["location"] == "central":
                try:
                    out.append(self.store_for_folder(row["path"]))
                except Exception:  # noqa: BLE001
                    continue
        return out

    def for_scope(self, scope: str | None) -> Store:
        if not scope or scope == CENTRAL:
            return self.catalog
        return self.store_for_folder(scope)

    def for_id(self, any_id: int) -> Store:
        """資料・段落・概念・関係の番号から、保存先を返す."""
        no = int(any_id) // BASE
        if no == 0:
            return self.catalog
        for row in self.sources():
            if int(row["store_no"] or 0) == no:
                return self.store_for_folder(row["path"])
        raise KeyError(f"番号 {any_id} の保存先が見つかりません (フォルダの登録を外したか、フォルダが見つかりません)")

    def for_source(self, path: str) -> Store:
        """ファイルのパスから、それを入れる保存先を返す (登録したフォルダの中なら、そのフォルダ)."""
        p = Path(real(path))
        best = None
        for row in self.sources():
            root = Path(row["path"])
            if p == root or root in p.parents:
                if best is None or len(str(root)) > len(best):
                    best = str(root)
        return self.store_for_folder(best) if best else self.catalog

    def scope_label(self, scope: str) -> str:
        return self.for_scope(scope).label

    def close(self) -> None:
        for st in self._stores.values():
            st.close()
        self.catalog.close()

    # ---------------- 前の版からの引っ越し ----------------
    def _migrate_legacy(self) -> None:
        """前の版 (すべてを 1 つのデータベースに入れていた) で登録したフォルダの資料を、フォルダごとの保存先へ移す.

        資料はファイルから読み直し、課題と解決手段 (概念・別名・根拠・関係) は、同じ本文の段落に付け直す。
        """
        from .ingest import ingest

        rows = [dict(r) for r in self.catalog.conn.execute("SELECT path FROM sources")]
        if not rows:
            return
        old = self.catalog
        with old.tx() as c:
            c.execute("DELETE FROM sources")
        for r in rows:
            p = r["path"]
            if not Path(p).is_dir():
                continue
            try:
                self.register(p)
            except ValueError:
                continue
            st = self.store_for_folder(p)
            ingest(st, p, st.markdown_dir)
            moved = old.delete_documents_under_preview(p)
            copied = _copy_concepts(old, st, moved)
            old.delete_documents(moved)
            # 移した概念のうち、元の場所に根拠が残っていないものは消す (同じ概念が 2 か所にならないように)
            with old.tx() as c:
                for cid in copied:
                    if not c.execute("SELECT 1 FROM evidence WHERE concept_id = ?", (cid,)).fetchone():
                        c.execute("DELETE FROM concepts WHERE id = ?", (cid,))


def _copy_concepts(src: Store, dst: Store, doc_ids: list[int]) -> list[int]:
    """src の資料 (doc_ids) に根拠を持つ概念を、dst の同じ本文の段落に付け直して写す. 写した概念の (src の) 番号を返す."""
    if not doc_ids:
        return []
    q = ",".join("?" * len(doc_ids))
    ev = src.conn.execute(
        f"SELECT e.concept_id, e.note, p.text FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id WHERE p.document_id IN ({q})", doc_ids).fetchall()
    if not ev:
        return []
    by_text = {r["text"]: int(r["id"]) for r in dst.conn.execute("SELECT id, text FROM paragraphs")}
    from . import layer as ly

    new_id: dict[int, int] = {}
    for r in ev:
        cid = int(r["concept_id"])
        if cid not in new_id:
            c = src.conn.execute("SELECT * FROM concepts WHERE id = ?", (cid,)).fetchone()
            aliases = [a[0] for a in src.conn.execute("SELECT alias FROM aliases WHERE concept_id = ?", (cid,))]
            new_id[cid] = ly.upsert_concept(dst, c["name"], c["type"], c["description"] or None, aliases)["id"]
        if r["text"] in by_text:
            ly.add_evidence(dst, new_id[cid], [by_text[r["text"]]], r["note"])
    for r in src.conn.execute("SELECT * FROM relations").fetchall():
        if int(r["src_id"]) in new_id and int(r["dst_id"]) in new_id:
            ly.relate(dst, new_id[int(r["src_id"])], new_id[int(r["dst_id"])], r["kind"], None, r["note"])
    return list(new_id)
