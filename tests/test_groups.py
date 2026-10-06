from __future__ import annotations

from conftest import register_and_import
from fastapi.testclient import TestClient

from lexweft_lite import clusters as cl
from lexweft_lite import groups as gr

H = {"X-LexWeft": "1"}
BATTERY = ["セルの熱暴走を相変化材料のシートで抑える。相変化材料は潜熱で吸熱する。",
           "冷却板の流路を対向流にして、セル温度のばらつきを抑える。冷却板の流路幅も変える。",
           "セル間の熱伝播を断熱材で遅らせる。断熱材の厚みと熱伝播の関係を測った。",
           "相変化材料のシートの劣化を調べた。相変化材料の潜熱は千回で一割減った。",
           "冷却板の圧力損失と流路幅の関係を測った。流路幅を狭めると圧力損失が増えた。",
           "断熱材と相変化材料を重ねたシートで、セル間の熱伝播を抑えた。",
           "ロボットの把持部に断熱材を貼り、熱いセルを把持して運ぶ。"]
ROBOT = ["ロボットハンドの把持力を触覚センサで調整する。触覚センサは指先にある。",
         "ロボットの経路計画を強化学習で行う。強化学習の報酬は移動時間で決める。",
         "ロボットハンドで柔らかい物体を把持する。把持力は触覚センサで測る。",
         "強化学習で学んだ経路計画を実機のロボットに移す。シミュレーションとの差が課題。",
         "触覚センサの信号からロボットハンドの滑りを検出する。",
         "ロボットハンドで電池のセルを把持し、断熱材の上に置く。",
         "ロボットハンドの指の数と把持の成功率の関係を調べた。"]


def _folder(tmp_path):
    root = tmp_path / "mix"
    for name, texts in (("電池", BATTERY), ("ロボット", ROBOT)):
        d = root / name
        d.mkdir(parents=True)
        for i, t in enumerate(texts):
            (d / f"{name}_{i}.md").write_text(f"# {name} {i}\n\n{t}\n", encoding="utf-8")
    (root / "まとめ.md").write_text("# まとめ\n\n電池とロボットのメモ。\n", encoding="utf-8")
    return root


def test_groups_come_from_subfolders(home, tmp_path):
    st = register_and_import(_folder(tmp_path))
    gs = {g["key"]: g for g in gr.groups(st)}
    assert gs["電池"]["documents"] == 7 and gs["ロボット"]["documents"] == 7 and gs[gr.TOP]["documents"] == 1
    assert gs[gr.TOP]["label"] == gr.TOP_LABEL and gs[gr.TOP]["color"] == gr.TOP_COLOR
    assert gs["ロボット"]["color"] == gr.COLORS[0] and gs["電池"]["color"] == gr.COLORS[1]   # 直下の資料があっても、サブフォルダの色はずれない
    m = gr.add_to_map(st, cl.map_data(st))
    assert {p["g"] for p in m["points"]} == {"電池", "ロボット", gr.TOP}
    assert all(sum(c["groups"].values()) == c["size"] for c in m["clusters"])
    # 名前を変える (空にするとフォルダ名に戻る)
    assert gr.rename(st, "電池", "電池チーム")["label"] == "電池チーム"
    assert {g["key"]: g["label"] for g in gr.groups(st)}["電池"] == "電池チーム"
    gr.rename(st, "電池", "")
    assert {g["key"]: g["label"] for g in gr.groups(st)}["電池"] == "電池"


def test_links_between_groups(home, tmp_path):
    st = register_and_import(_folder(tmp_path))
    L = gr.links(st, "電池", "ロボット")
    assert L["a"] == "電池" and L["b"] == "ロボット"
    left, right = {c["id"] for c in L["left"]}, {c["id"] for c in L["right"]}
    for e in L["edges"]:
        assert e["a"] in left and e["b"] in right and e["pairs"] >= len(e["examples"])
        for x in e["examples"]:
            assert x["a"]["title"].startswith("電池") and x["b"]["title"].startswith("ロボット")   # 左は電池、右はロボットの資料
    # グループが 1 つしかないフォルダ
    one = tmp_path / "one" / "only"
    one.mkdir(parents=True)
    for i, t in enumerate(BATTERY):
        (one / f"b{i}.md").write_text(f"# b {i}\n\n{t}\n", encoding="utf-8")
    st1 = register_and_import(tmp_path / "one")
    assert gr.links(st1)["edges"] == [] and "1 つしか" in gr.links(st1)["note"]


def test_group_api(home, tmp_path):
    from lexweft_lite.web import app

    st = register_and_import(_folder(tmp_path))
    c = TestClient(app, base_url="http://127.0.0.1")
    assert len(c.get("/api/groups", params={"scope": st.key}).json()) == 3
    m = c.get("/api/map", params={"scope": st.key}).json()
    assert m["groups"] and "g" in m["points"][0] and "bridge" in m["clusters"][0]
    L = c.get("/api/groups/links", params={"scope": st.key, "a": "電池", "b": "ロボット"}).json()
    assert L["a"] == "電池"
    R = c.get("/api/route", params={"q": "ロボットハンドで把持する", "scope": st.key}).json()
    assert R["route"]["documents"] and all(d["group"]["key"] in ("電池", "ロボット", gr.TOP) for d in R["route"]["documents"])
    assert c.post("/api/groups/rename", json={"scope": st.key, "key": "電池", "label": "x"}).status_code == 403   # ヘッダーが無い書き込みは断る
    assert c.post("/api/groups/rename", json={"scope": st.key, "key": "無い", "label": "x"}, headers=H).status_code == 404
    assert c.post("/api/groups/rename", json={"scope": st.key, "key": "電池", "label": "<b>x</b>"}, headers=H).json()["label"] == "<b>x</b>"
