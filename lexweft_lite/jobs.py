"""取り込みを裏で進める (進み具合を見られ、途中で止められる). フォルダの変化に気づいて自動で取り込む.

登録したフォルダの資料は、そのフォルダの保存先へ。それ以外 (登録していないフォルダ・ファイル・URL) はアプリ側の目録へ入れる。
取り込みが終わったら、変わった保存先の意味層を作り直す。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator

from . import runtime
from .ingest import ingest_loaded
from .loaders import iter_files, load_file, load_url
from .store import Store

AUTO_INTERVAL = 300   # 秒. 登録したフォルダの変化を見る間隔


@dataclass
class Job:
    id: str
    target: str
    targets: list[str] = field(default_factory=list)
    total: int = 0
    done: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    current: str = ""
    errors: list[str] = field(default_factory=list)
    state: str = "running"  # running | done | cancelled | failed
    auto: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def view(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "cancel"}
        d["counts"] = dict(self.counts)
        d["errors"] = self.errors[-20:]
        d["error_count"] = len(self.errors)
        return d


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _bump(job: Job, status: str, n: int = 1) -> None:
    job.counts[status] = job.counts.get(status, 0) + n


def scan_signature(folder: str) -> str:
    """フォルダの取り込める資料の様子 (件数・大きさの合計・いちばん新しい更新時刻). 変わったら取り込み直す合図."""
    n = size = 0
    newest = 0.0
    for p in iter_files(folder):
        try:
            st = p.stat()
        except OSError:
            continue
        n += 1
        size += st.st_size
        newest = max(newest, st.st_mtime)
    return f"{n}:{size}:{int(newest)}"


@contextlib.contextmanager
def store_lock(store: Store) -> Iterator[None]:
    """同じ保存先に、アプリと Claude (MCP) が同時に書き込まないようにする."""
    lock_path = Path(store.path).with_suffix(".lock")
    f = open(lock_path, "w")
    try:
        try:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt

            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
        yield
    finally:
        f.close()


def _remove_vanished(store: Store, root: str) -> int:
    """フォルダから消えたファイルの資料を消す (元のファイルが無いもの)."""
    gone = []
    for d in store.list_documents():
        src = d["source"]
        if src.startswith(("http://", "https://", "text:")):
            continue
        if src.startswith(root.rstrip("/") + "/") and not os.path.exists(src):
            gone.append(int(d["id"]))
    if gone:
        store.delete_documents(gone)
        if store.markdown_dir:
            from .markdown import remove_document_file

            for i in gone:
                remove_document_file(i, store.markdown_dir)
    return len(gone)


def _run(job: Job) -> None:
    lib = runtime.library()
    touched: dict[str, Store] = {}
    try:
        work: list[tuple[Store, Path | str, str | None]] = []   # (保存先, ファイルまたは URL, 登録したフォルダ)
        for target in job.targets:
            if target.startswith(("http://", "https://")):
                work.append((lib.catalog, target, None))
                continue
            root = Path(target).expanduser()
            if not root.exists():
                job.errors.append(f"見つかりません: {root}")
                continue
            if root.is_file():
                work.append((lib.for_source(str(root)), root, None))
                continue
            registered = lib.source(str(root))
            if registered:
                folder = registered["path"]          # 本当のパス (別名のパスでも同じ保存先に入れる)
                st = lib.store_for_folder(folder)
                work += [(st, p, folder) for p in iter_files(folder)]
            else:
                work += [(lib.catalog, p, None) for p in iter_files(root)]
        job.total = len(work)
        folders = {f for _, _, f in work if f}
        for st, item, _ in work:
            if job.cancel.is_set():
                job.state = "cancelled"
                break
            job.current = str(item)
            touched[st.path] = st
            try:
                with store_lock(st):
                    if isinstance(item, str):
                        r = ingest_loaded(st, load_url(item), st.markdown_dir)
                        _bump(job, r.status)
                    else:
                        doc = load_file(item)
                        if doc is None or not doc.text.strip():
                            _bump(job, "empty")
                        else:
                            _bump(job, ingest_loaded(st, doc, st.markdown_dir).status)
            except Exception as e:  # noqa: BLE001  壊れたファイル 1 件で全体を止めない
                _bump(job, "error")
                job.errors.append(f"{Path(str(item)).name}: {e}")
            job.done += 1
        if job.state == "running":
            for f in folders:
                st = lib.store_for_folder(f)
                touched[st.path] = st
                with store_lock(st):
                    removed = _remove_vanished(st, f)
                if removed:
                    _bump(job, "removed", removed)
                lib.touch(f, scan_signature(f), auto=job.auto)
            for t in job.targets:
                row = lib.source(t) if not t.startswith(("http://", "https://")) else None
                if row and row["path"] not in folders:   # 中身が空になったフォルダも記録する
                    st = lib.store_for_folder(row["path"])
                    touched[st.path] = st
                    with store_lock(st):
                        removed = _remove_vanished(st, row["path"])
                    if removed:
                        _bump(job, "removed", removed)
                    lib.touch(row["path"], scan_signature(row["path"]), auto=job.auto)
            job.state = "done"
    except Exception as e:  # noqa: BLE001
        job.state = "failed"
        job.errors.append(str(e))
    finally:
        job.current = ""
        job.finished = time.time()
        # 取り込みが終わったら、変わった保存先の意味層を作り直す
        try:
            from .clusters import build_in_background

            for st in touched.values():
                build_in_background(st)
        except Exception as e:  # noqa: BLE001
            job.errors.append(f"意味層: {e}")


def start(target: str | list[str], auto: bool = False) -> Job:
    """取り込みを始める. target はファイル・フォルダ・URL、または複数のフォルダ."""
    targets = [t.strip() for t in ([target] if isinstance(target, str) else target) if t and t.strip()]
    if not targets:
        raise ValueError("取り込むものがありません")
    with _lock:
        if any(j.state == "running" for j in _jobs.values()):
            raise ValueError("ほかの取り込みが進んでいます。終わるか止めてから始めてください")
        job = Job(id=uuid.uuid4().hex[:12], target=" / ".join(targets), targets=targets, auto=auto)
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def get(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise KeyError("取り込みの記録がありません")
    return job


def cancel(job_id: str) -> Job:
    job = get(job_id)
    job.cancel.set()
    return job


def latest() -> Job | None:
    return max(_jobs.values(), key=lambda j: j.started, default=None)


def running() -> bool:
    return any(j.state == "running" for j in _jobs.values())


# ---------------- フォルダの変化に気づく ----------------
def changed_folders() -> list[dict[str, Any]]:
    """登録したフォルダのうち、前に取り込んだときから中身が変わったもの."""
    out = []
    for row in runtime.library().sources():
        if not Path(row["path"]).is_dir():
            continue
        sig = scan_signature(row["path"])
        if sig != (row["scan_sig"] or ""):
            out.append({**row, "now": sig})
    return out


def auto_import_once() -> Job | None:
    """自動で取り込む設定のフォルダが変わっていれば、取り込みを始める."""
    if running():
        return None
    targets = [r["path"] for r in changed_folders() if r["auto"]]
    return start(targets, auto=True) if targets else None


def watch(interval: int = AUTO_INTERVAL, stop: threading.Event | None = None) -> threading.Thread:
    """登録したフォルダを一定の間隔で見て、変わっていれば自動で取り込む (アプリの画面が動いている間)."""
    stop = stop or threading.Event()

    def loop() -> None:
        time.sleep(5)
        while not stop.is_set():
            try:
                auto_import_once()
            except Exception:  # noqa: BLE001  見張りは止めない
                pass
            stop.wait(interval)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th


def rechunk_all(lib: Any) -> dict[str, dict[str, Any]]:
    """段落の分け方の版が変わった保存先を、取り込み直す (アプリと Claude が同時にしないよう鍵をかける)."""
    from .ingest import rechunk, rechunk_needed

    done: dict[str, dict[str, Any]] = {}
    for st in lib.stores():
        if not rechunk_needed(st):
            continue
        with store_lock(st):
            if rechunk_needed(st):   # 鍵を待つ間に、ほかの処理が済ませていることがある
                done[st.key] = rechunk(st, st.markdown_dir)
    return done
