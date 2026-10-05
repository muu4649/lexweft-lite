from __future__ import annotations

import asyncio

from lexweft_lite import clusters as cl
from lexweft_lite import navigate as nav
from lexweft_lite import runtime
from lexweft_lite.ingest import ingest
from lexweft_lite.search import search

BATTERY = ["セルの熱暴走を相変化材料で抑える。隣接セルの温度上昇を吸熱で遅らせる。",
           "セルの延焼を潜熱蓄熱材で防ぐ。隣接セルの温度上昇を吸熱で遅らせる。",
           "セルの類焼を断熱材で防ぐ。隣接セルへの異常発熱の広がりを抑える。",
           "モジュールの熱暴走を耐熱シートで遅らせる。セル間の温度上昇を測った。",
           "パックの延焼対策として、セル間に遮熱材を入れる。異常発熱の吸熱を確かめた。",
           "隣接セルの類焼を相変化材料で抑えた。セル間の温度上昇が下がった。"]
ROBOT = ["ロボットハンドの把持力を触覚センサで調整する。指先の滑りを検出する。",
         "グリッパのつかむ動作を力覚センサで調整する。物体の滑りを検出する。",
         "エンドエフェクタのピッキングを強化学習で学ぶ。物体の形状を推定する。",
         "ロボットハンドで柔らかい物体を把持する。指先の接触センサを使う。",
         "グリッパの成功率を物流倉庫で測った。物体の形状とずれを調べた。",
         "エンドエフェクタの指先に触覚センサを付け、スリップを防ぐ。"]


def _setup(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    for name, texts in (("b", BATTERY), ("r", ROBOT)):
        for i, t in enumerate(texts):
            (d / f"{name}{i}.md").write_text(f"# {name}{i}\n\n{t}\n", encoding="utf-8")
    s = runtime.store()
    scope = s.add_source(str(d))
    ingest(s, str(d))
    cl.build(s, scope)
    return s, scope, {doc["title"]: doc["id"] for doc in s.list_documents()}


def test_route_reaches_documents_without_the_query_word(home, tmp_path):
    s, scope, ids = _setup(tmp_path)
    battery = {ids[f"b{i}"] for i in range(6)}
    by_word = {h["document_id"] for h in search(s, ["熱暴走"], top_k=10)}
    assert by_word == {ids["b0"], ids["b3"]}                      # 文字の一致では 2 件だけ
    r = nav.route(s, "熱暴走を防ぐ方法", max_documents=6)
    got = {d["document_id"] for res in r["results"] for d in res["documents"]}
    assert len(got & battery) >= 4 and not (got - battery)        # 言い方の違う資料にも届き、関係のない資料は出ない
    assert r["results"][0]["documents"][0]["paragraphs"][0]["why"]


def test_unread_and_expand(home, tmp_path):
    s, scope, ids = _setup(tmp_path)
    battery = {ids[f"b{i}"] for i in range(6)}
    nav.route(s, "熱暴走を防ぐ方法", max_documents=2)
    nav.mark_read(ids["b0"])
    u = nav.unread(s, limit=10)
    left = [x["document_id"] for res in u["results"] for x in res["unread"]]
    assert ids["b0"] not in left and set(left[:4]) <= battery
    assert all(x["why"] and x["check_paragraph"]["paragraph_id"] for res in u["results"] for x in res["unread"])
    e = nav.expand(s, [ids["b0"]], "熱暴走を防ぐ方法", limit=4)
    near = [x["document_id"] for res in e["results"] for x in res["documents"]]
    assert near and ids["b0"] not in near and set(near) <= battery


def test_overview_and_mcp_tools(home, tmp_path):
    s, scope, ids = _setup(tmp_path)
    ov = nav.overview(s)
    assert ov["scopes"][0]["scope"] == scope and ov["scopes"][0]["clusters"]
    from lexweft_lite.mcp_server import server

    async def names():
        return {t.name for t in await server.list_tools()}, {p.name for p in await server.list_prompts()}

    tools, prompts = asyncio.run(names())
    assert {"lw_map", "lw_route", "lw_expand", "lw_unread"} <= tools and "investigate" in prompts
    from lexweft_lite import mcp_server as m

    m.lw_route("ロボットハンドの把持")
    m.lw_read_document(ids["r0"])
    left = [x["document_id"] for res in m.lw_unread()["results"] for x in res["unread"]]
    assert ids["r0"] not in left


def test_route_api(home, tmp_path):
    from fastapi.testclient import TestClient

    from lexweft_lite.web import app

    s, scope, ids = _setup(tmp_path)
    r = TestClient(app).get("/api/route", params={"q": "熱暴走を防ぐ方法", "max_documents": 2}).json()
    docs = [d for res in r["route"]["results"] for d in res["documents"]]
    left = [d for res in r["unread"]["results"] for d in res["unread"]]
    assert len(docs) == 2 and docs[0]["cluster_color"].startswith("#")
    assert left and not ({d["document_id"] for d in docs} & {d["document_id"] for d in left})   # 一覧に出た資料は読み残しに出さない
