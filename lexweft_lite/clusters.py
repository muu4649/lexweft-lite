"""自動の意味層: 資料のベクトル、まとまり (クラスター)、まとまりの説明、地図の座標.

取り込んだ資料から LLM を使わずに作る。
  1. 資料ごとの語の数 (keywords.py が数えたもの) から TF-IDF を作り、LSA (切り詰めた特異値分解) で資料のベクトルにする
  2. ベクトルを k-means でまとめる。まとまりの数はシルエット係数で選ぶ
  3. まとまりごとに、ほかのまとまりより多く出る語 (クラス単位の TF-IDF) を並べて、どんな集まりかの説明にする
  4. 地図の座標は t-SNE (資料が多いときは主成分) で 2 次元にする
資料が増えたり減ったりしたら作り直す。
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter
from typing import Any

import numpy as np

from . import keywords as kw
from .store import Store, nfc, now_iso

PALETTE = ("#0e7490", "#c2410c", "#7c3aed", "#15803d", "#b45309", "#be185d", "#1d4ed8", "#4d7c0f", "#9f1239", "#0f766e",
           "#a16207", "#6d28d9", "#047857", "#b91c1c", "#4338ca", "#0369a1", "#86198f", "#3f6212", "#9a3412", "#334155")

ALL = "*"   # すべての資料 (フォルダを問わない)

SCHEMA = """
-- 開発中の版で作った、フォルダの区別のない表は使わない
DROP TABLE IF EXISTS layer_meta;
DROP TABLE IF EXISTS clusters;
DROP TABLE IF EXISTS doc_clusters;
DROP TABLE IF EXISTS doc_vectors;

