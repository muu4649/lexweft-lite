"""グループ (タグ): 登録したフォルダの 1 段下のサブフォルダ名を、資料のグループにする.

グループは資料の置き場所から自動で決まる (取り込み済みの資料にもそのまま効く)。画面で付けた表示の名前だけを保存する。
グループどうしの関わり: 片方のグループの資料ごとに、もう片方のグループで意味のベクトルが近い資料を探し、
近い組を「まとまり ↔ まとまり」の線にまとめる。近いかどうかはベクトルの近さで決めているので、本当に関係があるかは本文で確かめる。
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from . import clusters as cl
from .store import Store

# まとまりの色と見分けやすい、はっきりした色
COLORS = ["#0e7490", "#c2410c", "#7c3aed", "#15803d", "#be185d", "#a16207", "#1d4ed8", "#4b5563"]
TOP = ""   # 登録したフォルダの直下に置いた資料
TOP_LABEL = "（フォルダの直下）"
TOP_COLOR = "#9ca3af"


def group_of_source(stored: str) -> str:
    """保存先に入っている資料の場所 (登録フォルダからの相対パス) から、グループ (最初のサブフォルダ名) を返す."""
    parts = (stored or "").replace("\\", "/").split("/")
    return parts[0] if len(parts) >= 2 and not stored.startswith(("/", "http://", "https://", "text:")) else TOP


def doc_groups(store: Store) -> dict[int, str]:
    if not store.root:
        return {}
    return {int(r["id"]): group_of_source(r["source"]) for r in store.conn.execute("SELECT id, source FROM documents")}


def _labels(store: Store) -> dict[str, str]:
    try:
        return json.loads(store.meta("group_labels") or "{}")
    except ValueError:
        return {}


def groups(store: Store) -> list[dict[str, Any]]:
    """グループの一覧 (資料の多い順). 色は名前の順で決める (一覧の並びが変わっても色は変わらない)."""
    counts = Counter(doc_groups(store).values())
    labels = _labels(store)
    # 直下の資料はいつも灰色にして、サブフォルダの色がずれないようにする
    color = {k: COLORS[i % len(COLORS)] for i, k in enumerate(sorted(k for k in counts if k != TOP))}
    color[TOP] = TOP_COLOR
    out = [{"key": k, "label": labels.get(k) or (k if k != TOP else TOP_LABEL), "color": color[k], "documents": n} for k, n in counts.items()]
    return sorted(out, key=lambda g: (-g["documents"], g["key"]))


def rename(store: Store, key: str, label: str) -> dict[str, Any]:
    if key not in set(doc_groups(store).values()):
        raise KeyError(f"グループ「{key}」はありません")
    labels = _labels(store)
    label = (label or "").strip()
    if label and label != key:
        labels[key] = label
    else:
        labels.pop(key, None)   # 空にすると、フォルダ名に戻す
    store.set_meta("group_labels", json.dumps(labels, ensure_ascii=False))
    return {"key": key, "label": labels.get(key) or key}


def add_to_map(store: Store, data: dict[str, Any]) -> dict[str, Any]:
    """地図のデータに、点のグループ・グループの一覧・まとまりごとのグループの内訳と「橋渡し」の印を足す."""
    g = doc_groups(store)
    data["groups"] = groups(store)
    for p in data["points"]:
        p["g"] = g.get(int(p["id"]), TOP)
    mix: dict[int, Counter] = defaultdict(Counter)
    for r in store.conn.execute("SELECT document_id, cluster_id FROM ldoc_clusters WHERE scope = ?", (cl.KEY,)):
        mix[int(r["cluster_id"])][g.get(int(r["document_id"]), TOP)] += 1
    for c in data["clusters"]:
        m = mix.get(int(c["id"]), Counter())
        c["groups"] = dict(m)
        size = sum(m.values()) or 1
        # 2 つ以上のグループが、それぞれ 2 件以上かつ 1 割以上入っているまとまり
        c["bridge"] = len(data["groups"]) >= 2 and sum(1 for n in m.values() if n >= 2 and n / size >= 0.1) >= 2
    return data


def _vectors(store: Store, ids: list[int]) -> tuple[list[int], np.ndarray]:
    want = set(ids)
    keep, rows = [], []
    for r in store.conn.execute("SELECT document_id, vec FROM ldoc_vectors WHERE scope = ?", (cl.KEY,)):
        d = int(r["document_id"])
        if d in want:
            keep.append(d)
            rows.append(np.frombuffer(r["vec"], dtype=np.float32))
    return keep, (np.vstack(rows) if rows else np.zeros((0, 1), dtype=np.float32))


def links(store: Store, a: str | None = None, b: str | None = None, per_doc: int = 3, max_edges: int = 30,
          examples: int = 5) -> dict[str, Any]:
    """2 つのグループの関わり: まとまり (左 = グループ a、右 = グループ b) と、近い資料の組をまとめた線."""
    gl = groups(store)
    out: dict[str, Any] = {"groups": gl, "a": None, "b": None, "left": [], "right": [], "edges": [], "pairs": 0}
    if len(gl) < 2:
        out["note"] = "グループが 1 つしかありません。登録したフォルダの中をサブフォルダに分けると、サブフォルダ名がグループになります。"
        return out
    keys = [g["key"] for g in gl]
    a = a if a in keys else keys[0]
    b = b if b in keys and b != a else next(k for k in keys if k != a)
    out["a"], out["b"] = a, b
    dg = doc_groups(store)
    ida, va = _vectors(store, [d for d, k in dg.items() if k == a])
    idb, vb = _vectors(store, [d for d, k in dg.items() if k == b])
    if not len(ida) or not len(idb):
        out["note"] = "意味層がまだできていません。"
        return out
    sims = va @ vb.T
    # 目立って近い組だけを残す (どの組も近く見えることがあるので、全体の平均より十分高いもの)
    floor = max(0.2, float(sims.mean() + 2 * sims.std()))
    k = min(per_doc, sims.shape[1])
    picked: dict[tuple[int, int], float] = {}
    for i in range(sims.shape[0]):   # a の資料ごとに、近い b の資料
        for j in np.argsort(-sims[i])[:k]:
            if sims[i, j] >= floor:
                picked[(i, int(j))] = float(sims[i, j])
    k = min(per_doc, sims.shape[0])
    for j in range(sims.shape[1]):   # b の資料ごとに、近い a の資料
        for i in np.argsort(-sims[:, j])[:k]:
            if sims[i, j] >= floor:
                picked[(int(i), j)] = float(sims[i, j])
    cluster_of = {int(r["document_id"]): int(r["cluster_id"]) for r in store.conn.execute(
        "SELECT document_id, cluster_id FROM ldoc_clusters WHERE scope = ?", (cl.KEY,))}
    info = {int(r["id"]): (r["label"], r["color"]) for r in store.conn.execute("SELECT id, label, color FROM lclusters WHERE scope = ?", (cl.KEY,))}
    edges: dict[tuple[int, int], list[tuple[float, int, int]]] = defaultdict(list)
    for (i, j), s in picked.items():
        da, db = ida[i], idb[j]
        if da in cluster_of and db in cluster_of:
            edges[(cluster_of[da], cluster_of[db])].append((s, da, db))
    ranked = sorted(edges.items(), key=lambda kv: -(len(kv[1]) * float(np.mean([x[0] for x in kv[1]]))))[:max_edges]
    titles = {int(r["id"]): r["title"] for r in store.conn.execute("SELECT id, title FROM documents")}
    df = {r["term"]: int(r["n"]) for r in store.conn.execute("SELECT term, COUNT(*) AS n FROM doc_terms GROUP BY term")}
    terms_cache: dict[int, set[str]] = {}

    def terms(d: int) -> set[str]:
        if d not in terms_cache:
            terms_cache[d] = {r["term"] for r in store.conn.execute("SELECT term FROM doc_terms WHERE document_id = ?", (d,))}
        return terms_cache[d]

    n_docs = max(1, len(titles))

    def shared(pairs: list[tuple[float, int, int]], n: int) -> list[str]:
        c: Counter = Counter()
        for _, da, db in pairs:
            for t in terms(da) & terms(db):
                if df.get(t, 0) <= 0.3 * n_docs:   # どの資料にも出る語 (見出しの語など) は、つながりの説明にならない
                    c[t] += 1
        # 多くの組に共通し、全体ではあまり出ない語ほど前に
        return [t for t, _ in sorted(c.items(), key=lambda kv: -kv[1] * math.log(n_docs / max(1, df.get(kv[0], 1))))[:n]]

    count_a: Counter = Counter(cluster_of[d] for d in ida if d in cluster_of)
    count_b: Counter = Counter(cluster_of[d] for d in idb if d in cluster_of)
    used_a, used_b = set(), set()
    for (ca, cb), pairs in ranked:
        pairs.sort(reverse=True)
        used_a.add(ca)
        used_b.add(cb)
        out["edges"].append({
            "a": ca, "b": cb, "pairs": len(pairs), "similarity": round(float(np.mean([p[0] for p in pairs])), 3),
            "terms": shared(pairs, 8),
            "examples": [{"a": {"id": da, "title": titles.get(da, "")}, "b": {"id": db, "title": titles.get(db, "")},
                          "similarity": round(s, 3), "terms": shared([(s, da, db)], 6)} for s, da, db in pairs[:examples]],
        })
    node = lambda c, n: {"id": c, "label": info.get(c, ("", ""))[0], "color": info.get(c, ("", "#6b7280"))[1], "documents": n}
    out["left"] = sorted([node(c, count_a[c]) for c in used_a], key=lambda x: -x["documents"])
    out["right"] = sorted([node(c, count_b[c]) for c in used_b], key=lambda x: -x["documents"])
    out["pairs"] = len(picked)
    out["threshold"] = round(floor, 3)
    out["note"] = "近い組は、意味のベクトルの近さで選んでいます。本当に関係があるかは、資料を開いて確かめてください。"
    return out


def annotate(lib: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """資料の一覧 (scope と document_id を持つもの) に、グループ (名前と色) を足す. グループが 2 つ以上あるフォルダだけ."""
    cache: dict[str, tuple[dict[int, str], dict[str, dict[str, Any]]]] = {}
    for it in items:
        scope = it.get("scope")
        if not scope:
            continue
        if scope not in cache:
            st = lib.for_scope(scope)
            gl = groups(st)
            cache[scope] = (doc_groups(st), {g["key"]: g for g in gl} if len(gl) >= 2 else {})
        dg, info = cache[scope]
        g = info.get(dg.get(int(it["document_id"]), TOP))
        if g:
            it["group"] = {"key": g["key"], "label": g["label"], "color": g["color"]}
    return items
