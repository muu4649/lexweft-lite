"""MCP サーバー: 利用者の LLM (Claude Desktop など) から、資料を読み、意味層をたどって探し、意味層を書く.

起動: lexweft mcp  (stdio)
資料と意味層は、登録したフォルダごとに分かれている。scope は登録したフォルダのパス (lw_scopes で分かる)、
または "central" (フォルダの外の資料)。省略すると、すべてのフォルダをまとめて扱う。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import clusters as cl
from . import concepts as co
from . import keywords as kw
from . import navigate as nav
from . import runtime
from .ingest import ingest, ingest_text
from .library import CENTRAL
from .markdown import document_markdown
from .search import search_all

INSTRUCTIONS = (
    "LeXWeft Lite は、利用者が登録したフォルダの資料 (段落に番号 [¶n] が付いた Markdown) と、フォルダごとの意味層を持つローカルの知識ベースです。"
    "意味層には、取り込み時に自動で作る部分 (資料のまとまり、キーワードのつながり、ベクトル) と、"
    "あなたが書く部分 (型ごとの概念、別名、根拠の段落、概念どうしの関係) があります。意味層はフォルダごとに分かれています。"
    "資料について調べるときは、意味層をたどります: lw_map で全体を見る → lw_route(質問) で関係するまとまり・資料・段落へ → "
    "lw_read_document で読む → 必要なら lw_expand で近くの資料へ → 答える前に lw_unread で読み残しを確かめる。"
    "lw_map にフォルダの中身が変わったと出たら、lw_refresh で取り込み直してから調べます。"
    "答えには根拠の段落番号 [¶n] と、どのフォルダの資料かを付けます。"
    "意味層を書くときは必ず根拠の段落番号を付け、資料に書かれていないことは書かないでください。概念は根拠の段落があるフォルダに入ります。"
    "既定の型は「課題」と「解決手段」です。足す前に lw_list_concepts で同じ意味の概念が無いか確かめます。"
)

server = MCPServer("lexweft-lite", instructions=INSTRUCTIONS)

MAX_CHARS = 20000


def _lib():
    return runtime.library()


def _cap(text: str, max_chars: int = MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n<!-- ここで切りました (全 {len(text)} 文字)。offset と limit で範囲を絞って呼び直してください -->"


def _default_scope(scope: str | None) -> str:
    """フォルダを指定しないときの範囲: 登録したフォルダが 1 つならそれ、無ければフォルダの外の資料."""
    if scope:
        return scope
    rows = _lib().sources()
    return rows[0]["path"] if len(rows) == 1 else (CENTRAL if not rows else "")


# ---------------- 全体と地図 ----------------
@server.tool()
def lw_overview() -> dict[str, Any]:
    """全体の件数、フォルダの一覧、型、関係の種類の例、まだ課題と解決手段が書かれていない資料を返す."""
    lib = _lib()
    total: dict[str, int] = {}
    for st in lib.stores():
        for k, v in st.stats().items():
            total[k] = total.get(k, 0) + v
    return {"stats": total, "folders": lw_scopes(), "types": co.list_types(lib), "relation_kinds": ["解決する", "引き起こす", "一部である", "関連する"],
            "documents_without_concepts": co.documents_without_concepts(lib, limit=10)}


@server.tool()
def lw_scopes() -> list[dict[str, Any]]:
    """登録したフォルダ (と、フォルダの外の資料) の一覧. scope に渡す値、資料数、意味層の版、保存先 (フォルダの中かアプリ側か)."""
    lib = _lib()
    out = []
    for row in lib.sources():
        st = lib.store_for_folder(row["path"])
        s = cl.status(st)
        out.append({"scope": row["path"], "folder": st.label, "documents": s["documents_now"], "clusters": s["clusters"],
                    "layer_version": s["version"], "built_at": s["built_at"], "stored_in": "フォルダの中 (_LeXWeft)" if row["location"] == "folder" else "アプリ側",
                    "reason": row["reason"]})
    if lib.catalog.count_documents():
        s = cl.status(lib.catalog)
        out.append({"scope": CENTRAL, "folder": "フォルダの外の資料", "documents": s["documents_now"], "clusters": s["clusters"],
                    "layer_version": s["version"], "built_at": s["built_at"], "stored_in": "アプリ側", "reason": ""})
    return out


@server.tool()
def lw_map(scope: str | None = None) -> dict[str, Any]:
    """意味層の地図: フォルダごとのまとまり (名前・資料数・よく出る語)、近いまとまりの組、意味層の版と前回からの変化、
    フォルダの中身が変わっていないか. 調べものの最初に呼ぶ."""
    return nav.overview(_lib(), scope)


@server.tool()
def lw_group_links(scope: str, group_a: str | None = None, group_b: str | None = None) -> dict[str, Any]:
    """フォルダの中の 2 つのグループ (サブフォルダ) の関わり: 片方のまとまりと、もう片方のまとまりを結ぶ線、
    その線のもとになった近い資料の組 (document_id・題名・近さ・共通の語). グループを省略すると資料の多い 2 つ.
    近さはベクトルで決めているので、関係があるかは lw_read_document で両方を読んで確かめる."""
    from . import groups as gr

    return gr.links(_lib().for_scope(scope), group_a, group_b)


@server.tool()
def lw_refresh(scope: str | None = None) -> dict[str, Any]:
    """登録したフォルダを読み直し、新しいファイル・変わったファイル・消えたファイルを反映して、意味層を作り直す.
    scope を省略すると、中身が変わったフォルダすべて. 終わるまで待つ."""
    import time

    from . import jobs

    targets = [scope] if scope and scope != CENTRAL else [r["path"] for r in jobs.changed_folders()]
    if not targets:
        return {"refreshed": [], "message": "変わったフォルダはありません"}
    job = jobs.start(targets)
    while job.state == "running":
        time.sleep(0.3)
    lib = _lib()
    for t in targets:   # 意味層ができるまで待つ (最大 3 分)
        st = lib.store_for_folder(t)
        for _ in range(600):
            s = cl.status(st)
            if not s["building"] and not s["stale"]:
                break
            if not s["building"]:
                cl.build_in_background(st)
            time.sleep(0.3)
    return {"refreshed": targets, "counts": job.view()["counts"], "errors": job.view()["errors"],
            "layers": [{"scope": t, "layer_version": cl.status(lib.store_for_folder(t))["version"],
                        "changes": cl.status(lib.store_for_folder(t))["changes"]} for t in targets]}


# ---------------- 探す ----------------
@server.tool()
def lw_route(question: str, scope: str | None = None, max_documents: int = 8) -> dict[str, Any]:
    """質問から、意味層をたどって関係するまとまり → 資料 → 段落を順位つきで返す (意味の近さと文字の一致の両方で探す).
    question は自然な文でよい。言い換えを | で並べるとさらに取りこぼしが減る (例: "電池の延焼を防ぐ方法 | 熱暴走 | 熱伝播").
    scope を省略すると、すべてのフォルダをまとめて探す. 呼ぶたびに新しい調べものとして、読んだ資料の記録を始め直す."""
    return nav.route(_lib(), question, scope, max_documents)


@server.tool()
def lw_expand(document_ids: list[int], question: str | None = None) -> dict[str, Any]:
    """読んだ資料の近くをたどる: 同じフォルダの似た資料・同じまとまりの資料を、理由つきで返す (読んだ資料は除く)."""
    return nav.expand(_lib(), document_ids, question)


@server.tool()
def lw_unread(question: str | None = None, read_document_ids: list[int] | None = None, scope: str | None = None) -> dict[str, Any]:
    """読み残しの確認: 質問に関係が強いのに、まだ読んでいない資料を理由つきで返す. 答える前に必ず呼ぶ.
    読んだ資料は lw_read_document で開いたものを自動で数える (read_document_ids で足せる)."""
    return nav.unread(_lib(), question, read_document_ids, scope=scope)


@server.tool()
def lw_search(queries: list[str], top_k: int = 10, scope: str | None = None) -> list[dict[str, Any]]:
    """段落を文字の一致で探す. queries には同じ意味の言い換え・別表記・英語表記を複数入れる (例: ["二次電池", "蓄電池", "battery"]).
    意味の近さでも探したいときは lw_route を使う."""
    hits = search_all(_lib(), queries, top_k=top_k, scope=scope)
    for h in hits:
        h["text"] = h["text"][:600]
    return hits


@server.tool()
def lw_list_documents(limit: int = 100, scope: str | None = None) -> list[dict[str, Any]]:
    """資料の一覧 (番号、題名、フォルダ、段落数、結ばれた概念の数)."""
    lib = _lib()
    out = []
    for st in ([lib.for_scope(scope)] if scope else lib.stores()):
        out += [{**d, "scope": st.key, "folder": st.label} for d in st.list_documents(limit=limit)]
    return out[:limit]


@server.tool()
def lw_read_document(document_id: int, offset: int = 0, limit: int = 60) -> str:
    """資料を段落番号 [¶n] 付きの Markdown で読む. 長い資料は offset (先頭からの段落数) と limit で区切って読む."""
    nav.mark_read(document_id)
    return _cap(document_markdown(_lib().for_id(document_id), document_id, offset, limit))


@server.tool()
def lw_clusters(scope: str | None = None) -> dict[str, Any]:
    """フォルダの資料のまとまり (自動で作ったクラスター) の一覧. 各まとまりの説明の語、資料数."""
    st = _lib().for_scope(_default_scope(scope) or None)
    return {"status": cl.status(st), "clusters": [{"id": c["id"], "label": c["label"], "size": c["size"], "terms": [t for t, _ in c["terms"]]}
                                                  for c in cl.clusters(st)]}


@server.tool()
def lw_cluster(cluster_id: int, scope: str | None = None) -> dict[str, Any]:
    """まとまりの詳細: 説明の語、中心に近い資料、代表の段落. scope はまとまりのあるフォルダ."""
    return cl.cluster_detail(_lib().for_scope(_default_scope(scope) or None), cluster_id, limit=40)


@server.tool()
def lw_similar_documents(document_id: int) -> list[dict[str, Any]]:
    """同じフォルダの中で、ベクトルの近い資料."""
    return cl.similar_documents(_lib().for_id(document_id), document_id)


@server.tool()
def lw_keywords(query: str | None = None, limit: int = 60, scope: str | None = None) -> dict[str, Any]:
    """フォルダの資料によく出る語と、一緒に出る語の組 (自動で数えたもの). query を渡すと、その問いに当たる資料だけで数える."""
    st = _lib().for_scope(_default_scope(scope) or None)
    g = kw.graph(st, limit=limit, query=query)
    return {"folder": st.label, "documents": g["documents"], "keywords": [{"term": n["label"], "documents": n["df"]} for n in g["nodes"]],
            "pairs": [{"a": e["source"][2:], "b": e["target"][2:], "documents": e["documents"]} for e in g["edges"]]}


# ---------------- 取り込む ----------------
@server.tool()
def lw_ingest(path_or_url: str) -> list[dict[str, Any]]:
    """ファイル・URL を資料として取り込む (登録したフォルダの中のファイルは、そのフォルダの資料になる)."""
    lib = _lib()
    st = lib.catalog if path_or_url.startswith(("http://", "https://")) else lib.for_source(path_or_url)
    return [r.__dict__ for r in ingest(st, path_or_url, st.markdown_dir)]


@server.tool()
def lw_add_text(title: str, text: str, group: str | None = None) -> dict[str, Any]:
    """文章を 1 件の資料として保存する (会話でまとめたメモなど).
    資料を入れるフォルダが決まっていれば、そこ (group を指定するとその中のサブフォルダ) に Markdown のファイルとして保存して取り込む.
    決まっていなければ、フォルダの外の資料になる."""
    from . import inbox as ib
    from . import jobs

    lib = _lib()
    if ib.path(lib):
        dest = ib.save_text(lib, title, text, group)
        try:
            job = jobs.start(ib.path(lib) or "")
            while job.state == "running":
                import time

                time.sleep(0.2)
        except ValueError:
            return {"title": title, "status": "saved", "path": str(dest), "note": "ほかの取り込みが進んでいるので、あとで自動で取り込みます"}
        return {"title": title, "status": "saved", "path": str(dest), "imported": job.view().get("counts")}
    return ingest_text(runtime.store(), title, text, markdown_dir=runtime.markdown_dir()).__dict__


# ---------------- 課題と解決手段を書く ----------------
@server.tool()
def lw_list_types() -> list[dict[str, Any]]:
    """意味層の型 (課題、解決手段、利用者が足した型) と概念の数."""
    return co.list_types(_lib())


@server.tool()
def lw_add_type(name: str, description: str = "") -> dict[str, Any]:
    """型を足す (例: 「効果」「材料」「評価指標」). 利用者が求めたときだけ足す."""
    return co.add_type(_lib(), name, description)


@server.tool()
def lw_list_concepts(type: str | None = None, query: str | None = None, scope: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """概念の一覧 (どのフォルダの概念かも返す). type で型を、query で名前・別名・説明の一部を、scope でフォルダを絞る."""
    return co.list_concepts(_lib(), scope, type_=type, query=query, limit=limit)


@server.tool()
def lw_get_concept(name_or_id: str, scope: str | None = None) -> dict[str, Any]:
    """概念の詳細 (別名、説明、根拠の段落、関係). 同じ名前が複数のフォルダにあるときは scope でフォルダを指定する."""
    return co.get_concept(_lib(), name_or_id, scope)


@server.tool()
def lw_write_concept(name: str, type: str, paragraph_ids: list[int], description: str = "", aliases: list[str] | None = None,
                     note: str = "") -> dict[str, Any]:
    """概念を書く. 根拠の段落 (paragraph_ids、必須) があるフォルダに入る. 同じフォルダに同じ名前・別名の概念があれば、それに足す."""
    return co.write(_lib(), name, type, paragraph_ids, description or None, aliases or [], note)


@server.tool()
def lw_add_evidence(name_or_id: str, paragraph_ids: list[int], note: str = "") -> dict[str, Any]:
    """既にある概念に根拠の段落を足す (同じフォルダの段落)."""
    return co.add_evidence(_lib(), name_or_id, paragraph_ids, note)


@server.tool()
def lw_relate(source: str, target: str, kind: str = "解決する", paragraph_id: int | None = None, note: str = "",
              scope: str | None = None) -> dict[str, Any]:
    """概念どうしを結ぶ (同じフォルダの概念どうし). 例: source=解決手段, target=課題, kind=解決する. 根拠の段落番号を付ける."""
    return co.relate(_lib(), source, target, kind, paragraph_id, note, scope)


@server.tool()
def lw_merge_concepts(keep: str, drop: str) -> dict[str, Any]:
    """同じ意味の概念をまとめる (同じフォルダの概念どうし). drop の名前は keep の別名になり、根拠と関係は keep に移る."""
    return co.merge(_lib(), keep, drop)


@server.tool()
def lw_update_concept(name_or_id: str, name: str | None = None, type: str | None = None, description: str | None = None) -> dict[str, Any]:
    """概念の名前・型・説明を直す."""
    return co.update(_lib(), name_or_id, name, type, description)


@server.tool()
def lw_remove_evidence(name_or_id: str, paragraph_id: int) -> bool:
    """概念から根拠の段落を外す (誤って結んだとき)."""
    return co.remove_evidence(_lib(), name_or_id, paragraph_id)


@server.tool()
def lw_delete_relation(relation_id: int) -> bool:
    """関係を消す (lw_get_concept の relations[].id)."""
    return co.delete_relation(_lib(), relation_id)


@server.tool()
def lw_delete_concept(name_or_id: str) -> bool:
    """概念を消す (根拠と関係も消える). 利用者が求めたときだけ使う."""
    return co.delete(_lib(), name_or_id)


@server.tool()
def lw_export_layer(scope: str | None = None) -> str:
    """課題と解決手段の意味層を Markdown で返す (フォルダごと. scope で絞れる)."""
    return _cap(co.layer_markdown(_lib(), scope), 60000)


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
