from __future__ import annotations

from conftest import make_folder, register_and_import
from fastapi.testclient import TestClient

from lexweft_lite import clusters as cl
from lexweft_lite import runtime

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


def test_layer_is_built_per_folder(home, tmp_path):
    a = register_and_import(make_folder(tmp_path, "battery", BATTERY))
    b = register_and_import(make_folder(tmp_path, "robot", ROBOT))
    m = cl.map_data(a)
    assert len(m["points"]) == 7 and all(p["title"].startswith("battery") for p in m["points"])   # そのフォルダの資料だけ
    words = {t for c in m["clusters"] for t, _ in c["terms"]}
    assert "ロボット" not in words and "ロボットハンド" not in words
    assert len(cl.map_data(b)["points"]) == 7
    doc = m["points"][0]["id"]
    sims = cl.similar_documents(a, doc)
    assert sims and all(x["title"].startswith("battery") for x in sims)   # 似た資料は同じフォルダの中から
    # 資料が増えると古くなる
    (tmp_path / "battery" / "extra.md").write_text("# 追加\n\n冷却板の流路を変えた。", encoding="utf-8")
    from lexweft_lite.ingest import ingest

    ingest(a, str(tmp_path / "battery"), a.markdown_dir)
    assert cl.status(a)["stale"]


def test_cluster_ids_and_colors_stay_when_rebuilt(home, tmp_path):
    st = register_and_import(make_folder(tmp_path, "mix", BATTERY + ROBOT))
    before = {c["label"]: (c["id"], c["color"]) for c in cl.clusters(st)}
    v1 = cl.status(st)["version"]
    (tmp_path / "mix" / "extra.md").write_text("# 追加\n\nロボットハンドの触覚センサで把持力を測った。", encoding="utf-8")
    from lexweft_lite.ingest import ingest

    ingest(st, str(tmp_path / "mix"), st.markdown_dir)
    cl.build(st)
    after = cl.status(st)
    assert after["version"] == v1 + 1 and after["changes"]["added_documents"] == 1
    kept = {c["id"]: c["color"] for c in cl.clusters(st)}
    # 前のまとまりの番号と色は、中身が重なっていれば引き継ぐ
    assert after["changes"]["kept_clusters"] >= 1
    assert any(i in kept and kept[i] == color for i, color in before.values())


def test_scopes_api(home, tmp_path):
    from lexweft_lite.web import app

    st = register_and_import(make_folder(tmp_path, "battery", BATTERY))
    c = TestClient(app)
    sc = c.get("/api/scopes").json()
    assert [x["scope"] for x in sc] == [st.key] and sc[0]["clusters"] >= 1 and sc[0]["location"] == "folder"
    m = c.get("/api/map", params={"scope": st.key}).json()
    assert len(m["points"]) == 7
    d = c.get(f"/api/clusters/{m['clusters'][0]['id']}", params={"scope": st.key}).json()
    assert d["terms"] and d["documents"]
    g = c.get("/api/keywords/graph", params={"scope": st.key, "limit": 20}).json()
    assert g["documents"] == 7 and any(n.get("cluster") is not None for n in g["nodes"])
    srcs = c.get("/api/sources").json()
    assert srcs[0]["documents"] == 7 and srcs[0]["changed"] is False


def test_updated_files_keep_document_and_cluster_ids(home, tmp_path):
    import time

    from lexweft_lite.ingest import ingest

    folder = make_folder(tmp_path, "mix", BATTERY + ROBOT)
    st = register_and_import(folder)
    ids_before = {d["source"]: d["id"] for d in st.list_documents()}
    clusters_before = {c["id"] for c in cl.clusters(st)}
    time.sleep(1.1)   # 時刻は秒単位なので、前の版より後に取り込んだと分かるように
    for f in sorted(folder.glob("*.md")):   # すべてのファイルの書き方だけを少し変える
        f.write_text(f.read_text(encoding="utf-8") + "\n\n(図は省いた)\n", encoding="utf-8")
    res = ingest(st, str(folder), st.markdown_dir)
    assert {r.status for r in res} == {"updated"}
    assert {d["source"]: d["id"] for d in st.list_documents()} == ids_before   # 資料の番号は変わらない
    assert len(list(st.markdown_dir.glob("*.md"))) == len(ids_before)       # 古い Markdown が残らない
    cl.build(st)
    ch = cl.status(st)["changes"]
    assert ch["added_documents"] == 0 and ch["removed_documents"] == 0 and ch["updated_documents"] == len(ids_before)
    assert {c["id"] for c in cl.clusters(st)} == clusters_before and ch["gone_clusters"] == [] and ch["new_clusters"] == []
