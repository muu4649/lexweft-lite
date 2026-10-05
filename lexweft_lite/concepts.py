"""課題と解決手段 (LLM と利用者が書く意味層) を、フォルダをまたいで扱う.

概念は、根拠の段落があるフォルダの保存先に入る (フォルダごとに分かれる)。
番号は保存先ごとに範囲が分かれているので、番号から保存先が分かる。名前で探すときは全フォルダから探す。
"""

from __future__ import annotations

from typing import Any

from . import layer as ly
from .library import CENTRAL, Library
from .store import Store, normalize


def _stores(lib: Library, scope: str | None) -> list[Store]:
    return [lib.for_scope(scope)] if scope else lib.stores()


def _is_id(ref: Any) -> bool:
    return isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit())


def resolve(lib: Library, ref: int | str, scope: str | None = None) -> tuple[Store, dict[str, Any]]:
    """番号・名前・別名で概念を探す. 同じ名前が複数のフォルダにあるときは、フォルダを指定してもらう."""
    if _is_id(ref):
        st = lib.for_id(int(ref))
        c = ly.resolve(st, int(ref))
        if c:
            return st, c
    hits = []
    for st in _stores(lib, scope):
        c = ly.resolve(st, str(ref))
        if c:
            hits.append((st, c))
    if not hits:
        raise KeyError(f"概念「{ref}」はありません")
    if len(hits) > 1:
        raise ValueError(f"「{ref}」は複数のフォルダにあります ({', '.join(st.label for st, _ in hits)})。scope でフォルダを指定してください")
    return hits[0]


def store_for_paragraphs(lib: Library, paragraph_ids: list[int]) -> Store:
    stores = {}
    for pid in paragraph_ids:
        st = lib.for_id(int(pid))
        stores[st.path] = st
    if len(stores) > 1:
        raise ValueError("根拠の段落が複数のフォルダにまたがっています。フォルダごとに分けて書いてください")
    if not stores:
        raise ValueError("根拠の段落番号 paragraph_ids を 1 つ以上付けてください")
    return next(iter(stores.values()))


def write(lib: Library, name: str, type_: str, paragraph_ids: list[int], description: str | None = None,
          aliases: list[str] | None = None, note: str = "") -> dict[str, Any]:
    """概念を、根拠の段落があるフォルダに書く (同じ名前の概念がそのフォルダにあれば、それに足す)."""
    st = store_for_paragraphs(lib, paragraph_ids)
    out = ly.upsert_concept(st, name, type_, description, aliases or [])
    out["evidence"] = ly.add_evidence(st, out["id"], paragraph_ids, note)
    out["scope"], out["folder"] = st.key, st.label
    return out


def add_evidence(lib: Library, ref: int | str, paragraph_ids: list[int], note: str = "") -> dict[str, Any]:
    st, c = resolve(lib, ref)
    other = store_for_paragraphs(lib, paragraph_ids)
    if other.path != st.path:
        raise ValueError(f"概念「{c['name']}」は {st.label} にあり、段落は {other.label} にあります。同じフォルダの段落を指定してください")
    return ly.add_evidence(st, c["id"], paragraph_ids, note)


def relate(lib: Library, source: int | str, target: int | str, kind: str, paragraph_id: int | None = None, note: str = "",
           scope: str | None = None) -> dict[str, Any]:
    s_st, s = resolve(lib, source, scope)
    t_st, t = resolve(lib, target, scope or (s_st.key if s_st.key != CENTRAL else None))
    if s_st.path != t_st.path:
        raise ValueError("違うフォルダの概念どうしは結べません (意味層はフォルダごとに分かれています)")
    out = ly.relate(s_st, s["id"], t["id"], kind, paragraph_id, note)
    out["scope"], out["folder"] = s_st.key, s_st.label
    return out


