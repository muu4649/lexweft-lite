"""意味層をたどって、関係する資料に少ない手順で届くための道具 (MCP から使う).

  地図       範囲ごとのまとまりと、まとまりどうしの近さ
  道案内     質問から、関係するまとまり → 資料 → 段落を順位つきで返す
               (意味の近さ = 段落ベクトルと質問ベクトルのコサイン、文字の一致 = 全文検索。RRF で 1 本にする)
  広げる     読んだ資料から、似た資料・同じまとまりの資料へたどる (理由つき)
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
def _model(store: Store, scope: str) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT m.*, s.built_at FROM lmodels m JOIN lscopes s ON s.scope = m.scope WHERE m.scope = ?", (scope,)).fetchone()
    if row is None:
        return None
    key = (scope, row["built_at"])
    with _lock:
        if key in _cache:
            return _cache[key]
        terms = json.loads(row["terms"])
        dims = int(row["dims"])
        m = {
            "scope": scope, "terms": terms, "idf": np.frombuffer(row["idf"], dtype=np.float32),
            "comp": np.frombuffer(row["components"], dtype=np.float32).reshape(dims, len(terms)),
            "centers": np.frombuffer(row["centers"], dtype=np.float32).reshape(int(row["k"]), dims),
        }
        pids, dids, vecs = [], [], []
        for r in store.conn.execute("SELECT paragraph_id, document_id, vec FROM lpara_vectors WHERE scope = ?", (scope,)):
            pids.append(int(r["paragraph_id"]))
            dids.append(int(r["document_id"]))
            vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
        m["pids"] = np.array(pids, dtype=np.int64)
        m["dids"] = np.array(dids, dtype=np.int64)
        m["pvecs"] = np.vstack(vecs) if vecs else np.zeros((0, dims), dtype=np.float32)
        dvec = {int(r["document_id"]): np.frombuffer(r["vec"], dtype=np.float32) for r in store.conn.execute(
            "SELECT document_id, vec FROM ldoc_vectors WHERE scope = ?", (scope,))}
        m["doc_ids"] = np.array(list(dvec), dtype=np.int64)
        m["dvecs"] = np.vstack(list(dvec.values())) if dvec else np.zeros((0, dims), dtype=np.float32)
        m["cluster_of"] = {int(r["document_id"]): int(r["cluster_id"]) for r in store.conn.execute(
            "SELECT document_id, cluster_id FROM ldoc_clusters WHERE scope = ?", (scope,))}
        for k in [k for k in _cache if k[0] == scope]:
            _cache.pop(k)
        _cache[key] = m
        return m


def _scopes(store: Store, scope: str | None) -> list[str]:
    if scope:
        return [scope]
    folders = [s["path"] for s in store.list_sources()]
    built = {r[0] for r in store.conn.execute("SELECT scope FROM lmodels")}
    out = [f for f in folders if f in built]
    return out or ([cl.ALL] if cl.ALL in built else [])


def _queries(question: str) -> list[str]:
    return [q.strip() for q in re.split(r"[|｜\n]", question or "") if q.strip()]


def _qvec(m: dict[str, Any], question: str) -> np.ndarray | None:
    counts = kw.extract_terms(_queries(question))
    if not any(t in set(m["terms"]) for t in counts):
        # 質問の言葉が語の一覧に無いときは、語を含む長い語・語に含まれる語で近いものを探す
        near = Counter()
        for t in counts:
            for u in m["terms"]:
                if len(t) >= 2 and len(u) >= 2 and (t in u or u in t):
                    near[u] += 1
        counts = near
    if not counts:
        return None
    return cl.embed_counts([counts], m["terms"], m["idf"], m["comp"])[0]


# ---------------- 地図 ----------------
def overview(store: Store, scope: str | None = None) -> dict[str, Any]:
    """範囲ごとのまとまり (名前・資料数・よく出る語) と、近いまとまりどうしの組."""
    out = []
    for sc in _scopes(store, scope):
        m = _model(store, sc)
        cs = cl.clusters(store, sc)
        links = []
        if m is not None and len(cs) > 1:
            sim = m["centers"] @ m["centers"].T
            for i in range(len(cs)):
                for j in range(i + 1, len(cs)):
                    if sim[i, j] > 0.25:
                        links.append({"a": i, "b": j, "similarity": round(float(sim[i, j]), 2)})
            links.sort(key=lambda x: -x["similarity"])
        out.append({"scope": sc, "clusters": [{"id": c["id"], "label": c["label"], "documents": c["size"], "terms": [t for t, _ in c["terms"][:6]]} for c in cs],
                    "near_clusters": links[:12]})
    return {"scopes": out, "how_to": "lw_route(question) で関係するまとまりと資料へ。答える前に lw_unread で読み残しを確かめる"}


# ---------------- 道案内 ----------------
def _rank(store: Store, m: dict[str, Any], question: str, top: int = 300) -> tuple[dict[int, float], dict[int, list[str]], np.ndarray | None]:
    """段落ごとの点 (意味の近さと文字の一致の RRF) と、その理由."""
    scores: dict[int, float] = {}
    why: dict[int, list[str]] = {}
    q = _qvec(m, question)
    if q is not None and len(m["pids"]):
        sims = m["pvecs"] @ q
        order = np.argsort(-sims)[:top]
        for rank, i in enumerate(order):
            if sims[i] <= 0.05:
                break
            pid = int(m["pids"][i])
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            why.setdefault(pid, []).append(f"意味が近い {sims[i]:.2f}")
    in_scope = set(int(x) for x in m["pids"])
    for qq in _queries(question):
        for rank, pid in enumerate(p for p in rank_one(store, qq, limit=top) if p in in_scope):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            why.setdefault(pid, []).append(f"文字が一致「{qq}」")
    return scores, why, q


def route(store: Store, question: str, scope: str | None = None, max_documents: int = 8, paragraphs_per_document: int = 2) -> dict[str, Any]:
    """質問から、関係するまとまり → 資料 → 段落を返す. ここから新しい調べものとして読んだ資料を数える."""
    start(question)
    results = []
    for sc in _scopes(store, scope):
        m = _model(store, sc)
        if m is None:
            continue
        scores, why, q = _rank(store, m, question)
        if not scores:
            results.append({"scope": sc, "clusters": [], "documents": []})
            continue
        pdoc = {int(p): int(d) for p, d in zip(m["pids"], m["dids"])}
        by_doc: dict[int, list[tuple[float, int]]] = {}
        for pid, s in scores.items():
            by_doc.setdefault(pdoc[pid], []).append((s, pid))
        # 資料の点: いちばん良い段落 + ほかの良い段落を少し
        doc_score = {d: sorted(v, reverse=True)[0][0] + 0.3 * sum(s for s, _ in sorted(v, reverse=True)[1:3]) for d, v in by_doc.items()}
        ranked = sorted(doc_score, key=lambda d: -doc_score[d])
        # まとまりの点: 上位の資料がどのまとまりに入っているか + 中心と質問の近さ
        cluster_score: Counter = Counter()
        for d in ranked[:30]:
            cluster_score[m["cluster_of"].get(d, -1)] += doc_score[d]
        center_sim = (m["centers"] @ q) if q is not None else np.zeros(len(m["centers"]))
        labels = {c["id"]: c["label"] for c in cl.clusters(store, sc)}
        ctop = sorted(cluster_score, key=lambda c: -cluster_score[c])[:4]
        clusters_out = [{"id": c, "label": labels.get(c, ""), "share": round(cluster_score[c] / max(1e-9, sum(cluster_score.values())), 2),
                         "question_similarity": round(float(center_sim[c]), 2) if 0 <= c < len(center_sim) else None} for c in ctop if c >= 0]
        titles = {int(r["id"]): r["title"] for r in store.conn.execute(
            f"SELECT id, title FROM documents WHERE id IN ({','.join('?' * len(ranked[:max_documents]))})", ranked[:max_documents])} if ranked else {}
        paras = store.get_paragraphs([pid for d in ranked[:max_documents] for _, pid in sorted(by_doc[d], reverse=True)[:paragraphs_per_document]])
        docs_out = []
        for d in ranked[:max_documents]:
            best = sorted(by_doc[d], reverse=True)[:paragraphs_per_document]
            docs_out.append({"document_id": d, "title": titles.get(d, ""), "cluster": m["cluster_of"].get(d),
                             "paragraphs": [{"paragraph_id": pid, "why": why.get(pid, []), "text": _body(paras[pid], 280) if pid in paras else ""} for _, pid in best]})
        mark_shown(ranked[:max_documents])
        results.append({"scope": sc, "clusters": clusters_out, "documents": docs_out, "more_documents": max(0, len(ranked) - max_documents)})
    return {"question": question, "results": results,
            "next": "関係の強い資料は lw_read_document で読む。近くも見たいときは lw_expand。答える前に lw_unread で読み残しを確かめる"}


# ---------------- 広げる ----------------
def expand(store: Store, document_ids: list[int], question: str | None = None, limit: int = 8) -> dict[str, Any]:
    """読んだ資料の近く (似た資料・同じまとまり) を、理由つきで返す. 読んだ資料は除く."""
    question = question or reading.get("question") or ""
    seen = set(int(d) for d in document_ids) | reading["read"]
    out = []
    for sc in _scopes(store, None):
        m = _model(store, sc)
        if m is None or not len(m["doc_ids"]):
            continue
        index = {int(d): i for i, d in enumerate(m["doc_ids"])}
        base = [index[d] for d in document_ids if d in index]
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
        cand = []
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
                cand.append((score, d, reasons))
        cand.sort(reverse=True)
        titles = {int(r["id"]): r["title"] for r in store.conn.execute(
            f"SELECT id, title FROM documents WHERE id IN ({','.join('?' * len(cand[:limit]))})", [d for _, d, _ in cand[:limit]])} if cand else {}
        mark_shown([d for _, d, _ in cand[:limit]])
        out.append({"scope": sc, "documents": [{"document_id": d, "title": titles.get(d, ""), "cluster": m["cluster_of"].get(d), "why": r, "score": round(s, 3)}
                                               for s, d, r in cand[:limit]]})
    return {"from": document_ids, "results": out}


# ---------------- 読み残し ----------------
def unread(store: Store, question: str | None = None, read_document_ids: list[int] | None = None, limit: int = 10,
           min_score: float = 0.0) -> dict[str, Any]:
    """質問に関係が強いのに、まだ読んでいない資料. 読んだ資料は、lw_read_document で開いたものと read_document_ids."""
    question = question or reading.get("question") or ""
    read = set(int(d) for d in (read_document_ids or [])) | reading["read"]
    out = []
    for sc in _scopes(store, None):
        m = _model(store, sc)
        if m is None:
            continue
        scores, why, _ = _rank(store, m, question)
        pdoc = {int(p): int(d) for p, d in zip(m["pids"], m["dids"])}
        by_doc: dict[int, tuple[float, int]] = {}
        for pid, s in scores.items():
            d = pdoc[pid]
            if d not in by_doc or s > by_doc[d][0]:
                by_doc[d] = (s, pid)
        ranked = sorted(by_doc.items(), key=lambda kv: -kv[1][0])
        left = [(d, s, pid) for d, (s, pid) in ranked if d not in read and s > min_score][:limit]
        paras = store.get_paragraphs([pid for _, _, pid in left])
        titles = {int(r["id"]): r["title"] for r in store.conn.execute(
            f"SELECT id, title FROM documents WHERE id IN ({','.join('?' * len(left))})", [d for d, _, _ in left])} if left else {}
        read_clusters = {m["cluster_of"].get(d) for d in read}
        items = []
        for d, s, pid in left:
            reasons = list(why.get(pid, []))
            if m["cluster_of"].get(d) in read_clusters:
                reasons.append("読んだ資料と同じまとまり")
            if d in reading["shown"]:
                reasons.append("一覧には出たが、まだ開いていない")
            items.append({"document_id": d, "title": titles.get(d, ""), "cluster": m["cluster_of"].get(d), "why": reasons,
                          "check_paragraph": {"paragraph_id": pid, "text": _body(paras[pid], 240) if pid in paras else ""}})
        out.append({"scope": sc, "unread": items, "read_in_scope": len([d for d in read if d in m["cluster_of"]])})
    return {"question": question, "read": sorted(read), "results": out,
            "next": "check_paragraph を見て、関係があれば lw_read_document で読む。関係がなければそう判断した理由を答えに添える"}
