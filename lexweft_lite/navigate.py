"""意味層をたどって、関係する資料に少ない手順で届くための道具 (MCP と画面から使う).

  地図       フォルダごとのまとまりと、まとまりどうしの近さ、意味層の版と前回からの変化
  道案内     質問から、関係するまとまり → 資料 → 段落を順位つきで返す
               フォルダごとに、意味の近さ (段落ベクトルと質問ベクトルのコサイン) と文字の一致 (全文検索) を RRF でまとめ、
               フォルダをまたぐときは、資料ごとの意味の近さで 1 本の順位にする
  広げる     読んだ資料から、同じフォルダの似た資料・同じまとまりの資料へたどる (理由つき)
  読み残し   質問に関係が強いのに、まだ読んでいない資料を理由つきで返す

どの資料を読んだかは、このプロセスの中で覚える (道案内を呼ぶたびに新しい調べものとして数え直す)。
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from typing import Any

import numpy as np

from . import clusters as cl
from . import keywords as kw
from .library import Library
from .search import rank_one
from .store import Store

RRF_K = 60
_cache: dict[tuple[str, str], dict[str, Any]] = {}
_lock = threading.Lock()

# 調べものの記録 (道案内を呼ぶと新しく始まる)
reading: dict[str, Any] = {"question": None, "read": set(), "shown": set()}


def start(question: str) -> None:
    reading.update(question=question, read=set(), shown=set())


def mark_read(document_id: int) -> None:
    reading["read"].add(int(document_id))


def mark_shown(document_ids) -> None:
    reading["shown"].update(int(d) for d in document_ids)


def _body(para: dict[str, Any], n: int) -> str:
    """段落の本文 (先頭の見出しの行は除く)."""
    text, head = para["text"], para.get("heading") or ""
    if head and text.startswith(head):
        text = text[len(head):].lstrip("\n")
    return text[:n]


# ---------------- 計算の型を読む ----------------
def _model(store: Store) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT m.*, s.built_at FROM lmodels m JOIN lscopes s ON s.scope = m.scope WHERE m.scope = ?", (cl.KEY,)).fetchone()
    if row is None:
        return None
    key = (store.path, row["built_at"])
    with _lock:
        if key in _cache:
            return _cache[key]
        terms = json.loads(row["terms"])
        dims = int(row["dims"])
        ids, centers = cl.model_centers(row["centers"], dims)
        m = {"terms": terms, "termset": set(terms), "idf": np.frombuffer(row["idf"], dtype=np.float32),
             "comp": np.frombuffer(row["components"], dtype=np.float32).reshape(dims, len(terms)),
             "center_ids": ids, "centers": centers}
        pids, dids, vecs = [], [], []
        for r in store.conn.execute("SELECT paragraph_id, document_id, vec FROM lpara_vectors WHERE scope = ?", (cl.KEY,)):
            pids.append(int(r["paragraph_id"]))
            dids.append(int(r["document_id"]))
            vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
        m["pids"] = np.array(pids, dtype=np.int64)
        m["dids"] = np.array(dids, dtype=np.int64)
        m["pvecs"] = np.vstack(vecs) if vecs else np.zeros((0, dims), dtype=np.float32)
        dvec = {int(r["document_id"]): np.frombuffer(r["vec"], dtype=np.float32) for r in store.conn.execute(
            "SELECT document_id, vec FROM ldoc_vectors WHERE scope = ?", (cl.KEY,))}
        m["doc_ids"] = np.array(list(dvec), dtype=np.int64)
        m["dvecs"] = np.vstack(list(dvec.values())) if dvec else np.zeros((0, dims), dtype=np.float32)
        m["cluster_of"] = {int(r["document_id"]): int(r["cluster_id"]) for r in store.conn.execute(
            "SELECT document_id, cluster_id FROM ldoc_clusters WHERE scope = ?", (cl.KEY,))}
        m["labels"] = {int(r["id"]): (r["label"], r["color"]) for r in store.conn.execute("SELECT id, label, color FROM lclusters WHERE scope = ?", (cl.KEY,))}
        for k in [k for k in _cache if k[0] == store.path]:
            _cache.pop(k)
        _cache[key] = m
        return m


def _stores(lib: Library, scope: str | None) -> list[Store]:
    """道案内に使う保存先. scope を指定しなければ、意味層ができている保存先すべて (登録したフォルダとフォルダの外の資料)."""
    if scope:
        return [lib.for_scope(scope)]
    return [st for st in lib.stores() if st.conn.execute("SELECT 1 FROM lmodels WHERE scope = ?", (cl.KEY,)).fetchone()]


def _queries(question: str) -> list[str]:
    return [q.strip() for q in re.split(r"[|｜\n]", question or "") if q.strip()]


def _qvec(m: dict[str, Any], question: str) -> np.ndarray | None:
    counts = kw.extract_terms(_queries(question))
    if not any(t in m["termset"] for t in counts):
        # 質問の言葉が語の一覧に無いときは、語を含む長い語・語に含まれる語で近いものを使う
        near: Counter = Counter()
        for t in counts:
            for u in m["terms"]:
                if len(t) >= 2 and len(u) >= 2 and (t in u or u in t):
                    near[u] += 1
        counts = near
    if not counts:
        return None
    return cl.embed_counts([counts], m["terms"], m["idf"], m["comp"])[0]


def _cluster(m: dict[str, Any], doc_id: int) -> tuple[int | None, str, str]:
    c = m["cluster_of"].get(doc_id)
    label, color = m["labels"].get(c, ("", "#6b7280"))
    return c, label, color


# ---------------- 地図 ----------------
def overview(lib: Library, scope: str | None = None) -> dict[str, Any]:
    """フォルダごとのまとまり (名前・資料数・よく出る語)、近いまとまりどうしの組、意味層の版と前回からの変化."""
    from .jobs import changed_folders

    changed = {r["path"]: r for r in changed_folders()} if not scope or scope != "central" else {}
    out = []
    for st in (lib.stores() if not scope else [lib.for_scope(scope)]):
        status = cl.status(st)
        if not status["documents_now"] and st.key == "central":
            continue
        m = _model(st)
        cs = cl.clusters(st)
        links = []
        if m is not None and len(m["center_ids"]) > 1:
            sim = m["centers"] @ m["centers"].T
            ids = m["center_ids"]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if sim[i, j] > 0.25:
                        links.append({"a": ids[i], "b": ids[j], "similarity": round(float(sim[i, j]), 2)})
            links.sort(key=lambda x: -x["similarity"])
        notes = []
        if st.key in changed:
            notes.append("フォルダの中身が、前に取り込んだときから変わっています。lw_refresh で取り込み直せます")
        if status["stale"]:
            notes.append("意味層を作り直す必要があります (アプリが動いていれば自動で作り直します)")
        out.append({"scope": st.key, "label": st.label, "documents": status["documents_now"], "layer_version": status["version"],
                    "built_at": status["built_at"], "changes_since_previous": status["changes"], "notes": notes,
                    "clusters": [{"id": c["id"], "label": c["label"], "documents": c["size"], "terms": [t for t, _ in c["terms"][:6]]} for c in cs],
                    "near_clusters": links[:12]})
    return {"folders": out,
            "how_to": "lw_route(question) で関係するまとまりと資料へ (scope でフォルダを絞れる)。答える前に lw_unread で読み残しを確かめる。"
                      "まとまりの番号は、作り直しても中身が同じなら変わらない"}


# ---------------- 道案内 ----------------
def _rank(store: Store, m: dict[str, Any], question: str, top: int = 300) -> tuple[dict[int, float], dict[int, list[str]], dict[int, float], np.ndarray | None]:
    """段落ごとの点 (意味の近さと文字の一致の RRF)、理由、意味の近さ."""
    scores: dict[int, float] = {}
    why: dict[int, list[str]] = {}
    sem: dict[int, float] = {}
    q = _qvec(m, question)
    if q is not None and len(m["pids"]):
        sims = m["pvecs"] @ q
        order = np.argsort(-sims)[:top]
        for rank, i in enumerate(order):
            if sims[i] <= 0.05:
                break
            pid = int(m["pids"][i])
            sem[pid] = float(sims[i])
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            why.setdefault(pid, []).append(f"意味が近い {sims[i]:.2f}")
    for qq in _queries(question):
        for rank, pid in enumerate(rank_one(store, qq, limit=top)):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            why.setdefault(pid, []).append(f"文字が一致「{qq}」")
    return scores, why, sem, q


def _documents(store: Store, m: dict[str, Any], question: str) -> tuple[list[dict[str, Any]], dict[int, list[str]], np.ndarray | None]:
    """保存先の中で、質問に関係する資料を順に (資料の点・いちばん良い段落・フォルダをまたいで比べるための強さ)."""
    scores, why, sem, q = _rank(store, m, question)
    pdoc = {int(p): int(d) for p, d in zip(m["pids"], m["dids"])}
    for pid in scores:
        if pid not in pdoc:   # 意味層を作ったあとに増えた段落 (文字の一致だけで見つかったもの)
            row = store.conn.execute("SELECT document_id FROM paragraphs WHERE id = ?", (pid,)).fetchone()
            if row:
                pdoc[pid] = int(row[0])
    by_doc: dict[int, list[tuple[float, int]]] = {}
    for pid, s in scores.items():
        if pid in pdoc:
            by_doc.setdefault(pdoc[pid], []).append((s, pid))
    out = []
    for d, v in by_doc.items():
        v.sort(reverse=True)
        score = v[0][0] + 0.3 * sum(s for s, _ in v[1:3])
        best_sem = max((sem.get(pid, 0.0) for _, pid in v), default=0.0)
        text_hit = any(w.startswith("文字") for _, pid in v for w in why.get(pid, []))
        out.append({"document_id": d, "score": score, "strength": best_sem + (0.15 if text_hit else 0.0), "paragraphs": [pid for _, pid in v]})
    out.sort(key=lambda x: -x["score"])
    return out, why, q


def route(lib: Library, question: str, scope: str | None = None, max_documents: int = 8, paragraphs_per_document: int = 2) -> dict[str, Any]:
    """質問から、関係するまとまり → 資料 → 段落を返す. ここから新しい調べものとして読んだ資料を数える."""
    start(question)
    folders, pool = [], []
    for st in _stores(lib, scope):
        m = _model(st)
        if m is None:
            continue
        docs, why, q = _documents(st, m, question)
        if not docs:
            continue
        # まとまりの点: 上位の資料がどのまとまりに入っているか
        cs: Counter = Counter()
        for d in docs[:30]:
            c = m["cluster_of"].get(d["document_id"])
            if c is not None:
                cs[c] += d["score"]
        total = sum(cs.values()) or 1.0
        center_sim = dict(zip(m["center_ids"], (m["centers"] @ q).tolist())) if q is not None else {}
        folders.append({"scope": st.key, "label": st.label, "layer_version": cl.status(st)["version"],
                        "clusters": [{"id": c, "label": m["labels"].get(c, ("", ""))[0], "color": m["labels"].get(c, ("", "#6b7280"))[1],
                                      "share": round(cs[c] / total, 2), "question_similarity": round(center_sim.get(c, 0.0), 2)}
                                     for c, _ in cs.most_common(4)],
                        "matched_documents": len(docs)})
        for rank, d in enumerate(docs[:max_documents * 3]):
            pool.append((d["strength"], -rank, st, m, d, why))
    many = len({p[2].path for p in pool}) > 1
    # 1 つのフォルダだけなら、そのフォルダの中の順位のまま。フォルダをまたぐときは意味の近さで比べる
    pool.sort(key=(lambda x: (x[0], x[1])) if many else (lambda x: x[1]), reverse=True)
    picked = pool[:max_documents]
    out_docs = []
    for _, _, st, m, d, why in picked:
        pids = d["paragraphs"][:paragraphs_per_document]
        paras = st.get_paragraphs(pids)
        title = st.conn.execute("SELECT title FROM documents WHERE id = ?", (d["document_id"],)).fetchone()
        c, label, color = _cluster(m, d["document_id"])
        out_docs.append({"document_id": d["document_id"], "title": title["title"] if title else "", "scope": st.key, "folder": st.label,
                         "cluster": c, "cluster_label": label, "cluster_color": color,
                         "paragraphs": [{"paragraph_id": pid, "why": why.get(pid, []), "text": _body(paras[pid], 280) if pid in paras else ""} for pid in pids]})
    mark_shown(d["document_id"] for d in out_docs)
    return {"question": question, "documents": out_docs, "folders": folders, "across_folders": many,
            "more_documents": max(0, sum(f["matched_documents"] for f in folders) - len(out_docs)),
            "next": "関係の強い資料は lw_read_document で読む。近くも見たいときは lw_expand。答える前に lw_unread で読み残しを確かめる"}


# ---------------- 広げる ----------------
def expand(lib: Library, document_ids: list[int], question: str | None = None, limit: int = 8) -> dict[str, Any]:
    """読んだ資料の近く (同じフォルダの似た資料・同じまとまり) を、理由つきで返す. 読んだ資料は除く."""
    question = question or reading.get("question") or ""
    seen = set(int(d) for d in document_ids) | reading["read"]
    by_store: dict[str, tuple[Store, list[int]]] = {}
    for d in document_ids:
        try:
            st = lib.for_id(int(d))
        except KeyError:
            continue
        by_store.setdefault(st.path, (st, []))[1].append(int(d))
    cand = []
    for st, base_ids in by_store.values():
        m = _model(st)
        if m is None or not len(m["doc_ids"]):
            continue
        index = {int(d): i for i, d in enumerate(m["doc_ids"])}
        base = [index[d] for d in base_ids if d in index]
        if not base:
            continue
        sims = m["dvecs"] @ m["dvecs"][base].T     # 資料 × 読んだ資料
        best = sims.max(axis=1)
        src = sims.argmax(axis=1)
        qsim = None
        if question:
            q = _qvec(m, question)
            if q is not None:
                qsim = m["dvecs"] @ q
        base_clusters = {m["cluster_of"].get(int(m["doc_ids"][b])) for b in base}
        for i, d in enumerate(m["doc_ids"]):
            d = int(d)
            if d in seen:
                continue
            reasons = []
            score = float(best[i])
            if best[i] > 0.3:
                reasons.append(f"読んだ資料 #{int(m['doc_ids'][base[src[i]]])} と似ている {best[i]:.2f}")
            if m["cluster_of"].get(d) in base_clusters:
                reasons.append("同じまとまり")
                score += 0.1
            if qsim is not None:
                score = 0.6 * score + 0.4 * float(qsim[i])
                if qsim[i] > 0.3:
                    reasons.append(f"質問と意味が近い {qsim[i]:.2f}")
            if reasons:
                cand.append((score, d, reasons, st, m))
    cand.sort(key=lambda x: -x[0])
    out = []
    for s, d, r, st, m in cand[:limit]:
        title = st.conn.execute("SELECT title FROM documents WHERE id = ?", (d,)).fetchone()
        c, label, color = _cluster(m, d)
        out.append({"document_id": d, "title": title["title"] if title else "", "scope": st.key, "folder": st.label,
                    "cluster": c, "cluster_label": label, "cluster_color": color, "why": r, "score": round(s, 3)})
    mark_shown(x["document_id"] for x in out)
    return {"from": document_ids, "documents": out}


# ---------------- 読み残し ----------------
def unread(lib: Library, question: str | None = None, read_document_ids: list[int] | None = None, limit: int = 10,
           scope: str | None = None) -> dict[str, Any]:
    """質問に関係が強いのに、まだ読んでいない資料. 読んだ資料は、lw_read_document で開いたものと read_document_ids."""
    question = question or reading.get("question") or ""
    read = set(int(d) for d in (read_document_ids or [])) | reading["read"]
    pool = []
    for st in _stores(lib, scope):
        m = _model(st)
        if m is None:
            continue
        docs, why, _ = _documents(st, m, question)
        read_clusters = {m["cluster_of"].get(d) for d in read}
        for rank, d in enumerate(docs):
            if d["document_id"] in read:
                continue
            pool.append((d["strength"], -rank, st, m, d, why, read_clusters))
    many = len({p[2].path for p in pool}) > 1
    pool.sort(key=(lambda x: (x[0], x[1])) if many else (lambda x: x[1]), reverse=True)
    items = []
    for _, _, st, m, d, why, read_clusters in pool[:limit]:
        pid = d["paragraphs"][0]
        para = st.get_paragraphs([pid]).get(pid)
        title = st.conn.execute("SELECT title FROM documents WHERE id = ?", (d["document_id"],)).fetchone()
        c, label, color = _cluster(m, d["document_id"])
        reasons = list(why.get(pid, []))
        if c in read_clusters:
            reasons.append("読んだ資料と同じまとまり")
        if d["document_id"] in reading["shown"]:
            reasons.append("一覧には出たが、まだ開いていない")
        items.append({"document_id": d["document_id"], "title": title["title"] if title else "", "scope": st.key, "folder": st.label,
                      "cluster": c, "cluster_label": label, "cluster_color": color, "why": reasons,
                      "check_paragraph": {"paragraph_id": pid, "text": _body(para, 240) if para else ""}})
    return {"question": question, "read": sorted(read), "unread": items,
            "next": "check_paragraph を見て、関係があれば lw_read_document で読む。関係がなければそう判断した理由を答えに添える"}
