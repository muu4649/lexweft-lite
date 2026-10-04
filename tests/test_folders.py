from __future__ import annotations

import time

from fastapi.testclient import TestClient

from lexweft_lite import keywords as kw
from lexweft_lite import runtime
from lexweft_lite.ingest import ingest
from lexweft_lite.loaders import iter_files, scan

H = {"X-LexWeft": "1"}


def _tree(root):
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("# 冷却板\n\n冷却板の流路を対向流にして、セル温度のばらつきを抑える。", encoding="utf-8")
    (root / "notes" / "b.txt").write_text("冷却板の圧力損失と流路幅の関係を測った。セル温度のばらつきも見た。", encoding="utf-8")
    (root / "notes" / "c.md").write_text("相変化材料のシートで、セルの発熱を吸収する。冷却板は使わない。", encoding="utf-8")
    for junk in [".venv/lib/x.md", ".git/y.txt", "node_modules/pkg/README.md", "lib/site-packages/z.txt", "Some.app/Contents/r.md"]:
        p = root / junk
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("入れてはいけない", encoding="utf-8")
    (root / "notes" / "LICENSE.txt").write_text("MIT License", encoding="utf-8")
    (root / "notes" / "image.png").write_bytes(b"\x89PNG")


def test_folder_skips_hidden_and_dev_dirs(tmp_path):
    _tree(tmp_path)
    names = sorted(p.name for p in iter_files(tmp_path))
    assert names == ["a.md", "b.txt", "c.md"]
    sc = scan(tmp_path)
    assert sc["files"] == 3 and sc["by_suffix"] == {".md": 2, ".txt": 1}


def test_keywords_graph(store, tmp_path):
    _tree(tmp_path)
    ingest(store, str(tmp_path))
    assert kw.pending(store) == []
    g = kw.graph(store, limit=20)
    labels = {n["label"] for n in g["nodes"]}
    assert {"冷却板", "流路"} <= labels or {"冷却板", "セル温度"} <= labels
    assert any(e["source"] == "k:冷却板" or e["target"] == "k:冷却板" for e in g["edges"])
    focused = kw.graph(store, limit=20, query="相変化材料")
    assert focused["documents"] == 1
    assert kw.documents_with(store, "冷却板")["total"] == 3


def test_extract_terms_ignores_code_in_japanese_docs():
    counts = kw.extract_terms(["相変化材料のシートを使う。PCM を挟む。\n```python\nimport numpy as np\nx = np.zeros(3)\n```\n<div style='padding:0'>熱伝播</div>"])
    assert counts["相変化材料"] == 1 and counts["PCM"] == 1 and counts["熱伝播"] == 1
    assert "numpy" not in counts and "padding" not in counts and "import" not in counts


def test_jobs_scan_and_delete_under(home, tmp_path):
    from lexweft_lite.web import app

    _tree(tmp_path)
    c = TestClient(app)
    assert c.get("/api/scan", params={"path": str(tmp_path)}).json()["files"] == 3
    job = c.post("/api/jobs", json={"path": str(tmp_path)}, headers=H).json()
    for _ in range(100):
        j = c.get("/api/jobs/latest").json()
        if j["state"] != "running":
            break
        time.sleep(0.05)
    assert j["id"] == job["id"] and j["state"] == "done" and j["counts"] == {"added": 3}
    docs = c.get("/api/documents", params={"q": "notes"}).json()
    assert docs["total"] == 3
    folders = c.get("/api/documents/folders", params={"depth": 99}).json()
    assert folders[0]["documents"] == 3
    assert c.post("/api/documents/delete-under", json={"prefix": "/"}, headers=H).status_code == 400
    r = c.post("/api/documents/delete-under", json={"prefix": str(tmp_path / "notes")}, headers=H).json()
    assert r["deleted"] == 3 and runtime.store().count_documents() == 0
    # 名前の前方一致だけでは消さない (notes2 は notes の中ではない)
    (tmp_path / "notes2").mkdir()
    (tmp_path / "notes2" / "d.md").write_text("別のフォルダ", encoding="utf-8")
    ingest(runtime.store(), str(tmp_path / "notes2"))
    assert c.post("/api/documents/delete-under", json={"prefix": str(tmp_path / "notes")}, headers=H).json()["deleted"] == 0


def test_nfd_paths_match_nfc(home, tmp_path):
    import unicodedata

    from lexweft_lite import clusters as cl

    name = "IPランドスケープ事例"
    d = tmp_path / unicodedata.normalize("NFD", name)
    d.mkdir()
    (d / "a.md").write_text("# a\n\nランドスケープの事例。", encoding="utf-8")
    s = runtime.store()
    ingest(s, str(d))
    src = s.list_documents()[0]["source"]
    assert src == unicodedata.normalize("NFC", src)
    nfc_dir = str(tmp_path / name)
    assert cl.scope_documents(s, nfc_dir) and cl.scope_documents(s, unicodedata.normalize("NFD", nfc_dir))


def test_delete_outside_sources(home, tmp_path):
    from lexweft_lite.web import app

    _tree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.md").write_text("# x\n\n別の場所の資料", encoding="utf-8")
    s = runtime.store()
    ingest(s, str(tmp_path / "notes"))
    ingest(s, str(other))
    from lexweft_lite.ingest import ingest_text

    ingest_text(s, "メモ", "貼り付けた文章")
    c = TestClient(app)
    assert c.post("/api/documents/delete-outside", json={}, headers=H).status_code == 400   # 登録が無いと使えない
    s.add_source(str(tmp_path / "notes"))
    assert c.get("/api/documents/outside").json()["documents"] == 1
    assert c.post("/api/documents/delete-outside", json={}, headers=H).json()["deleted"] == 1
    assert s.count_documents() == 4   # notes の 3 件と貼り付けた文章は残る
