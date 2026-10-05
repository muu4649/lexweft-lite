"""MCP サーバー: 利用者の LLM (Claude Desktop など) から、資料を読み、意味層を書き、探す.

起動: lexweft mcp  (stdio)
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import clusters as cl
from . import keywords as kw
from . import layer as ly
from . import navigate as nav
from . import runtime
from .ingest import ingest, ingest_text
from .markdown import document_markdown, layer_markdown
from .search import search as _search

INSTRUCTIONS = (
    "LeXWeft Lite は、利用者が入れた資料 (段落に番号 [¶n] が付いた Markdown) と、その資料から作る意味層を持つローカルの知識ベースです。"
    "意味層には、取り込み時に自動で作る部分 (資料のまとまり lw_clusters、キーワードのつながり lw_keywords) と、"
    "あなたが書く部分 (型ごとの概念、別名、根拠の段落、概念どうしの関係) があります。"
    "意味層はあなた (LLM) が書きます。既定の型は「課題」と「解決手段」で、必要なら型を足せます。"
    "書くときは必ず根拠の段落番号を付け、資料に書かれていないことは書かないでください。"
    "概念を足す前に lw_list_concepts で同じ意味の概念が無いか確かめ、あれば同じ名前を使うか別名として足します。"
    "資料について調べるときは、意味層をたどります: lw_map で全体を見る → lw_route(質問) で関係するまとまり・資料・段落へ → "
    "lw_read_document で読む → 必要なら lw_expand で近くの資料へ → 答える前に lw_unread で読み残しを確かめる。"
    "答えには根拠の段落番号 [¶n] を付けます。"
)

server = MCPServer("lexweft-lite", instructions=INSTRUCTIONS)

MAX_CHARS = 20000


def _cap(text: str, max_chars: int = MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n<!-- ここで切りました (全 {len(text)} 文字)。offset と limit で範囲を絞って呼び直してください -->"


@server.tool()
def lw_overview() -> dict[str, Any]:
    """全体の件数、型、関係の種類の例、まだ意味層に書かれていない資料を返す. 最初に呼ぶ."""
    s = runtime.store()
    return {"stats": s.stats(), "types": ly.list_types(s), "relation_kinds": list(ly.RELATION_KINDS),
            "documents_without_concepts": ly.documents_without_concepts(s, limit=10)}


@server.tool()
def lw_list_documents(limit: int = 100) -> list[dict[str, Any]]:
    """資料の一覧 (ID、題名、段落数、結ばれた概念の数)."""
    return runtime.store().list_documents()[:limit]


@server.tool()
def lw_read_document(document_id: int, offset: int = 0, limit: int = 60) -> str:
    """資料を段落番号 [¶n] 付きの Markdown で読む. 長い資料は offset (先頭からの段落数) と limit で区切って読む."""
    nav.mark_read(document_id)
    return _cap(document_markdown(runtime.store(), document_id, offset, limit))


@server.tool()
def lw_map(scope: str | None = None) -> dict[str, Any]:
    """意味層の地図: 範囲 (登録したフォルダ) ごとのまとまりの名前・資料数・よく出る語と、近いまとまりどうしの組. 調べものの最初に呼ぶ."""
    return nav.overview(runtime.store(), scope)


@server.tool()
def lw_route(question: str, scope: str | None = None, max_documents: int = 8) -> dict[str, Any]:
    """質問から、意味層をたどって関係するまとまり → 資料 → 段落を順位つきで返す (意味の近さと文字の一致の両方で探す).
    question は自然な文でよい。言い換えを | で並べるとさらに取りこぼしが減る (例: "電池の延焼を防ぐ方法 | 熱暴走 | 熱伝播").
    呼ぶたびに新しい調べものとして、読んだ資料の記録を始め直す."""
    return nav.route(runtime.store(), question, scope, max_documents)


@server.tool()
def lw_expand(document_ids: list[int], question: str | None = None) -> dict[str, Any]:
    """読んだ資料の近くをたどる: 似た資料・同じまとまりの資料を、理由つきで返す (読んだ資料は除く)."""
    return nav.expand(runtime.store(), document_ids, question)


@server.tool()
def lw_unread(question: str | None = None, read_document_ids: list[int] | None = None) -> dict[str, Any]:
    """読み残しの確認: 質問に関係が強いのに、まだ読んでいない資料を理由つきで返す. 答える前に必ず呼ぶ.
    読んだ資料は lw_read_document で開いたものを自動で数える (read_document_ids で足せる)."""
    return nav.unread(runtime.store(), question, read_document_ids)


@server.tool()
def lw_search(queries: list[str], top_k: int = 10, document_id: int | None = None) -> list[dict[str, Any]]:
    """段落を全文検索する. queries には同じ意味の言い換え・別表記・英語表記を複数入れる (例: ["二次電池", "蓄電池", "battery"]).
    1 つの問いの中で空白で区切った語は、すべて含む段落を探す."""
    hits = _search(runtime.store(), queries, top_k=top_k, document_id=document_id)
    for h in hits:
        h["text"] = h["text"][:600]
    return hits


@server.tool()
def lw_ingest(path_or_url: str) -> list[dict[str, Any]]:
    """ファイル・フォルダ・URL を資料として取り込む (md / txt / html / pdf / docx / csv)."""
    return [r.__dict__ for r in ingest(runtime.store(), path_or_url, runtime.markdown_dir())]


@server.tool()
def lw_add_text(title: str, text: str) -> dict[str, Any]:
    """文章を 1 件の資料として保存する (会話でまとめたメモなど)."""
    return ingest_text(runtime.store(), title, text, markdown_dir=runtime.markdown_dir()).__dict__


@server.tool()
def lw_keywords(query: str | None = None, limit: int = 60) -> dict[str, Any]:
    """資料によく出る語と、一緒に出る語の組を返す (自動で数えたもの). query を渡すと、その問いに当たる資料だけで数える.
    資料が多いとき、どのテーマから意味層を書くか決めるのに使う."""
    g = kw.graph(runtime.store(), limit=limit, query=query)
    return {"documents": g["documents"], "keywords": [{"term": n["label"], "documents": n["df"]} for n in g["nodes"]],
            "pairs": [{"a": e["source"][2:], "b": e["target"][2:], "documents": e["documents"]} for e in g["edges"]]}


@server.tool()
def lw_scopes() -> list[dict[str, Any]]:
    """意味層の範囲 (登録したフォルダと、すべての資料) の一覧. lw_clusters に scope として渡す."""
    return cl.scopes(runtime.store())


@server.tool()
def lw_clusters(scope: str | None = None) -> dict[str, Any]:
    """資料のまとまり (自動で作ったクラスター) の一覧. 各まとまりの説明の語、資料数を返す. 全体像をつかむときに最初に呼ぶ.
    scope は登録したフォルダのパス (lw_scopes で分かる)。省略すると最初に登録したフォルダ (無ければすべての資料)."""
    s = runtime.store()
    if not scope:
        srcs = s.list_sources()
        scope = srcs[0]["path"] if srcs else cl.ALL
    return {"status": cl.status(s, scope), "clusters": [{"id": c["id"], "label": c["label"], "size": c["size"], "terms": [t for t, _ in c["terms"]]}
                                                        for c in cl.clusters(s, scope)]}


@server.tool()
def lw_cluster(cluster_id: int, scope: str | None = None) -> dict[str, Any]:
    """まとまりの詳細: 説明の語、中心に近い資料、代表の段落. scope は lw_clusters と同じ."""
    s = runtime.store()
    if not scope:
        srcs = s.list_sources()
        scope = srcs[0]["path"] if srcs else cl.ALL
    return cl.cluster_detail(s, cluster_id, scope, limit=40)


@server.tool()
def lw_similar_documents(document_id: int) -> list[dict[str, Any]]:
    """同じフォルダの中で、ベクトルの近い資料."""
    return cl.similar_documents(runtime.store(), document_id)


@server.tool()
def lw_list_types() -> list[dict[str, Any]]:
    """意味層の型 (課題、解決手段、利用者が足した型) と概念の数."""
    return ly.list_types(runtime.store())


@server.tool()
def lw_add_type(name: str, description: str = "") -> dict[str, Any]:
    """型を足す (例: 「効果」「材料」「評価指標」). 利用者が求めたときだけ足す."""
    return ly.add_type(runtime.store(), name, description)


@server.tool()
def lw_list_concepts(type: str | None = None, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """概念の一覧. type で型を、query で名前・別名・説明の一部を絞る."""
    return ly.list_concepts(runtime.store(), type_=type, query=query, limit=limit)


@server.tool()
def lw_get_concept(name_or_id: str) -> dict[str, Any]:
    """概念の詳細 (別名、説明、根拠の段落、関係)."""
    return ly.get_concept(runtime.store(), name_or_id)


@server.tool()
def lw_write_concept(name: str, type: str, paragraph_ids: list[int], description: str = "", aliases: list[str] | None = None,
                     note: str = "") -> dict[str, Any]:
    """概念を書く. 同じ名前・別名の概念があればそれに足す. paragraph_ids は概念が書かれている段落の番号 (必須)."""
    s = runtime.store()
    if not paragraph_ids:
        raise ValueError("根拠の段落番号 paragraph_ids を 1 つ以上付けてください")
    out = ly.upsert_concept(s, name, type, description or None, aliases or [])
    out["evidence"] = ly.add_evidence(s, out["id"], paragraph_ids, note)
    return out


@server.tool()
def lw_add_evidence(name_or_id: str, paragraph_ids: list[int], note: str = "") -> dict[str, Any]:
    """既にある概念に根拠の段落を足す."""
    return ly.add_evidence(runtime.store(), name_or_id, paragraph_ids, note)


@server.tool()
def lw_relate(source: str, target: str, kind: str = "解決する", paragraph_id: int | None = None, note: str = "") -> dict[str, Any]:
    """概念どうしを結ぶ. 例: source=解決手段, target=課題, kind=解決する. 根拠の段落番号を付ける."""
    return ly.relate(runtime.store(), source, target, kind, paragraph_id, note)


@server.tool()
def lw_merge_concepts(keep: str, drop: str) -> dict[str, Any]:
    """同じ意味の概念をまとめる. drop の名前は keep の別名になり、根拠と関係は keep に移る."""
    return ly.merge_concepts(runtime.store(), keep, drop)


@server.tool()
def lw_update_concept(name_or_id: str, name: str | None = None, type: str | None = None, description: str | None = None) -> dict[str, Any]:
    """概念の名前・型・説明を直す."""
    return ly.update_concept(runtime.store(), name_or_id, name, type, description)


@server.tool()
def lw_remove_evidence(name_or_id: str, paragraph_id: int) -> bool:
    """概念から根拠の段落を外す (誤って結んだとき)."""
    return ly.remove_evidence(runtime.store(), name_or_id, paragraph_id)


@server.tool()
def lw_delete_relation(relation_id: int) -> bool:
    """関係を消す (lw_get_concept の relations[].id)."""
    return ly.delete_relation(runtime.store(), relation_id)


@server.tool()
def lw_delete_concept(name_or_id: str) -> bool:
    """概念を消す (根拠と関係も消える). 利用者が求めたときだけ使う."""
    return ly.delete_concept(runtime.store(), name_or_id)


@server.tool()
def lw_export_layer() -> str:
    """意味層全体を 1 枚の Markdown で返す (型ごとの概念、別名、根拠、関係)."""
    return _cap(layer_markdown(runtime.store()), 60000)


@server.prompt()
def build_layer(document_id: str = "") -> str:
    """資料を読んで、課題と解決手段の意味層を書く手順."""
    target = f"資料 {document_id}" if document_id else "lw_overview の documents_without_concepts にある資料 (上から順に)"
    return (
        f"{target} を対象に、LeXWeft Lite の意味層を書いてください。\n"
        "1. lw_overview で型と既存の件数を確かめる。\n"
        "2. lw_read_document で資料を読む (長ければ offset と limit で区切る)。\n"
        "3. 資料が述べている「課題」と「解決手段」を取り出す。名前は短い名詞句にし、資料に書かれていることだけを使う。\n"
        "4. 足す前に lw_list_concepts(query=...) で同じ意味の概念を探し、あれば同じ名前を使う。言い換えは aliases に入れる。\n"
        "5. lw_write_concept で、根拠の段落番号 (paragraph_ids) を付けて書く。\n"
        "6. 解決手段が課題を解くと資料に書かれていれば、lw_relate(source=解決手段, target=課題, kind=解決する, paragraph_id=根拠) で結ぶ。\n"
        "7. 終わったら、書いた概念と関係の数、迷った点を短く報告する。"
    )


@server.prompt()
def investigate(question: str) -> str:
    """意味層をたどって、関係する資料を読み残しなく調べて答える手順."""
    return (
        f"LeXWeft Lite の資料で「{question}」を調べて答えてください。意味層をたどり、関係があるのに読まれない資料が残らないようにします。\n"
        "1. lw_map で、どんなまとまりがあるかを見る。\n"
        f"2. lw_route(question=\"{question} | <言い換え> | <別の表記>\") で、関係するまとまり・資料・段落を見る。言い換えは 2〜4 個入れる。\n"
        "3. 関係の強い資料を lw_read_document で読む (長ければ関係する段落の前後だけ)。\n"
        "4. 読んだ資料の近くも関係しそうなら lw_expand(document_ids=[...]) でたどる。\n"
        "5. 答える前に lw_unread を呼び、出てきた資料の check_paragraph を確かめる。関係があれば読み、なければ外した理由を短く書く。\n"
        "6. 根拠の段落番号 [¶n] を付けて答える。最後に、読んだ資料の数と、読み残しを確かめた結果を 1 行で添える。"
    )


@server.prompt()
def build_layer_for_topic(topic: str) -> str:
    """資料が多いときに、1 つのテーマに絞って課題と解決手段の意味層を書く手順."""
    return (
        f"テーマ「{topic}」について、LeXWeft Lite の意味層を書いてください。資料が多いので、全部は読まずにテーマに関わる段落だけを使います。\n"
        f"1. lw_keywords(query=\"{topic}\") で、このテーマの資料によく出る語を見る。\n"
        "2. その語や言い換えを並べて lw_search(queries=[...], top_k=30) で段落を集める。\n"
        "3. 段落から「課題」と「解決手段」を取り出す。名前は短い名詞句にし、段落に書かれていることだけを使う。\n"
        "4. 足す前に lw_list_concepts(query=...) で同じ意味の概念を探し、あれば同じ名前を使う。言い換えは aliases に入れる。\n"
        "5. lw_write_concept で、根拠の段落番号 (paragraph_ids) を付けて書く。\n"
        "6. 解決手段が課題を解くと段落に書かれていれば、lw_relate(source=解決手段, target=課題, kind=解決する, paragraph_id=根拠) で結ぶ。\n"
        "7. 終わったら、書いた概念と関係の数、足りないと感じた資料や観点を短く報告する。"
    )


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