-- 意味層は範囲 (scope) ごとに持つ. scope は登録したフォルダのパス、または * (すべての資料)
CREATE TABLE IF NOT EXISTS lscopes (
    scope TEXT PRIMARY KEY,
    built_at TEXT, signature TEXT, documents INTEGER NOT NULL DEFAULT 0, k INTEGER NOT NULL DEFAULT 0,
    method TEXT, seconds REAL
);
CREATE TABLE IF NOT EXISTS lclusters (
    scope TEXT NOT NULL,
    id INTEGER NOT NULL,
    label TEXT NOT NULL,
    terms TEXT NOT NULL,          -- JSON [[語, 重み], ...]
    size INTEGER NOT NULL,
    color TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    PRIMARY KEY (scope, id)
);
CREATE TABLE IF NOT EXISTS ldoc_clusters (
    scope TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    cluster_id INTEGER NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    closeness REAL NOT NULL,      -- まとまりの中心とのコサイン類似度
    PRIMARY KEY (scope, document_id)
);
-- 質問や段落を同じベクトル空間に置くための計算の型 (語の一覧・idf・LSA の成分・まとまりの中心)
CREATE TABLE IF NOT EXISTS lmodels (
    scope TEXT PRIMARY KEY,
    terms TEXT NOT NULL,          -- JSON [語, ...]
    idf BLOB NOT NULL,            -- float32 [語の数]
    components BLOB NOT NULL,     -- float32 [次元 × 語の数]
    dims INTEGER NOT NULL,
    centers BLOB NOT NULL,        -- float32 [まとまりの数 × 次元]
    k INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lpara_vectors (
    scope TEXT NOT NULL,
    paragraph_id INTEGER NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (scope, paragraph_id)
);
CREATE INDEX IF NOT EXISTS idx_lpara_doc ON lpara_vectors(scope, document_id);
CREATE TABLE IF NOT EXISTS ldoc_vectors (
    scope TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    vec BLOB NOT NULL,
    PRIMARY KEY (scope, document_id)
);
"""

_build_lock = threading.Lock()
_building: set[str] = set()
_errors: dict[str, str] = {}


def ensure_schema(store: Store) -> None:
    """Store が開くときに表を作る. ここでは何もしない (古い呼び出しのため残す)."""


def scope_filter(scope: str, column: str = "d.source") -> tuple[str, list[Any]]:
    """その範囲の資料に絞る SQL の条件."""
    if scope == ALL:
        return "1 = 1", []
    p = nfc(scope).rstrip("/")
    if not p:
        raise ValueError("範囲が空です")
    like = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    return f"({column} = ? OR {column} LIKE ? ESCAPE '\\')", [p, like]


def scope_documents(store: Store, scope: str) -> list[int]:
    where, params = scope_filter(scope)
    return [int(r[0]) for r in store.conn.execute(
        f"SELECT d.id FROM documents d JOIN doc_terms_done t ON t.document_id = d.id WHERE {where} ORDER BY d.id", params)]


def signature(store: Store, scope: str) -> str:
    """範囲の資料の集合が変わったか見るための印."""
    where, params = scope_filter(scope)
    row = store.conn.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(d.id), 0) AS s, COALESCE(MAX(d.added_at), '') AS a FROM documents d WHERE {where}",
                             params).fetchone()
    return f"{row['n']}:{row['s']}:{row['a']}"


def status(store: Store, scope: str = ALL) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM lscopes WHERE scope = ?", (scope,)).fetchone()
    where, params = scope_filter(scope)
    n_docs = int(store.conn.execute(f"SELECT COUNT(*) FROM documents d WHERE {where}", params).fetchone()[0])
    # 前の版で作った意味層 (段落のベクトルが無い) も、作り直しが要るとみなす
    no_model = bool(row and int(row["documents"]) >= 3 and int(row["k"]) >= 1
                    and store.conn.execute("SELECT 1 FROM lmodels WHERE scope = ?", (scope,)).fetchone() is None)
    return {"scope": scope, "built_at": row["built_at"] if row else None,
            "stale": (row["signature"] if row else None) != signature(store, scope) or no_model,
            "building": scope in _building, "error": _errors.get(scope),
            "documents": int(row["documents"]) if row else 0, "documents_now": n_docs,
            "clusters": int(row["k"]) if row else 0, "method": row["method"] if row else None,
            "seconds": row["seconds"] if row else None, "pending_terms": len(kw.pending(store))}


def scopes(store: Store) -> list[dict[str, Any]]:
    """選べる範囲: 登録したフォルダと、すべての資料."""
    out = []
    for src in store.list_sources():
        st = status(store, src["path"])
        out.append({"scope": src["path"], "label": _folder_label(src["path"]), **{k: st[k] for k in ("built_at", "stale", "building", "clusters", "documents_now", "error")}})
    st = status(store, ALL)
    out.append({"scope": ALL, "label": "すべての資料", **{k: st[k] for k in ("built_at", "stale", "building", "clusters", "documents_now", "error")}})
    return out


def _folder_label(path: str) -> str:
    from pathlib import PurePath

    parts = PurePath(path).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


# ---------------- 作る ----------------
def _matrix(store: Store, scope: str, min_df: int = 2, max_terms: int = 20000):
    """範囲の資料 × 語の行列 (疎)."""
    from scipy.sparse import csr_matrix

    doc_ids = scope_documents(store, scope)
    n = len(doc_ids)
    if n == 0:
        return doc_ids, [], None
    store.conn.execute("CREATE TEMP TABLE IF NOT EXISTS scope_docs (id INTEGER PRIMARY KEY)")
    store.conn.execute("DELETE FROM scope_docs")
    store.conn.executemany("INSERT INTO scope_docs(id) VALUES (?)", [(d,) for d in doc_ids])
    max_df = max(min_df, int(n * 0.6)) if n >= 10 else n
    terms = [r["term"] for r in store.conn.execute(
        "SELECT term, COUNT(*) AS df FROM doc_terms WHERE document_id IN (SELECT id FROM scope_docs) GROUP BY term"
        " HAVING df >= ? AND df <= ? ORDER BY df DESC LIMIT ?", (min(min_df, n), max_df, max_terms))]
    col = {t: i for i, t in enumerate(terms)}
    row_of = {d: i for i, d in enumerate(doc_ids)}
    rows, cols, vals = [], [], []
    for r in store.conn.execute("SELECT document_id, term, n FROM doc_terms WHERE document_id IN (SELECT id FROM scope_docs)"):
        j = col.get(r["term"])
        i = row_of.get(int(r["document_id"]))
        if j is None or i is None:
            continue
        rows.append(i)
        cols.append(j)
        vals.append(float(r["n"]) * kw._weight(r["term"]))   # 一般語になりやすい語を軽くする
    return doc_ids, terms, csr_matrix((vals, (rows, cols)), shape=(n, len(terms)), dtype=np.float32)


def _choose_k(vecs: np.ndarray) -> int:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    n = vecs.shape[0]
    if n < 6:
        return 1
    hi = int(min(24, max(3, n // 8)))
    lo = 3 if n >= 12 else 2
    best_k, best = lo, -1.0
    rng = np.random.default_rng(0)
    sample = vecs if n <= 2000 else vecs[rng.choice(n, 2000, replace=False)]
    for k in range(lo, hi + 1):
        labels = KMeans(n_clusters=k, n_init=3, random_state=0).fit_predict(sample)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(sample, labels, metric="cosine")
        # 少し多めのまとまりを好む (見通しのため)。差が小さければ大きい k を採る
        if s > best + 0.005:
            best, best_k = s, k
    return best_k


def _labels(counts, assign: np.ndarray, terms: list[str], k: int, top: int = 8) -> list[list[tuple[str, float]]]:
    """クラス単位の TF-IDF: まとまりの中で多く、ほかでは少ない語."""
    tf = np.vstack([np.asarray(counts[assign == c].sum(axis=0)).ravel() for c in range(k)])  # k × 語
    total = tf.sum(axis=0) + 1e-9
    avg = tf.sum() / max(1, k)
    idf = np.log(1 + avg / total)
    weight = np.array([kw._weight(t) for t in terms])
    score = (tf / (tf.sum(axis=1, keepdims=True) + 1e-9)) * idf * weight
    out = []
    for c in range(k):
        order = np.argsort(-score[c])
        picked: list[tuple[str, float]] = []
        for j in order:
            t = terms[j]
            if score[c, j] <= 0:
                break
            # 片方がもう片方を含む語は、先に選んだほうだけにする
            if any(t in p or p in t for p, _ in picked):
                continue
            picked.append((t, float(score[c, j])))
            if len(picked) >= top:
                break
        out.append(picked)
    return out


def build(store: Store, scope: str = ALL) -> dict[str, Any]:
    """範囲 (登録したフォルダ、またはすべての資料) の自動の意味層を作り直す."""
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA, TruncatedSVD
    from sklearn.feature_extraction.text import TfidfTransformer
    from sklearn.preprocessing import normalize

    kw.backfill(store)
    sig = signature(store, scope)
    t0 = time.time()
    doc_ids, terms, counts = _matrix(store, scope)
    n = len(doc_ids)
    if n == 0 or not terms:
        _replace(store, scope, sig, n, 0, "none", t0, [], [], [])
        return status(store, scope)
    tf = TfidfTransformer(sublinear_tf=True).fit(counts)
    tfidf = tf.transform(counts)
    # 次元は資料数よりずっと小さくする (資料数に近いと圧縮が効かず、言い換えどうしが近づかない)
    dims = max(1, min(100, len(terms) - 1, n - 1, max(2, n // 4)))
    svd = None
    if dims >= 2:
        svd = TruncatedSVD(n_components=dims, random_state=0).fit(tfidf)
        vecs = svd.transform(tfidf)
    else:
        vecs = tfidf.toarray()
    vecs = normalize(vecs).astype(np.float32)
    k = _choose_k(vecs) if n >= 6 else 1
    if k > 1:
        km = KMeans(n_clusters=k, n_init=8, random_state=0).fit(vecs)
        assign, centers = km.labels_, normalize(km.cluster_centers_)
    else:
        assign, centers = np.zeros(n, dtype=int), normalize(vecs.mean(axis=0, keepdims=True))
    # 大きい順に番号を振り直す
    order = [c for c, _ in Counter(assign.tolist()).most_common()]
    remap = {old: new for new, old in enumerate(order)}
    assign = np.array([remap[a] for a in assign])
    centers = centers[order]
    closeness = (vecs * centers[assign]).sum(axis=1)
    # 地図の座標
    if n >= 5 and n <= 3000:
        from sklearn.manifold import TSNE

        xy = TSNE(n_components=2, perplexity=max(2.0, min(30.0, (n - 1) / 3)), init="pca", random_state=0, metric="cosine").fit_transform(vecs)
        method = "LSA + k-means + t-SNE"
    elif n >= 3:
        xy = PCA(n_components=2, random_state=0).fit_transform(vecs)
        method = "LSA + k-means + PCA"
    else:
        xy = np.array([[float(i), 0.0] for i in range(n)])
        method = "LSA"
    xy = (xy - xy.min(axis=0)) / (np.ptp(xy, axis=0) + 1e-9)
    labels = _labels(counts, assign, terms, k)
    crow = []
    for ci in range(k):
        members = assign == ci
        cx, cy = xy[members].mean(axis=0)
        top = labels[ci]
        label = "・".join(t for t, _ in top[:3]) or f"まとまり {ci + 1}"
        crow.append((scope, ci, label, json.dumps([[t, round(w, 6)] for t, w in top], ensure_ascii=False), int(members.sum()),
                     PALETTE[ci % len(PALETTE)], float(cx), float(cy)))
    drow = [(scope, d, int(assign[i]), float(xy[i, 0]), float(xy[i, 1]), float(closeness[i])) for i, d in enumerate(doc_ids)]
    vrow = [(scope, d, vecs[i].tobytes()) for i, d in enumerate(doc_ids)]
    model = prow = None
    if svd is not None:
        idf = tf.idf_.astype(np.float32)
        comp = svd.components_.astype(np.float32)
        model = (scope, json.dumps(terms, ensure_ascii=False), idf.tobytes(), comp.tobytes(), int(comp.shape[0]),
                 centers.astype(np.float32).tobytes(), int(k))
        prow = _paragraph_vectors(store, scope, doc_ids, terms, idf, comp)
    _replace(store, scope, sig, n, k, method, t0, crow, drow, vrow, model, prow)
    return status(store, scope)


def embed_counts(rows: list[Counter], terms: list[str], idf: np.ndarray, comp: np.ndarray) -> np.ndarray:
    """語の数 (段落や質問ごと) を、意味層と同じベクトル空間に置く. 資料のベクトルと同じ手順 (重み → TF-IDF → LSA → 正規化)."""
    from scipy.sparse import csr_matrix
    from sklearn.preprocessing import normalize

    col = {t: i for i, t in enumerate(terms)}
    r, c, v = [], [], []
    for i, cnt in enumerate(rows):
        for t, num in cnt.items():
            j = col.get(t)
            if j is not None:
                r.append(i)
                c.append(j)
                v.append(1.0 + math.log(num * kw._weight(t)))   # 資料と同じ (sklearn の sublinear_tf: 1 + log(tf))
    x = csr_matrix((v, (r, c)), shape=(len(rows), len(terms)), dtype=np.float32)
    x = x.multiply(idf.reshape(1, -1)).tocsr()
    x = normalize(x)
    return normalize(np.asarray(x @ comp.T)).astype(np.float32)


def _paragraph_vectors(store: Store, scope: str, doc_ids: list[int], terms: list[str], idf: np.ndarray, comp: np.ndarray):
    """範囲の段落ごとのベクトル (質問と意味で比べるため)."""
    rows, meta = [], []
    for d in doc_ids:
        for para in store.paragraphs_of(d):
            rows.append(kw.extract_terms([para["text"]]))
            meta.append((int(para["id"]), d))
    out = []
    for i in range(0, len(rows), 5000):
        vecs = embed_counts(rows[i:i + 5000], terms, idf, comp)
        out += [(scope, pid, d, vecs[j].tobytes()) for j, (pid, d) in enumerate(meta[i:i + 5000])]
    return out


def _replace(store: Store, scope: str, sig: str, n: int, k: int, method: str, t0: float, crow, drow, vrow, model=None, prow=None) -> None:
    """範囲の意味層を、まとめて入れ替える (途中の状態を見せない)."""
    with store.tx() as c:
        c.execute("DELETE FROM lclusters WHERE scope = ?", (scope,))
        c.execute("DELETE FROM ldoc_clusters WHERE scope = ?", (scope,))
        c.execute("DELETE FROM ldoc_vectors WHERE scope = ?", (scope,))
        c.execute("DELETE FROM lmodels WHERE scope = ?", (scope,))
        c.execute("DELETE FROM lpara_vectors WHERE scope = ?", (scope,))
        if model:
            c.execute("INSERT INTO lmodels(scope, terms, idf, components, dims, centers, k) VALUES (?, ?, ?, ?, ?, ?, ?)", model)
        if prow:
            c.executemany("INSERT INTO lpara_vectors(scope, paragraph_id, document_id, vec) VALUES (?, ?, ?, ?)", prow)
        c.executemany("INSERT INTO lclusters(scope, id, label, terms, size, color, x, y) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", crow)
        c.executemany("INSERT INTO ldoc_clusters(scope, document_id, cluster_id, x, y, closeness) VALUES (?, ?, ?, ?, ?, ?)", drow)
        c.executemany("INSERT INTO ldoc_vectors(scope, document_id, vec) VALUES (?, ?, ?)", vrow)
        c.execute("INSERT OR REPLACE INTO lscopes(scope, built_at, signature, documents, k, method, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (scope, now_iso(), sig, n, k, method, round(time.time() - t0, 1)))


def build_in_background(store: Store, scope: str = ALL, force: bool = False) -> bool:
    """範囲の意味層が古ければ、裏で作り直す. 始めたら True."""
    if scope in _building or (not force and not status(store, scope)["stale"]):
        return False
    _building.add(scope)
    _errors.pop(scope, None)

    def run() -> None:
        with _build_lock:            # 計算は 1 つずつ
            try:
                build(store, scope)
            except Exception as e:  # noqa: BLE001
                _errors[scope] = str(e)
            finally:
                _building.discard(scope)

    threading.Thread(target=run, daemon=True).start()
    return True


def refresh_registered(store: Store) -> list[str]:
    """登録したフォルダのうち、資料が変わったものを作り直す. すべての資料の範囲は、作ってあれば作り直す."""
    started = []
    for src in store.list_sources():
        if build_in_background(store, src["path"]):
            started.append(src["path"])
    if store.conn.execute("SELECT 1 FROM lscopes WHERE scope = ?", (ALL,)).fetchone() and build_in_background(store, ALL):
        started.append(ALL)
    return started


# ---------------- 読む ----------------
def clusters(store: Store, scope: str = ALL) -> list[dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT * FROM lclusters WHERE scope = ? ORDER BY id", (scope,)):
        d = dict(r)
        d["terms"] = json.loads(d["terms"])
        out.append(d)
    return out


def map_data(store: Store, scope: str = ALL, max_points: int = 6000) -> dict[str, Any]:
    pts = [dict(r) for r in store.conn.execute(
        "SELECT dc.document_id AS id, dc.cluster_id AS c, dc.x, dc.y, d.title FROM ldoc_clusters dc JOIN documents d ON d.id = dc.document_id"
        " WHERE dc.scope = ? ORDER BY dc.closeness DESC LIMIT ?", (scope, max_points))]
    return {"status": status(store, scope), "clusters": clusters(store, scope), "points": pts}


def cluster_detail(store: Store, cluster_id: int, scope: str = ALL, limit: int = 60) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM lclusters WHERE scope = ? AND id = ?", (scope, cluster_id)).fetchone()
    if row is None:
        raise KeyError(f"まとまり {cluster_id} はありません")
    d = dict(row)
    d["terms"] = json.loads(d["terms"])
    d["documents"] = [dict(r) for r in store.conn.execute(
        "SELECT d.id, d.title, d.kind, dc.closeness FROM ldoc_clusters dc JOIN documents d ON d.id = dc.document_id"
        " WHERE dc.scope = ? AND dc.cluster_id = ? ORDER BY dc.closeness DESC LIMIT ?", (scope, cluster_id, limit))]
    # 代表の段落: まとまりの語を多く含む段落 (中心に近い資料の中から)
    top_terms = [t for t, _ in d["terms"][:5]]
    reps = []
    for doc in d["documents"][:8]:
        best = None
        for para in store.paragraphs_of(doc["id"]):
            hit = sum(1 for t in top_terms if t in para["text"])
            if hit and (best is None or hit > best[0]):
                best = (hit, para)
        if best:
            reps.append({"paragraph_id": best[1]["id"], "document_id": doc["id"], "title": doc["title"], "text": best[1]["text"][:300], "hits": best[0]})
    d["paragraphs"] = sorted(reps, key=lambda r: -r["hits"])[:5]
    return d


def scope_of_document(store: Store, document_id: int) -> str | None:
    """資料が入っている、意味層を作ってある範囲 (登録したフォルダを優先)."""
    rows = [r["scope"] for r in store.conn.execute("SELECT scope FROM ldoc_vectors WHERE document_id = ?", (document_id,))]
    folders = sorted((r for r in rows if r != ALL), key=len, reverse=True)
    return folders[0] if folders else (ALL if ALL in rows else None)


def similar_documents(store: Store, document_id: int, scope: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """同じ範囲の中で、ベクトルの近い資料."""
    scope = scope or scope_of_document(store, document_id)
    if scope is None:
        return []
    row = store.conn.execute("SELECT vec FROM ldoc_vectors WHERE scope = ? AND document_id = ?", (scope, document_id)).fetchone()
    if row is None:
        return []
    q = np.frombuffer(row["vec"], dtype=np.float32)
    ids, mats = [], []
    for r in store.conn.execute("SELECT document_id, vec FROM ldoc_vectors WHERE scope = ?", (scope,)):
        if int(r["document_id"]) == document_id:
            continue
        ids.append(int(r["document_id"]))
        mats.append(np.frombuffer(r["vec"], dtype=np.float32))
    if not ids:
        return []
    sims = np.vstack(mats) @ q
    order = [i for i in np.argsort(-sims)[:limit] if sims[i] > 0.05]
    if not order:
        return []
    titles = {int(r["id"]): r["title"] for r in store.conn.execute(
        f"SELECT id, title FROM documents WHERE id IN ({','.join('?' * len(order))})", [ids[i] for i in order])}
    return [{"id": ids[i], "title": titles.get(ids[i], ""), "similarity": round(float(sims[i]), 3)} for i in order]


def cluster_of_terms(store: Store, terms: list[str], scope: str = ALL) -> dict[str, int]:
    """語ごとに、いちばん強く出るまとまり (キーワードのつながりの色分け用)."""
    if not terms:
        return {}
    q = ",".join("?" * len(terms))
    best: dict[str, tuple[float, int]] = {}
    sizes = {int(r["id"]): int(r["size"]) for r in store.conn.execute("SELECT id, size FROM lclusters WHERE scope = ?", (scope,))}
    for r in store.conn.execute(
            f"SELECT t.term, dc.cluster_id AS c, COUNT(*) AS n FROM doc_terms t JOIN ldoc_clusters dc ON dc.document_id = t.document_id AND dc.scope = ?"
            f" WHERE t.term IN ({q}) GROUP BY t.term, dc.cluster_id", [scope, *terms]):
        share = r["n"] / max(1, sizes.get(int(r["c"]), 1)) * math.log(1 + r["n"])
        if r["term"] not in best or share > best[r["term"]][0]:
            best[r["term"]] = (share, int(r["c"]))
    return {t: c for t, (_, c) in best.items()}
