"""自動の意味層: 資料のベクトル、まとまり (クラスター)、まとまりの説明、地図の座標.

意味層は保存先 (登録したフォルダ 1 つ、またはアプリ側の目録) ごとに 1 つ持つ。取り込んだ資料から LLM を使わずに作る。
  1. 資料ごとの語の数 (keywords.py が数えたもの) から TF-IDF を作り、LSA (切り詰めた特異値分解) で資料のベクトルにする
  2. ベクトルを k-means でまとめる。まとまりの数はシルエット係数で選ぶ
  3. まとまりごとに、ほかのまとまりより多く出る語 (クラス単位の TF-IDF) を並べて、どんな集まりかの説明にする
  4. 地図の座標は t-SNE (資料が多いときは主成分) で 2 次元にする
  5. 作り直したときは、中身の資料が重なる前のまとまりに同じ番号と色を引き継ぎ、版の番号と「何が変わったか」を残す
資料が増えたり減ったりしたら作り直す。
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import Counter
from typing import Any

import numpy as np

from . import keywords as kw
from .store import Store, now_iso

PALETTE = ("#0e7490", "#c2410c", "#7c3aed", "#15803d", "#b45309", "#be185d", "#1d4ed8", "#4d7c0f", "#9f1239", "#0f766e",
           "#a16207", "#6d28d9", "#047857", "#b91c1c", "#4338ca", "#0369a1", "#86198f", "#3f6212", "#9a3412", "#334155")

KEY = "folder"   # 表の scope 列に入れる値 (1 つの保存先に意味層は 1 つ)

SCHEMA = """
-- 開発中の版で作った表は使わない
DROP TABLE IF EXISTS layer_meta;
DROP TABLE IF EXISTS clusters;
DROP TABLE IF EXISTS doc_clusters;
DROP TABLE IF EXISTS doc_vectors;

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
    centers BLOB NOT NULL,        -- float32 [まとまりの数 × 次元] (まとまりの番号順)
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


