"""キーワードのつながり (取り込んだ時点で自動で作る).

資料ごとに語 (漢字・カタカナの連なり、英単語) を数えて保存し、
資料に一緒に出てくる語どうしを結んだ共起ネットワークを返す。LLM は使わない。
意味層 (課題・解決手段) とは別のもので、意味層を書く前の見取り図として使う。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable

from .store import Store


# 語の数え方を変えたら上げる (取り込み済みの資料も数え直し、意味層を作り直す)
TERMS_VERSION = "4"
# 長い語も途中で切らずに取り出し、長すぎるもの (20 文字を超える) は数えない
_TERM_RE = re.compile(r"[一-龥々〆ヵヶァ-ヴー]{2,}|[A-Za-z][A-Za-z0-9\-]{2,30}")
_MAX_JA = 20
# 特許の「前記ロボット」「当該装置」などは、頭の語を外して数える
_PREFIXES = ("前記", "上記", "当該")
_CONJ_RE = re.compile(r"及び|並びに|又は|若しくは|乃至")
_STOP_JA = {
    "場合", "以下", "以上", "上記", "前記", "本発明", "発明", "実施", "実施形態", "本実施形態", "実施例", "形態", "一例", "例え", "参照", "記載",
    "図示", "説明", "構成", "方法", "可能", "必要", "特徴", "目的", "結果", "対象", "情報", "内容", "部分", "全体", "一方", "他方", "同様",
    "所定", "各種", "複数", "一部", "本願", "請求項", "段落", "表示", "使用", "利用", "提供", "関連", "関係", "以降", "従来", "今後", "現在",
    "年度", "時点", "資料", "本書", "概要", "ページ", "データ", "システム", "ため", "こと", "もの", "これ", "それ", "ここ", "について",
}
_STOP_JA |= {
    "本開示", "前記第", "特許文献", "非特許文献", "特願", "特開", "特表", "公報", "出願", "出願人", "発明者", "明細書", "請求", "図面", "符号",
    "実施の形態", "変形例", "第一", "第二", "第三", "具備", "備え", "有する", "含む", "当該", "該当", "上述", "後述", "下記", "同図",
    # 特許の決まり文句 (「(メタ)アクリレート」の「メタ」もここ)
    "ステップ", "一般式", "一実施形態", "一実施", "本実施", "実施態様", "一態様", "態様", "メタ", "化学式", "構造式",
    "製造方法", "構成要素", "複数種類",
    # 図面の説明 (「図面の簡単な説明」「〜のブロック図である」など)
    "簡単", "様々", "ブロック図", "模式図", "フローチャート", "斜視図", "断面図", "正面図", "平面図", "側面図", "説明図", "概略図", "概念図",
}
_STOP_EN = {
    "the", "and", "for", "with", "from", "this", "that", "these", "those", "which", "were", "have", "has", "been", "also", "into", "such",
    "than", "then", "their", "there", "they", "them", "what", "when", "where", "while", "will", "would", "can", "could", "may", "might",
    "should", "not", "are", "was", "its", "our", "your", "use", "used", "using", "based", "each", "other", "more", "most", "some", "any",
    "all", "one", "two", "via", "per", "but", "out", "about", "over", "under", "between", "within", "without", "http", "https", "www", "com",
    "pdf", "fig", "figure", "table", "page", "etc",
}


_FENCE_RE = re.compile(r"```.*?```", re.S)
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_CODE_CHARS = set("{};=<>$\\|")   # 角かっこ ([1] や Markdown のリンク) は文章にもよく出るので、コードの印にしない
_JA_CHAR_RE = re.compile(r"[ぁ-んァ-ヴ一-龥]")


def _prose(text: str) -> str:
    """コード片・HTML のタグ・記号の多い行を外して、文章だけにする."""
    text = _TAG_RE.sub(" ", _FENCE_RE.sub(" ", text or ""))
    keep = []
    for line in text.splitlines():
        if line and sum(ch in _CODE_CHARS for ch in line) / len(line) > 0.04:
            continue
        keep.append(line)
    return "\n".join(keep)


def extract_terms(texts: Iterable[str]) -> Counter:
    """語を数える. 日本語が主の資料では、英語は大文字の略語 (PCM, LiDAR など) だけを数える."""
    body = "\n".join(_prose(t) for t in texts)
    japanese = len(_JA_CHAR_RE.findall(body)) >= len(body) * 0.1
    counts: Counter = Counter()
    # 「酸基及び」「A又はB」の「及」「又」などは語の切れ目にする (後ろが平仮名なので漢字だけが語に付いてしまう)
    body = _CONJ_RE.sub(" ", body)
    for m in _TERM_RE.finditer(body):
        t = m.group(0)
        if t.isascii():
            if any(ch.isdigit() for ch in t) or "-" in t.strip("-") and japanese:
                continue
            if japanese:
                upper = sum(ch.isupper() for ch in t)
                if not (2 <= len(t) <= 8 and upper >= 2):
                    continue
            else:
                t = t.lower()
                if t in _STOP_EN or len(t) < 4:
                    continue
        else:
            for pre in _PREFIXES:
                if t.startswith(pre) and len(t) - len(pre) >= 2:
                    t = t[len(pre):]
                    break
            if len(t) > _MAX_JA or t in _STOP_JA or len(set(t)) == 1:
                continue
        counts[t] += 1
    return counts


def index_document(store: Store, document_id: int) -> int:
    """1 件の資料の語を数えて保存する (取り込みのたびに呼ぶ)."""
    texts = [p["text"] for p in store.paragraphs_of(document_id)]
    counts = extract_terms(texts)
    with store.tx() as c:
        c.execute("DELETE FROM doc_terms WHERE document_id = ?", (document_id,))
        c.executemany("INSERT INTO doc_terms(document_id, term, n) VALUES (?, ?, ?)", [(document_id, t, n) for t, n in counts.items()])
        c.execute("INSERT OR IGNORE INTO doc_terms_done(document_id) VALUES (?)", (document_id,))
    return len(counts)


def pending(store: Store) -> list[int]:
    return [int(r[0]) for r in store.conn.execute(
        "SELECT d.id FROM documents d WHERE NOT EXISTS (SELECT 1 FROM doc_terms_done x WHERE x.document_id = d.id) ORDER BY d.id")]


def backfill(store: Store, limit: int | None = None) -> int:
    """まだ語を数えていない資料を数える (前の版で取り込んだ資料や、語の数え方を変えたときのため)."""
    if store.meta("terms_version") != TERMS_VERSION:
        with store.tx() as c:
            c.execute("DELETE FROM doc_terms_done")
        store.set_meta("terms_version", TERMS_VERSION)
    ids = pending(store)
    if limit is not None:
        ids = ids[:limit]
    for i in ids:
        index_document(store, i)
    return len(ids)


def _documents_for_query(store: Store, query: str, limit: int = 400) -> list[int]:
    from .search import search

    queries = [q.strip() for q in re.split(r"[|｜]", query) if q.strip()]
    hits = search(store, queries, top_k=limit * 3)
    seen: list[int] = []
    for h in hits:
        if h["document_id"] not in seen:
            seen.append(h["document_id"])
        if len(seen) >= limit:
            break
    return seen


_KANJI_ONLY = re.compile(r"^[一-龥々]+$")


def _weight(term: str) -> float:
    if term.isascii():
        return 0.9 if term.isupper() else 0.5
    if _KANJI_ONLY.match(term) and len(term) <= 2:
        return 0.35
    return 0.9 if len(term) == 3 else 1.2


def graph(store: Store, limit: int = 80, query: str | None = None, edges_per_node: int = 4,
          documents: list[int] | None = None) -> dict[str, Any]:
    """よく出る語と、一緒に出る語どうしの辺.
    documents を渡すとその資料だけで (登録したフォルダの範囲など)、query を渡すとその問いに当たる資料だけで作る."""
    remaining = len(pending(store))
    doc_ids: list[int] | None = documents
    if query and query.strip():
        hits = _documents_for_query(store, query, limit=5000 if documents is not None else 400)
        doc_ids = [d for d in hits if documents is None or d in set(documents)][:400]
        if not doc_ids:
            return {"nodes": [], "edges": [], "documents": 0, "pending": remaining, "query": query}
    if doc_ids is not None and not doc_ids:
        return {"nodes": [], "edges": [], "documents": 0, "pending": remaining, "query": query}
    where, params = "", []
    if doc_ids is not None:
        where = f" WHERE document_id IN ({','.join('?' * len(doc_ids))})"
        params = list(doc_ids)
    n_docs = len(doc_ids) if doc_ids is not None else int(store.conn.execute("SELECT COUNT(*) FROM doc_terms_done").fetchone()[0])
    if n_docs == 0:
        return {"nodes": [], "edges": [], "documents": 0, "pending": remaining, "query": query}
    min_df = 2 if n_docs >= 4 else 1
    max_df = max(min_df, int(n_docs * 0.5)) if n_docs >= 10 else n_docs
    rows = store.conn.execute(
        f"SELECT term, COUNT(*) AS df, SUM(n) AS tf FROM doc_terms{where} GROUP BY term HAVING df >= ? AND df <= ?",
        [*params, min_df, max_df]).fetchall()
    # 多くの資料に出るが、どの資料にも出る語ではないものを上に (資料数 × idf)。
    # 2 文字の漢字語 (確認・判断など) と英語の小文字の語は一般語になりやすいので軽くし、複合語・カタカナ語を重くする
    scored = sorted(rows, key=lambda r: -(r["df"] * math.log(1 + n_docs / r["df"]) * _weight(r["term"]) * (1 + math.log(r["tf"] / r["df"]))))
    # 片方がもう片方を含む語 (「電池」と「蓄電池」) は、長いほうが少なくとも同じくらい出ていれば短いほうを外す
    chosen: list[Any] = []
    for r in scored:
        if any(r["term"] in c["term"] and c["df"] >= r["df"] * 0.8 for c in chosen):
            continue
        chosen.append(r)
        if len(chosen) >= limit:
            break
    terms = [r["term"] for r in chosen]
    if not terms:
        return {"nodes": [], "edges": [], "documents": n_docs, "pending": remaining, "query": query}
    docs_of: dict[str, set[int]] = {t: set() for t in terms}
    q = ",".join("?" * len(terms))
    for r in store.conn.execute(f"SELECT term, document_id FROM doc_terms WHERE term IN ({q})" + (f" AND document_id IN ({','.join('?' * len(doc_ids))})" if doc_ids else ""),
                                [*terms, *(doc_ids or [])]):
        docs_of[r["term"]].add(int(r["document_id"]))
    pairs: list[tuple[float, str, str, int]] = []
    for i, a in enumerate(terms):
        for b in terms[i + 1:]:
            both = len(docs_of[a] & docs_of[b])
            if both < min_df:
                continue
            jac = both / len(docs_of[a] | docs_of[b])
            pairs.append((jac, a, b, both))
    pairs.sort(reverse=True)
    degree: Counter = Counter()
    edges = []
    for jac, a, b, both in pairs:
        if degree[a] >= edges_per_node and degree[b] >= edges_per_node:
            continue
        if jac < 0.08:
            break
        degree[a] += 1
        degree[b] += 1
        edges.append({"source": f"k:{a}", "target": f"k:{b}", "kind": "共起", "weight": round(jac, 3), "documents": both, "relation": False})
    nodes = [{"id": f"k:{r['term']}", "label": r["term"], "kind": "keyword", "type": "キーワード", "color": "#0e7490",
              "size": 1 + r["df"] * 12 / max(1, chosen[0]["df"]), "df": r["df"]} for r in chosen]
    return {"nodes": nodes, "edges": edges, "documents": n_docs, "pending": remaining, "query": query}


def documents_with(store: Store, term: str, limit: int = 50) -> dict[str, Any]:
    rows = store.conn.execute(
        "SELECT d.id, d.title, t.n FROM doc_terms t JOIN documents d ON d.id = t.document_id WHERE t.term = ? ORDER BY t.n DESC, d.id LIMIT ?",
        (term, limit)).fetchall()
    total = int(store.conn.execute("SELECT COUNT(*) FROM doc_terms WHERE term = ?", (term,)).fetchone()[0])
    return {"term": term, "total": total, "documents": [dict(r) for r in rows]}
