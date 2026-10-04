"""意味層: 型 (課題・解決手段・利用者が足す型)、概念、別名、根拠の段落、概念どうしの関係.

意味層は、利用者の LLM (MCP 経由) か画面から書く。アプリ自身は LLM を呼ばない。
"""

from __future__ import annotations

import re
from typing import Any

from .store import Store, normalize, now_iso

RELATION_KINDS = ("解決する", "引き起こす", "一部である", "関連する")
_PALETTE = ("#7c3aed", "#15803d", "#b45309", "#be185d", "#1d4ed8", "#4d7c0f", "#9f1239", "#0f766e")


# ---------------- 型 ----------------
def list_types(store: Store) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        "SELECT t.name, t.description, t.color, t.ordinal, (SELECT COUNT(*) FROM concepts c WHERE c.type = t.name) AS concepts"
        " FROM types t ORDER BY t.ordinal, t.name").fetchall()
    return [dict(r) for r in rows]


def add_type(store: Store, name: str, description: str = "", color: str | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("型の名前が空です")
    n = int(store.conn.execute("SELECT COUNT(*) FROM types").fetchone()[0])
    if color is None or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = _PALETTE[n % len(_PALETTE)]
    with store.tx() as c:
        created = c.execute("INSERT OR IGNORE INTO types(name, description, color, ordinal) VALUES (?, ?, ?, ?)",
                            (name, description or "", color, 10 + n)).rowcount > 0
        if not created and description:
            c.execute("UPDATE types SET description = ? WHERE name = ?", (description, name))
    return {"name": name, "created": created}


def delete_type(store: Store, name: str) -> bool:
    used = int(store.conn.execute("SELECT COUNT(*) FROM concepts WHERE type = ?", (name,)).fetchone()[0])
    if used:
        raise ValueError(f"型「{name}」の概念が {used} 件あるので消せません")
    with store.tx() as c:
        return c.execute("DELETE FROM types WHERE name = ?", (name,)).rowcount > 0


def _ensure_type(store: Store, type_: str) -> str:
    type_ = (type_ or "").strip()
    if not type_:
        raise ValueError("型が空です")
    if store.conn.execute("SELECT 1 FROM types WHERE name = ?", (type_,)).fetchone() is None:
        add_type(store, type_)
    return type_


# ---------------- 概念 ----------------
def resolve(store: Store, ref: int | str) -> dict[str, Any] | None:
    """ID、名前、別名のどれかで概念を引く (表記ゆれは吸収する)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        row = store.conn.execute("SELECT * FROM concepts WHERE id = ?", (int(ref),)).fetchone()
        if row is not None:
            return dict(row)
    key = normalize(str(ref))
    if not key:
        return None
    row = store.conn.execute("SELECT * FROM concepts WHERE name_norm = ?", (key,)).fetchone()
    if row is None:
        row = store.conn.execute("SELECT c.* FROM concepts c JOIN aliases a ON a.concept_id = c.id WHERE a.alias_norm = ? ORDER BY c.id LIMIT 1",
                                 (key,)).fetchone()
    return dict(row) if row else None


def _require(store: Store, ref: int | str) -> dict[str, Any]:
    c = resolve(store, ref)
    if c is None:
        raise KeyError(f"概念「{ref}」はありません")
    return c


def upsert_concept(store: Store, name: str, type_: str, description: str | None = None,
                   aliases: list[str] | None = None) -> dict[str, Any]:
    """概念を足す. 同じ名前 (または別名) の概念が既にあれば、それに説明と別名を足す (型は変えない)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("概念の名前が空です")
    existing = resolve(store, name)
    if existing is not None:
        if description:
            with store.tx() as c:
                c.execute("UPDATE concepts SET description = ?, updated_at = ? WHERE id = ?", (description, now_iso(), existing["id"]))
        added = add_aliases(store, existing["id"], aliases or [])
        out = {"id": existing["id"], "name": existing["name"], "type": existing["type"], "created": False, "aliases_added": added}
        if type_ and type_ != existing["type"]:
            out["note"] = f"既にある概念なので型は「{existing['type']}」のままです"
        return out
    type_ = _ensure_type(store, type_)
    with store.tx() as c:
        cid = c.execute("INSERT INTO concepts(name, name_norm, type, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (name, normalize(name), type_, description or "", now_iso(), now_iso())).lastrowid
    added = add_aliases(store, int(cid), aliases or [])
    return {"id": int(cid), "name": name, "type": type_, "created": True, "aliases_added": added}


def update_concept(store: Store, ref: int | str, name: str | None = None, type_: str | None = None,
                   description: str | None = None) -> dict[str, Any]:
    c = _require(store, ref)
    fields: dict[str, Any] = {}
    if name and name.strip() and name.strip() != c["name"]:
        other = resolve(store, name)
        if other is not None and other["id"] != c["id"]:
            raise ValueError(f"「{name}」は既に概念 {other['id']} の名前か別名です。統合を使ってください")
        fields["name"] = name.strip()
        fields["name_norm"] = normalize(name)
    if type_ and type_ != c["type"]:
        fields["type"] = _ensure_type(store, type_)
    if description is not None:
        fields["description"] = description
    if fields:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with store.tx() as conn:
            conn.execute(f"UPDATE concepts SET {sets} WHERE id = ?", [*fields.values(), c["id"]])
    return get_concept(store, c["id"])


def delete_concept(store: Store, ref: int | str) -> bool:
    c = _require(store, ref)
    with store.tx() as conn:
        return conn.execute("DELETE FROM concepts WHERE id = ?", (c["id"],)).rowcount > 0


def add_aliases(store: Store, ref: int | str, aliases: list[str]) -> int:
    c = _require(store, ref)
    n = 0
    with store.tx() as conn:
        for a in aliases or []:
            a = (a or "").strip()
            an = normalize(a)
            if not a or an == c["name_norm"]:
                continue
            clash = conn.execute("SELECT id FROM concepts WHERE name_norm = ? AND id != ?", (an, c["id"])).fetchone()
            if clash is not None:
                continue
            n += conn.execute("INSERT OR IGNORE INTO aliases(concept_id, alias, alias_norm) VALUES (?, ?, ?)", (c["id"], a, an)).rowcount
    return n


def remove_alias(store: Store, ref: int | str, alias: str) -> bool:
    c = _require(store, ref)
    with store.tx() as conn:
        return conn.execute("DELETE FROM aliases WHERE concept_id = ? AND alias_norm = ?", (c["id"], normalize(alias))).rowcount > 0


def merge_concepts(store: Store, keep: int | str, drop: int | str) -> dict[str, Any]:
    """drop を keep にまとめる. drop の名前は keep の別名になり、根拠と関係は keep に付け替える."""
    k = _require(store, keep)
    d = _require(store, drop)
    if k["id"] == d["id"]:
        raise ValueError("同じ概念です")
    drop_aliases = [r["alias"] for r in store.conn.execute("SELECT alias FROM aliases WHERE concept_id = ?", (d["id"],))]
    with store.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO evidence(concept_id, paragraph_id, note, created_at) SELECT ?, paragraph_id, note, created_at FROM evidence WHERE concept_id = ?",
                     (k["id"], d["id"]))
        for r in conn.execute("SELECT * FROM relations WHERE src_id = ? OR dst_id = ?", (d["id"], d["id"])).fetchall():
            src = k["id"] if r["src_id"] == d["id"] else r["src_id"]
            dst = k["id"] if r["dst_id"] == d["id"] else r["dst_id"]
            if src != dst:
                conn.execute("INSERT OR IGNORE INTO relations(src_id, dst_id, kind, paragraph_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (src, dst, r["kind"], r["paragraph_id"], r["note"], r["created_at"]))
        conn.execute("DELETE FROM concepts WHERE id = ?", (d["id"],))
    add_aliases(store, k["id"], [d["name"], *drop_aliases])
    return get_concept(store, k["id"])


# ---------------- 根拠 ----------------
def add_evidence(store: Store, ref: int | str, paragraph_ids: list[int], note: str = "") -> dict[str, Any]:
    """概念が書かれている段落を根拠として結ぶ. 無い段落番号は飛ばして返す."""
    c = _require(store, ref)
    ids = [int(p) for p in paragraph_ids or []]
    found = store.get_paragraphs(ids)
    added = 0
    with store.tx() as conn:
        for pid in ids:
            if pid in found:
                added += conn.execute("INSERT OR IGNORE INTO evidence(concept_id, paragraph_id, note, created_at) VALUES (?, ?, ?, ?)",
                                      (c["id"], pid, note or "", now_iso())).rowcount
    return {"concept_id": c["id"], "added": added, "missing": [p for p in ids if p not in found]}


def remove_evidence(store: Store, ref: int | str, paragraph_id: int) -> bool:
    c = _require(store, ref)
    with store.tx() as conn:
        return conn.execute("DELETE FROM evidence WHERE concept_id = ? AND paragraph_id = ?", (c["id"], int(paragraph_id))).rowcount > 0


# ---------------- 関係 ----------------
def relate(store: Store, src: int | str, dst: int | str, kind: str, paragraph_id: int | None = None, note: str = "") -> dict[str, Any]:
    """概念どうしを結ぶ (例: 解決手段 →解決する→ 課題). 根拠の段落を付けられる."""
    s = _require(store, src)
    d = _require(store, dst)
    if s["id"] == d["id"]:
        raise ValueError("同じ概念どうしは結べません")
    kind = (kind or "関連する").strip()
    if paragraph_id is not None and not store.get_paragraphs([int(paragraph_id)]):
        raise KeyError(f"段落 ¶{paragraph_id} はありません")
    with store.tx() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO relations(src_id, dst_id, kind, paragraph_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (s["id"], d["id"], kind, paragraph_id, note or "", now_iso()))
        created = cur.rowcount > 0
        if not created and (paragraph_id is not None or note):
            conn.execute("UPDATE relations SET paragraph_id = COALESCE(?, paragraph_id), note = CASE WHEN ? != '' THEN ? ELSE note END"
                         " WHERE src_id = ? AND dst_id = ? AND kind = ?", (paragraph_id, note or "", note or "", s["id"], d["id"], kind))
    rid = store.conn.execute("SELECT id FROM relations WHERE src_id = ? AND dst_id = ? AND kind = ?", (s["id"], d["id"], kind)).fetchone()["id"]
    return {"id": int(rid), "src": s["name"], "dst": d["name"], "kind": kind, "created": created}


def delete_relation(store: Store, relation_id: int) -> bool:
    with store.tx() as conn:
        return conn.execute("DELETE FROM relations WHERE id = ?", (int(relation_id),)).rowcount > 0


# ---------------- 読む ----------------
def list_concepts(store: Store, type_: str | None = None, query: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    sql = ("SELECT c.id, c.name, c.type, c.description,"
           " (SELECT COUNT(*) FROM evidence e WHERE e.concept_id = c.id) AS evidence,"
           " (SELECT COUNT(DISTINCT p.document_id) FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id WHERE e.concept_id = c.id) AS documents,"
           " (SELECT COUNT(*) FROM relations r WHERE r.src_id = c.id OR r.dst_id = c.id) AS relations,"
           " (SELECT GROUP_CONCAT(a.alias, ' / ') FROM aliases a WHERE a.concept_id = c.id) AS aliases"
           " FROM concepts c")
    where: list[str] = []
    params: list[Any] = []
    if type_:
        where.append("c.type = ?")
        params.append(type_)
    if query:
        q = f"%{normalize(query)}%"
        where.append("(c.name_norm LIKE ? OR EXISTS (SELECT 1 FROM aliases a WHERE a.concept_id = c.id AND a.alias_norm LIKE ?) OR c.description LIKE ?)")
        params += [q, q, f"%{query}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY documents DESC, evidence DESC, c.name LIMIT ?"
    params.append(limit)
    return [dict(r) for r in store.conn.execute(sql, params)]


def get_concept(store: Store, ref: int | str, evidence_limit: int = 50) -> dict[str, Any]:
    c = _require(store, ref)
    aliases = [r["alias"] for r in store.conn.execute("SELECT alias FROM aliases WHERE concept_id = ? ORDER BY alias", (c["id"],))]
    evidence = [dict(r) for r in store.conn.execute(
        "SELECT e.paragraph_id, e.note, p.document_id, d.title, p.heading, p.text FROM evidence e"
        " JOIN paragraphs p ON p.id = e.paragraph_id JOIN documents d ON d.id = p.document_id"
        " WHERE e.concept_id = ? ORDER BY p.document_id, p.ordinal LIMIT ?", (c["id"], evidence_limit))]
    for e in evidence:
        text = e["text"]
        if e["heading"] and text.startswith(e["heading"]):
            text = text[len(e["heading"]):].lstrip("\n")
        e["text"] = text[:400]
    relations = []
    for r in store.conn.execute(
            "SELECT r.id, r.kind, r.paragraph_id, r.note, r.src_id, r.dst_id, s.name AS src, s.type AS src_type, d.name AS dst, d.type AS dst_type"
            " FROM relations r JOIN concepts s ON s.id = r.src_id JOIN concepts d ON d.id = r.dst_id WHERE r.src_id = ? OR r.dst_id = ? ORDER BY r.kind, r.id",
            (c["id"], c["id"])):
        d = dict(r)
        d["direction"] = "out" if r["src_id"] == c["id"] else "in"
        relations.append(d)
    total = int(store.conn.execute("SELECT COUNT(*) FROM evidence WHERE concept_id = ?", (c["id"],)).fetchone()[0])
    return {"id": c["id"], "name": c["name"], "type": c["type"], "description": c["description"], "aliases": aliases,
            "evidence": evidence, "evidence_total": total, "relations": relations}


def concepts_in_document(store: Store, document_id: int) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        "SELECT c.id, c.name, c.type, GROUP_CONCAT(e.paragraph_id) AS paragraphs FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id"
        " JOIN concepts c ON c.id = e.concept_id WHERE p.document_id = ? GROUP BY c.id ORDER BY c.type, c.name", (document_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "type": r["type"], "paragraphs": [int(x) for x in str(r["paragraphs"]).split(",") if x]} for r in rows]


def documents_without_concepts(store: Store, limit: int = 20) -> list[dict[str, Any]]:
    """まだ意味層に書かれていない資料 (根拠の段落が 1 つも無い資料)."""
    rows = store.conn.execute(
        "SELECT d.id, d.title, d.kind, (SELECT COUNT(*) FROM paragraphs p WHERE p.document_id = d.id) AS paragraphs FROM documents d"
        " WHERE NOT EXISTS (SELECT 1 FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id WHERE p.document_id = d.id)"
        " ORDER BY d.id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def graph(store: Store, with_documents: bool = True, type_: str | None = None) -> dict[str, Any]:
    """可視化用のノードと辺. 概念 (型で色分け)、資料、概念どうしの関係、概念と資料の根拠."""
    colors = {t["name"]: t["color"] for t in list_types(store)}
    concepts = list_concepts(store, type_=type_, limit=5000)
    ids = {c["id"] for c in concepts}
    nodes = [{"id": f"c{c['id']}", "label": c["name"], "kind": "concept", "type": c["type"], "color": colors.get(c["type"], "#6b7280"),
              "size": 1 + c["documents"]} for c in concepts]
    edges = []
    for r in store.conn.execute("SELECT id, src_id, dst_id, kind FROM relations"):
        if r["src_id"] in ids and r["dst_id"] in ids:
            edges.append({"source": f"c{r['src_id']}", "target": f"c{r['dst_id']}", "kind": r["kind"], "relation": True})
    if with_documents:
        docs = {}
        for r in store.conn.execute("SELECT e.concept_id, p.document_id, d.title, COUNT(*) AS n FROM evidence e JOIN paragraphs p ON p.id = e.paragraph_id"
                                    " JOIN documents d ON d.id = p.document_id GROUP BY e.concept_id, p.document_id"):
            if r["concept_id"] not in ids:
                continue
            docs[r["document_id"]] = r["title"]
            edges.append({"source": f"c{r['concept_id']}", "target": f"d{r['document_id']}", "kind": "根拠", "weight": r["n"], "relation": False})
        nodes += [{"id": f"d{i}", "label": t, "kind": "document", "type": "資料", "color": "#9ca3af", "size": 1} for i, t in docs.items()]
    return {"nodes": nodes, "edges": edges, "types": list_types(store)}