def migrate(conn: sqlite3.Connection) -> None:
    """表を作り、前の版の表に列を足す. 前の版の「範囲ごと」の意味層は、作り直しの対象にする."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(lscopes)")}
    for col, typ in (("version", "INTEGER NOT NULL DEFAULT 0"), ("changes", "TEXT NOT NULL DEFAULT '{}'")):
        if have and col not in have:
            conn.execute(f"ALTER TABLE lscopes ADD COLUMN {col} {typ}")
    conn.commit()
    if have:
        # 前の版でフォルダのパスを範囲の名前にしていた行は、ここでは使わない (作り直す)
        for t in ("lscopes", "lclusters", "ldoc_clusters", "lmodels", "lpara_vectors", "ldoc_vectors"):
            conn.execute(f"DELETE FROM {t} WHERE scope != ?", (KEY,))
        conn.commit()


def signature(store: Store) -> str:
    """資料の集合が変わったか見るための印."""
    row = store.conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(id), 0) AS s, COALESCE(MAX(added_at), '') AS a FROM documents").fetchone()
    return f"{row['n']}:{row['s']}:{row['a']}"


def status(store: Store) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM lscopes WHERE scope = ?", (KEY,)).fetchone()
    n_docs = int(store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    # 前の版で作った意味層 (段落のベクトルが無い) も、作り直しが要るとみなす
    no_model = bool(row and int(row["documents"]) >= 3 and int(row["k"]) >= 1
                    and store.conn.execute("SELECT 1 FROM lmodels WHERE scope = ?", (KEY,)).fetchone() is None)
    return {"scope": store.key, "label": store.label, "built_at": row["built_at"] if row else None,
            "stale": ((row["signature"] if row else None) != signature(store) and n_docs > 0) or no_model,
            "building": store.path in _building, "error": _errors.get(store.path),
            "documents": int(row["documents"]) if row else 0, "documents_now": n_docs,
            "clusters": int(row["k"]) if row else 0, "method": row["method"] if row else None,
            "seconds": row["seconds"] if row else None, "version": int(row["version"]) if row else 0,
            "changes": json.loads(row["changes"] or "{}") if row else {}, "pending_terms": len(kw.pending(store))}


# ---------------- 作る ----------------
def _matrix(store: Store, min_df: int = 2, max_terms: int = 20000):
    """資料 × 語の行列 (疎)."""
    from scipy.sparse import csr_matrix

    doc_ids = [int(r[0]) for r in store.conn.execute("SELECT document_id FROM doc_terms_done ORDER BY document_id")]
    n = len(doc_ids)
    if n == 0:
        return doc_ids, [], None
    max_df = max(min_df, int(n * 0.6)) if n >= 10 else n
    terms = [r["term"] for r in store.conn.execute(
        "SELECT term, COUNT(*) AS df FROM doc_terms GROUP BY term HAVING df >= ? AND df <= ? ORDER BY df DESC LIMIT ?",
        (min(min_df, n), max_df, max_terms))]
    col = {t: i for i, t in enumerate(terms)}
    row_of = {d: i for i, d in enumerate(doc_ids)}
    rows, cols, vals = [], [], []
    for r in store.conn.execute("SELECT document_id, term, n FROM doc_terms"):
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


def _labels(counts, assign: np.ndarray, terms: list[str], groups: list[int], top: int = 8) -> dict[int, list[tuple[str, float]]]:
    """クラス単位の TF-IDF: まとまりの中で多く、ほかでは少ない語."""
    tf = np.vstack([np.asarray(counts[assign == c].sum(axis=0)).ravel() for c in groups])  # まとまり × 語
    total = tf.sum(axis=0) + 1e-9
    avg = tf.sum() / max(1, len(groups))
    idf = np.log(1 + avg / total)
    weight = np.array([kw._weight(t) for t in terms])
    score = (tf / (tf.sum(axis=1, keepdims=True) + 1e-9)) * idf * weight
    out: dict[int, list[tuple[str, float]]] = {}
    for gi, c in enumerate(groups):
        order = np.argsort(-score[gi])
        picked: list[tuple[str, float]] = []
        for j in order:
            t = terms[j]
            if score[gi, j] <= 0:
                break
            # 片方がもう片方を含む語は、先に選んだほうだけにする
            if any(t in p or p in t for p, _ in picked):
                continue
            picked.append((t, float(score[gi, j])))
            if len(picked) >= top:
                break
        out[c] = picked
    return out


def _stable_ids(new_assign: np.ndarray, doc_ids: list[int], prev: dict[int, int]) -> dict[int, int]:
    """新しいまとまり → 番号. 中身の資料が前のまとまりと重なれば、その番号を引き継ぐ (大きいまとまりから順に)."""
    groups = [c for c, _ in Counter(new_assign.tolist()).most_common()]
    used: set[int] = set()
    mapping: dict[int, int] = {}
    for c in groups:
        members = [doc_ids[i] for i in np.where(new_assign == c)[0]]
        overlap = Counter(prev[d] for d in members if d in prev)
        for old, n in overlap.most_common():
            if old not in used and n >= max(1, 0.3 * len(members)):
                mapping[c] = old
                used.add(old)
                break
    nxt = max(list(prev.values()) + [-1]) + 1
    for c in groups:
        if c not in mapping:
            while nxt in used:
                nxt += 1
            mapping[c] = nxt
            used.add(nxt)
    return mapping


def build(store: Store) -> dict[str, Any]:
    """意味層を作り直す."""
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA, TruncatedSVD
    from sklearn.feature_extraction.text import TfidfTransformer
    from sklearn.preprocessing import normalize

    kw.backfill(store)
    sig = signature(store)
    t0 = time.time()
    prev = {int(r["document_id"]): int(r["cluster_id"]) for r in store.conn.execute(
        "SELECT document_id, cluster_id FROM ldoc_clusters WHERE scope = ?", (KEY,))}
    prev_labels = {int(r["id"]): r["label"] for r in store.conn.execute("SELECT id, label FROM lclusters WHERE scope = ?", (KEY,))}
    prev_row = store.conn.execute("SELECT version, built_at FROM lscopes WHERE scope = ?", (KEY,)).fetchone()
    version = (int(prev_row["version"]) if prev_row else 0) + 1
    doc_ids, terms, counts = _matrix(store)
    n = len(doc_ids)
    # 前の版にもあって、そのあと取り込み直した資料 (番号はそのままで中身が変わったもの)
    updated = 0
    if prev_row and prev_row["built_at"]:
        kept = set(doc_ids) & set(prev)
        updated = sum(1 for r in store.conn.execute("SELECT id FROM documents WHERE added_at > ?", (prev_row["built_at"],)) if int(r["id"]) in kept)
    changes = {"added_documents": len(set(doc_ids) - set(prev)), "updated_documents": updated,
               "removed_documents": len(set(prev) - set(doc_ids))}
    if n == 0 or not terms:
        _replace(store, sig, n, 0, "none", t0, version, changes, [], [], [])
        return status(store)
    tf = TfidfTransformer(sublinear_tf=True).fit(counts)
    tfidf = tf.transform(counts)
    # 次元は資料数よりずっと小さくする (資料数に近いと圧縮が効かず、言い換えどうしが近づかない)。
    # ただし 8 より小さいと、関係の薄い段落まで「近い」と出るので、資料が少なくても 8 は取る
    dims = max(1, min(100, len(terms) - 1, n - 1, max(8, n // 4)))
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
        raw = km.labels_
    else:
        raw = np.zeros(n, dtype=int)
    # 前のまとまりと重なるものは同じ番号に
    mapping = _stable_ids(raw, doc_ids, prev)
    assign = np.array([mapping[c] for c in raw])
    ids = sorted(set(assign.tolist()))
    centers = normalize(np.vstack([vecs[assign == c].mean(axis=0) for c in ids])).astype(np.float32)
    center_of = {c: centers[i] for i, c in enumerate(ids)}
    closeness = np.array([float(vecs[i] @ center_of[assign[i]]) for i in range(n)])
    # 地図の座標
    if 5 <= n <= 3000:
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
    labels = _labels(counts, assign, terms, ids)
    crow = []
    for c in ids:
        members = assign == c
        cx, cy = xy[members].mean(axis=0)
        top = labels[c]
        label = "・".join(t for t, _ in top[:3]) or f"まとまり {c + 1}"
        crow.append((KEY, c, label, json.dumps([[t, round(w, 6)] for t, w in top], ensure_ascii=False), int(members.sum()),
                     PALETTE[c % len(PALETTE)], float(cx), float(cy)))
    changes["new_clusters"] = [r[2] for r in crow if r[1] not in prev_labels]
    changes["gone_clusters"] = [prev_labels[c] for c in prev_labels if c not in ids]
    changes["kept_clusters"] = len([c for c in ids if c in prev_labels])
    drow = [(KEY, d, int(assign[i]), float(xy[i, 0]), float(xy[i, 1]), float(closeness[i])) for i, d in enumerate(doc_ids)]
    vrow = [(KEY, d, vecs[i].tobytes()) for i, d in enumerate(doc_ids)]
    model = prow = None
    if svd is not None:
        idf = tf.idf_.astype(np.float32)
        comp = svd.components_.astype(np.float32)
        model = (KEY, json.dumps(terms, ensure_ascii=False), idf.tobytes(), comp.tobytes(), int(comp.shape[0]),
                 json.dumps(ids).encode("utf-8") + b"\n" + centers.tobytes(), len(ids))
        prow = _paragraph_vectors(store, doc_ids, terms, idf, comp)
    _replace(store, sig, n, len(ids), method, t0, version, changes, crow, drow, vrow, model, prow)
    return status(store)


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


def _paragraph_vectors(store: Store, doc_ids: list[int], terms: list[str], idf: np.ndarray, comp: np.ndarray):
    """段落ごとのベクトル (質問と意味で比べるため)."""
    rows, meta = [], []
    for d in doc_ids:
        for para in store.paragraphs_of(d):
            rows.append(kw.extract_terms([para["text"]]))
            meta.append((int(para["id"]), d))
    out = []
    for i in range(0, len(rows), 5000):
        vecs = embed_counts(rows[i:i + 5000], terms, idf, comp)
        out += [(KEY, pid, d, vecs[j].tobytes()) for j, (pid, d) in enumerate(meta[i:i + 5000])]
    return out


def _replace(store: Store, sig: str, n: int, k: int, method: str, t0: float, version: int, changes: dict,
             crow, drow, vrow, model=None, prow=None) -> None:
    """意味層を、まとめて入れ替える (途中の状態を見せない)."""
    with store.tx() as c:
        for t in ("lclusters", "ldoc_clusters", "ldoc_vectors", "lmodels", "lpara_vectors"):
            c.execute(f"DELETE FROM {t} WHERE scope = ?", (KEY,))
        c.executemany("INSERT INTO lclusters(scope, id, label, terms, size, color, x, y) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", crow)
        c.executemany("INSERT INTO ldoc_clusters(scope, document_id, cluster_id, x, y, closeness) VALUES (?, ?, ?, ?, ?, ?)", drow)
        c.executemany("INSERT INTO ldoc_vectors(scope, document_id, vec) VALUES (?, ?, ?)", vrow)
        if model:
            c.execute("INSERT INTO lmodels(scope, terms, idf, components, dims, centers, k) VALUES (?, ?, ?, ?, ?, ?, ?)", model)
        if prow:
            c.executemany("INSERT INTO lpara_vectors(scope, paragraph_id, document_id, vec) VALUES (?, ?, ?, ?)", prow)
        c.execute("INSERT OR REPLACE INTO lscopes(scope, built_at, signature, documents, k, method, seconds, version, changes)"
                  " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (KEY, now_iso(), sig, n, k, method, round(time.time() - t0, 1), version, json.dumps(changes, ensure_ascii=False)))


def model_centers(blob: bytes, dims: int) -> tuple[list[int], np.ndarray]:
    """lmodels.centers から、まとまりの番号と中心のベクトルを取り出す."""
    head, _, body = blob.partition(b"\n")
    try:
        ids = json.loads(head.decode("utf-8"))
        return ids, np.frombuffer(body, dtype=np.float32).reshape(len(ids), dims)
    except (ValueError, UnicodeDecodeError):
        arr = np.frombuffer(blob, dtype=np.float32).reshape(-1, dims)
        return list(range(len(arr))), arr


def build_in_background(store: Store, force: bool = False, after=None) -> bool:
    """意味層が古ければ、裏で作り直す. 始めたら True."""
    if store.path in _building or (not force and not status(store)["stale"]):
        return False
    _building.add(store.path)
    _errors.pop(store.path, None)

    def run() -> None:
        with _build_lock:            # 計算は 1 つずつ
            try:
                build(store)
            except Exception as e:  # noqa: BLE001
                _errors[store.path] = str(e)
            finally:
                _building.discard(store.path)
                if after:
                    after()

    threading.Thread(target=run, daemon=True).start()
    return True


def wait_idle(timeout: float = 120.0) -> bool:
    """裏で動いている作り直しが終わるまで待つ (テストや終了のとき用). 終われば True."""
    end = time.time() + timeout
    while _building and time.time() < end:
        time.sleep(0.05)
    return not _building


# ---------------- 読む ----------------
def clusters(store: Store) -> list[dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT * FROM lclusters WHERE scope = ? ORDER BY size DESC, id", (KEY,)):
        d = dict(r)
        d["terms"] = json.loads(d["terms"])
        out.append(d)
    return out


def map_data(store: Store, max_points: int = 6000) -> dict[str, Any]:
    pts = [dict(r) for r in store.conn.execute(
        "SELECT dc.document_id AS id, dc.cluster_id AS c, dc.x, dc.y, d.title FROM ldoc_clusters dc JOIN documents d ON d.id = dc.document_id"
        " WHERE dc.scope = ? ORDER BY dc.closeness DESC LIMIT ?", (KEY, max_points))]
    return {"status": status(store), "clusters": clusters(store), "points": pts}


def cluster_detail(store: Store, cluster_id: int, limit: int = 60) -> dict[str, Any]:
    row = store.conn.execute("SELECT * FROM lclusters WHERE scope = ? AND id = ?", (KEY, cluster_id)).fetchone()
    if row is None:
        raise KeyError(f"まとまり {cluster_id} はありません")
    d = dict(row)
    d["terms"] = json.loads(d["terms"])
    d["scope"] = store.key
    d["documents"] = [dict(r) for r in store.conn.execute(
        "SELECT d.id, d.title, d.kind, dc.closeness FROM ldoc_clusters dc JOIN documents d ON d.id = dc.document_id"
        " WHERE dc.scope = ? AND dc.cluster_id = ? ORDER BY dc.closeness DESC LIMIT ?", (KEY, cluster_id, limit))]
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


def similar_documents(store: Store, document_id: int, limit: int = 8) -> list[dict[str, Any]]:
    """同じ保存先 (フォルダ) の中で、ベクトルの近い資料."""
    row = store.conn.execute("SELECT vec FROM ldoc_vectors WHERE scope = ? AND document_id = ?", (KEY, document_id)).fetchone()
    if row is None:
        return []
    q = np.frombuffer(row["vec"], dtype=np.float32)
    ids, mats = [], []
    for r in store.conn.execute("SELECT document_id, vec FROM ldoc_vectors WHERE scope = ?", (KEY,)):
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


def cluster_of_terms(store: Store, terms: list[str]) -> dict[str, int]:
    """語ごとに、いちばん強く出るまとまり (キーワードのつながりの色分け用)."""
    if not terms:
        return {}
    q = ",".join("?" * len(terms))
    best: dict[str, tuple[float, int]] = {}
    sizes = {int(r["id"]): int(r["size"]) for r in store.conn.execute("SELECT id, size FROM lclusters WHERE scope = ?", (KEY,))}
    for r in store.conn.execute(
            f"SELECT t.term, dc.cluster_id AS c, COUNT(*) AS n FROM doc_terms t JOIN ldoc_clusters dc ON dc.document_id = t.document_id AND dc.scope = ?"
            f" WHERE t.term IN ({q}) GROUP BY t.term, dc.cluster_id", [KEY, *terms]):
        share = r["n"] / max(1, sizes.get(int(r["c"]), 1)) * math.log(1 + r["n"])
        if r["term"] not in best or share > best[r["term"]][0]:
            best[r["term"]] = (share, int(r["c"]))
    return {t: c for t, (_, c) in best.items()}
