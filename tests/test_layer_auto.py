from __future__ import annotations

from fastapi.testclient import TestClient

from lexweft_lite import clusters as cl
from lexweft_lite import runtime
from lexweft_lite.ingest import ingest

H = {"X-LexWeft": "1"}

BATTERY = ["セルの熱暴走を相変化材料のシートで抑える。相変化材料は潜熱で吸熱する。",
           "冷却板の流路を対向流にして、セル温度のばらつきを抑える。冷却板の流路幅も変える。",
           "セル間の熱伝播を断熱材で遅らせる。断熱材の厚みと熱伝播の関係を測った。",
           "相変化材料のシートの劣化を調べた。相変化材料の潜熱は千回で一割減った。",
           "冷却板の圧力損失と流路幅の関係を測った。流路幅を狭めると圧力損失が増えた。",
           "断熱材と相変化材料を重ねたシートで、セル間の熱伝播を抑えた。",
           "冷却板の対向流の流路で、セル温度の差が二度まで下がった。"]
ROBOT = ["ロボットハンドの把持力を触覚センサで調整する。触覚センサは指先にある。",
         "ロボットの経路計画を強化学習で行う。強化学習の報酬は移動時間で決める。",
         "ロボットハンドで柔らかい物体を把持する。把持力は触覚センサで測る。",
         "強化学習で学んだ経路計画を実機のロボットに移す。シミュレーションとの差が課題。",
         "触覚センサの信号からロボットハンドの滑りを検出する。",
         "ロボットの経路計画で障害物を避ける。強化学習と探索を組み合わせる。",
         "ロボットハンドの指の数と把持の成功率の関係を調べた。"]


def _folder(root, name, texts):
    d = root / name
    d.mkdir()
    for i, t in enumerate(texts):
        (d / f"{name}_{i}.md").write_text(f"# {name} {i}\n\n{t}\n", encoding="utf-8")
    return d


def test_layer_is_built_per_folder(home, tmp_path):
    s = runtime.store()
    a = _folder(tmp_path, "battery", BATTERY)
    b = _folder(tmp_path, "robot", ROBOT)
    for d in (a, b):
        s.add_source(str(d))
        ingest(s, str(d))
    st = cl.build(s, str(a))
    assert st["documents"] == 7 and st["clusters"] >= 1 and not st["stale"]
    m = cl.map_data(s, str(a))
    assert len(m["points"]) == 7
    titles = {p["title"] for p in m["points"]}
    assert all(t.startswith("battery") for t in titles)          # 指定したフォルダの資料だけ
    words = {t for c in m["clusters"] for t, _ in c["terms"]}
    assert "ロボット" not in words and "ロボットハンド" not in words
    # すべての資料の範囲は別に作る
    assert cl.status(s, cl.ALL)["built_at"] is None
    cl.build(s, cl.ALL)
    assert len(cl.map_data(s, cl.ALL)["points"]) == 14
    # 似た資料は同じフォルダの中から
    doc = m["points"][0]["id"]
    sims = cl.similar_documents(s, doc)
    assert sims and all(x["title"].startswith("battery") for x in sims)
    # 資料が増えると古くなる
    (a / "extra.md").write_text("# 追加\n\n冷却板の流路を変えた。", encoding="utf-8")
    ingest(s, str(a))
    assert cl.status(s, str(a))["stale"]


def test_scopes_api(home, tmp_path):
    from lexweft_lite.web import app

    a = _folder(tmp_path, "battery", BATTERY)
    c = TestClient(app)
    s = runtime.store()
    s.add_source(str(a))
    ingest(s, str(a))
    cl.build(s, str(a))
    sc = c.get("/api/scopes").json()
    assert [x["scope"] for x in sc] == [str(a), "*"] and sc[0]["clusters"] >= 1
    m = c.get("/api/map", params={"scope": str(a)}).json()
    assert len(m["points"]) == 7
    d = c.get(f"/api/clusters/{m['clusters'][0]['id']}", params={"scope": str(a)}).json()
    assert d["terms"] and d["documents"]
    g = c.get("/api/keywords/graph", params={"scope": str(a), "limit": 20}).json()
    assert g["documents"] == 7 and any(n.get("cluster") is not None for n in g["nodes"])