def list_concepts(lib: Library, scope: str | None = None, type_: str | None = None, query: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    out = []
    for st in _stores(lib, scope):
        for c in ly.list_concepts(st, type_=type_, query=query, limit=limit):
            out.append({**c, "scope": st.key, "folder": st.label})
    out.sort(key=lambda c: (-c["documents"], -c["evidence"], c["name"]))
    return out[:limit]


def get_concept(lib: Library, ref: int | str, scope: str | None = None, evidence_limit: int = 50) -> dict[str, Any]:
    st, c = resolve(lib, ref, scope)
    d = ly.get_concept(st, c["id"], evidence_limit)
    d["scope"], d["folder"] = st.key, st.label
    return d


def update(lib: Library, ref: int | str, name: str | None = None, type_: str | None = None, description: str | None = None) -> dict[str, Any]:
    st, c = resolve(lib, ref)
    if type_:
        _ensure_type_everywhere(lib, type_)
    d = ly.update_concept(st, c["id"], name, type_, description)
    d["scope"], d["folder"] = st.key, st.label
    return d


def delete(lib: Library, ref: int | str) -> bool:
    st, c = resolve(lib, ref)
    return ly.delete_concept(st, c["id"])


def merge(lib: Library, keep: int | str, drop: int | str) -> dict[str, Any]:
    k_st, k = resolve(lib, keep)
    d_st, d = resolve(lib, drop, k_st.key if k_st.key != CENTRAL else None)
    if k_st.path != d_st.path:
        raise ValueError("違うフォルダの概念はまとめられません")
    return ly.merge_concepts(k_st, k["id"], d["id"])


def add_aliases(lib: Library, ref: int | str, aliases: list[str]) -> int:
    st, c = resolve(lib, ref)
    return ly.add_aliases(st, c["id"], aliases)


def remove_alias(lib: Library, ref: int | str, alias: str) -> bool:
    st, c = resolve(lib, ref)
    return ly.remove_alias(st, c["id"], alias)


def remove_evidence(lib: Library, ref: int | str, paragraph_id: int) -> bool:
    st, c = resolve(lib, ref)
    return ly.remove_evidence(st, c["id"], paragraph_id)


def delete_relation(lib: Library, relation_id: int) -> bool:
    return ly.delete_relation(lib.for_id(relation_id), relation_id)


# ---------------- 型 (すべての保存先で同じものを使う) ----------------
def list_types(lib: Library) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for st in lib.stores():
        for t in ly.list_types(st):
            if t["name"] in merged:
                merged[t["name"]]["concepts"] += t["concepts"]
            else:
                merged[t["name"]] = dict(t)
    return sorted(merged.values(), key=lambda t: (t["ordinal"], t["name"]))


def _ensure_type_everywhere(lib: Library, name: str, description: str = "", color: str | None = None) -> None:
    known = {t["name"]: t for t in list_types(lib)}
    if name in known:
        color = color or known[name]["color"]
        description = description or known[name]["description"]
    for st in lib.stores():
        ly.add_type(st, name, description, color)


def add_type(lib: Library, name: str, description: str = "", color: str | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("型の名前が空です")
    created = name not in {t["name"] for t in list_types(lib)}
    _ensure_type_everywhere(lib, name, description, color)
    return {"name": name, "created": created}


def delete_type(lib: Library, name: str) -> bool:
    used = sum(t["concepts"] for t in list_types(lib) if t["name"] == name)
    if used:
        raise ValueError(f"型「{name}」の概念が {used} 件あるので消せません")
    return any([ly.delete_type(st, name) for st in lib.stores()])


# ---------------- 図と書き出し ----------------
def graph(lib: Library, scope: str | None = None, with_documents: bool = True, type_: str | None = None) -> dict[str, Any]:
    nodes, edges = [], []
    for st in _stores(lib, scope):
        g = ly.graph(st, with_documents=with_documents, type_=type_)
        for n in g["nodes"]:
            n["id"] = f"{st.store_no}:{n['id']}"
            n["scope"] = st.key
            nodes.append(n)
        for e in g["edges"]:
            e["source"] = f"{st.store_no}:{e['source']}"
            e["target"] = f"{st.store_no}:{e['target']}"
            edges.append(e)
    return {"nodes": nodes, "edges": edges, "types": list_types(lib)}


def layer_markdown(lib: Library, scope: str | None = None) -> str:
    from .markdown import layer_markdown as one

    parts = []
    for st in _stores(lib, scope):
        if not st.stats()["concepts"]:
            continue
        body = one(st).replace("# 意味層", f"# 意味層: {st.label}", 1)
        parts.append(body)
    return "\n".join(parts) if parts else "# 意味層\n\nまだ課題と解決手段が書かれていません。\n"


def documents_without_concepts(lib: Library, limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for st in lib.stores():
        for d in ly.documents_without_concepts(st, limit):
            out.append({**d, "scope": st.key, "folder": st.label})
    return out[:limit]


def concepts_in_document(lib: Library, document_id: int) -> list[dict[str, Any]]:
    return ly.concepts_in_document(lib.for_id(document_id), document_id)


def normalize_name(name: str) -> str:
    return normalize(name)
