"""フォルダの取り込みを裏で進める (進み具合を見られ、途中で止められる)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import runtime
from .ingest import ingest_loaded
from .loaders import iter_files, load_file, load_url


@dataclass
class Job:
    id: str
    target: str
    total: int = 0
    done: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    current: str = ""
    errors: list[str] = field(default_factory=list)
    state: str = "running"  # running | done | cancelled | failed
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


def _bump(job: Job, status: str) -> None:
    job.counts[status] = job.counts.get(status, 0) + 1


def _run(job: Job) -> None:
    store = runtime.store()
    md = runtime.markdown_dir()
    try:
        if job.target.startswith(("http://", "https://")):
            job.total = 1
            job.current = job.target
            r = ingest_loaded(store, load_url(job.target), md)
            _bump(job, r.status)
            job.done = 1
        else:
            root = Path(job.target).expanduser()
            if not root.exists():
                raise FileNotFoundError(str(root))
            files = [root] if root.is_file() else list(iter_files(root))
            job.total = len(files)
            for p in files:
                if job.cancel.is_set():
                    job.state = "cancelled"
                    break
                job.current = str(p)
                try:
                    doc = load_file(p)
                    if doc is None or not doc.text.strip():
                        _bump(job, "empty")
                    else:
                        _bump(job, ingest_loaded(store, doc, md).status)
                except Exception as e:  # noqa: BLE001  壊れたファイル 1 件で全体を止めない
                    _bump(job, "error")
                    job.errors.append(f"{p.name}: {e}")
                job.done += 1
        if job.state == "running":
            job.state = "done"
    except Exception as e:  # noqa: BLE001
        job.state = "failed"
        job.errors.append(str(e))
    finally:
        job.current = ""
        job.finished = time.time()


def start(target: str) -> Job:
    with _lock:
        running = [j for j in _jobs.values() if j.state == "running"]
        if running:
            raise ValueError("ほかの取り込みが進んでいます。終わるか止めてから始めてください")
        job = Job(id=uuid.uuid4().hex[:12], target=target.strip())
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
