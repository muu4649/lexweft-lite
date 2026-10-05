from __future__ import annotations

import asyncio

from conftest import make_folder, register_and_import

from lexweft_lite import clusters as cl
from lexweft_lite import navigate as nav
from lexweft_lite import runtime
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


def _one_folder(tmp_path):
    st = register_and_import(make_folder(tmp_path, "notes", [f"[b] {t}" for t in BATTERY] + [f"[r] {t}" for t in ROBOT]))
    ids = {}
    for d in st.list_documents():
        text = st.paragraphs_of(d["id"])[0]["text"]
        ids[("b" if "[b]" in text else "r") + str(int(d["title"].split()[-1]) % 6)] = d["id"]
    return st, ids


def test_route_reaches_documents_without_the_query_word(home, tmp_path):
    st, ids = _one_folder(tmp_path)
    battery = {v for k, v in ids.items() if k.startswith("b")}
    by_word = {h["document_id"] for h in search(st, ["熱暴走"], top_k=10)}
    assert len(by_word) == 2                                           # 文字の一致では 2 件だけ
    r = nav.route(runtime.library(), "熱暴走を防ぐ方法", max_documents=6)
    got = {d["document_id"] for d in r["documents"]}
    assert len(got & battery) >= 4 and not (got - battery)            # 言い方の違う資料にも届き、関係のない資料は出ない
    assert r["documents"][0]["paragraphs"][0]["why"] and r["documents"][0]["folder"] == st.label


def test_unread_and_expand(home, tmp_path):
    st, ids = _one_folder(tmp_path)
    lib = runtime.library()
    battery = {v for k, v in ids.items() if k.startswith("b")}
    first = sorted(battery)[0]
    nav.route(lib, "熱暴走を防ぐ方法", max_documents=2)
    nav.mark_read(first)
    u = nav.unread(lib, limit=10)
    left = [x["document_id"] for x in u["unread"]]
    assert first not in left and set(left[:4]) <= battery
    assert all(x["why"] and x["check_paragraph"]["paragraph_id"] for x in u["unread"])
    e = nav.expand(lib, [first], "熱暴走を防ぐ方法", limit=4)
    near = [x["document_id"] for x in e["documents"]]
    assert near and first not in near and set(near) <= battery


def test_route_across_folders(home, tmp_path):
    a = register_and_import(make_folder(tmp_path, "電池", BATTERY))
    b = register_and_import(make_folder(tmp_path, "ロボット", ROBOT))
    lib = runtime.library()
    r = nav.route(lib, "熱暴走を防ぐ方法 | ロボットハンドの把持", max_documents=8)
    assert {d["scope"] for d in r["documents"]} == {a.key, b.key} and r["across_folders"]
    only_b = nav.route(lib, "ロボットハンドの把持", scope=b.key)
    assert {d["scope"] for d in only_b["documents"]} == {b.key}
    ov = nav.overview(lib)
    assert {f["scope"] for f in ov["folders"]} == {a.key, b.key} and all(f["layer_version"] >= 1 for f in ov["folders"])


def test_overview_reports_changes(home, tmp_path):
    folder = make_folder(tmp_path, "電池", BATTERY)
    st = register_and_import(folder)
    (folder / "new.md").write_text("# 新しい\n\n熱暴走を断熱材で抑えた。", encoding="utf-8")
    ov = nav.overview(runtime.library())
    assert any("変わっています" in n for n in ov["folders"][0]["notes"])


def test_mcp_tools(home, tmp_path):
    st, ids = _one_folder(tmp_path)
    from lexweft_lite.mcp_server import server

    async def names():
        return {t.name for t in await server.list_tools()}, {p.name for p in await server.list_prompts()}

    tools, prompts = asyncio.run(names())
    assert {"lw_map", "lw_route", "lw_expand", "lw_unread", "lw_refresh", "lw_scopes"} <= tools and "investigate" in prompts
    from lexweft_lite import mcp_server as m

    assert m.lw_scopes()[0]["stored_in"].startswith("フォルダの中")
    m.lw_route("ロボットハンドの把持")
    first_r = sorted(v for k, v in ids.items() if k.startswith("r"))[0]
    m.lw_read_document(first_r)
    left = [x["document_id"] for x in m.lw_unread()["unread"]]
    assert first_r not in left
    (tmp_path / "notes" / "extra.md").write_text("# 追加\n\nロボットハンドの把持を変えた。", encoding="utf-8")
    r = m.lw_refresh()
    assert r["refreshed"] == [st.key] and r["counts"].get("added") == 1 and r["layers"][0]["layer_version"] >= 2


def test_old_layer_without_model_is_stale(home, tmp_path):
    st, _ = _one_folder(tmp_path)
    assert not cl.status(st)["stale"]
    st.conn.execute("DELETE FROM lmodels")      # 前の版で作った意味層のふり
    st.conn.commit()
    assert cl.status(st)["stale"]


def test_route_api(home, tmp_path):
    from fastapi.testclient import TestClient

    from lexweft_lite.web import app

    _one_folder(tmp_path)
    r = TestClient(app).get("/api/route", params={"q": "熱暴走を防ぐ方法", "max_documents": 2}).json()
    docs = r["route"]["documents"]
    left = r["unread"]["unread"]
    assert len(docs) == 2 and docs[0]["cluster_color"].startswith("#")
    assert left and not ({d["document_id"] for d in docs} & {d["document_id"] for d in left})   # 一覧に出た資料は読み残しに出さない
