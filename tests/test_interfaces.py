from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from lexweft_lite import cli, runtime
from lexweft_lite.ingest import ingest

H = {"X-LexWeft": "1"}


def test_api_flow(home, sample_file):
    from lexweft_lite.web import app

    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.post("/api/documents/path", json={"path": str(sample_file)}).status_code == 403  # 書き込みにはヘッダが要る
    r = c.post("/api/documents/path", json={"path": str(sample_file)}, headers=H).json()
    doc_id = r[0]["document_id"]
    doc = c.get(f"/api/documents/{doc_id}").json()
    pid = doc["paragraphs"][1]["id"]
    a = c.post("/api/concepts", json={"name": "セル間の熱伝播", "type": "課題", "paragraph_ids": [pid]}, headers=H).json()
    b = c.post("/api/concepts", json={"name": "相変化材料シート", "type": "解決手段", "paragraph_ids": [doc["paragraphs"][2]["id"]]}, headers=H).json()
    assert c.post("/api/relations", json={"source": b["id"], "target": a["id"], "kind": "解決する", "paragraph_id": pid}, headers=H).json()["created"]
    g = c.get("/api/graph").json()
    assert len(g["nodes"]) == 3 and len(g["edges"]) == 3
    s = c.get("/api/search", params={"q": ["熱暴走|相変化"]}).json()
    assert len(s["queries"]) == 2 and s["paragraphs"]
    assert "lexweft-lite" in c.get("/api/mcp-config").json()["mcpServers"]
    assert c.get("/api/overview").json()["distribution"] == {"channel": "dev", "feedback_url": ""}
    assert "## 課題" in c.get("/api/export/layer.md").text
    assert c.get("/").status_code == 200
    # 別の Host からの呼び出しは断る
    assert c.get("/api/overview", headers={"Host": "evil.example"}).status_code == 403
    up = c.post("/api/documents/upload", files=[("files", ("n.txt", "冷却板の試作メモ".encode(), "text/plain"))], headers=H).json()
    assert up[0]["status"] == "added" and (home / "files" / "n.txt").exists()
    assert c.delete(f"/api/documents/{doc_id}", headers=H).json()["deleted"]
    assert c.get(f"/api/concepts/{a['id']}").json()["evidence_total"] == 0


def test_mcp_tools(home, sample_file):
    from lexweft_lite.mcp_server import server

    async def run():
        tools = {t.name for t in await server.list_tools()}
        assert {"lw_overview", "lw_read_document", "lw_search", "lw_write_concept", "lw_relate", "lw_export_layer"} <= tools
        prompts = {p.name for p in await server.list_prompts()}
        assert "build_layer" in prompts

    asyncio.run(run())
    from lexweft_lite import mcp_server as m

    [r] = ingest(runtime.store(), str(sample_file))
    ov = m.lw_overview()
    assert ov["documents_without_concepts"][0]["id"] == r.document_id
    md = m.lw_read_document(r.document_id, 0, 2)
    assert "range: 1-2" in md
    pid = runtime.store().paragraphs_of(r.document_id)[2]["id"]
    w = m.lw_write_concept("相変化材料シート", "解決手段", [pid], aliases=["PCM シート"])
    assert w["created"] and w["evidence"]["added"] == 1
    m.lw_write_concept("セル間の熱伝播", "課題", [pid - 1])
    assert m.lw_relate("PCMシート", "セル間の熱伝播", "解決する", pid)["created"]
    assert m.lw_search(["相変化材料"])[0]["concepts"][0]["name"] == "相変化材料シート"
    assert "解決する → セル間の熱伝播" in m.lw_export_layer()
    assert m.lw_overview()["documents_without_concepts"] == []


def test_cli(home, sample_file, capsys):
    assert cli.main(["add", str(sample_file)]) == 0
    assert cli.main(["status"]) == 0
    assert "資料 1" in capsys.readouterr().out
    assert cli.main(["mcp-config"]) == 0
    out = capsys.readouterr().out
    cfg = json.loads(out)
    assert cfg["mcpServers"]["lexweft-lite"]["args"] == ["mcp"]
    assert cli.main(["export", "--out", str(home / "ex")]) == 0
    assert (home / "ex" / "layer.md").exists() and list((home / "ex" / "documents").glob("**/*.md"))


def test_serve_port_fallback(home, monkeypatch):
    import pytest

    from lexweft_lite import web

    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, log_level: seen.update(port=port))
    monkeypatch.setattr(web, "_lite_running", lambda p: False)
    monkeypatch.setattr(web, "_port_free", lambda host, p: p != 8765)
    web.serve(open_browser=False)              # 番号を指定しなければ、空いている次の番号で開く
    assert seen["port"] == 8766
    with pytest.raises(SystemExit):           # 指定した番号が使われていれば止まる
        web.serve(port=8765, open_browser=False)


def test_serve_for_app_writes_ready_file(home, tmp_path):
    import json
    import os
    import subprocess
    import sys
    import time
    import urllib.request

    ready = tmp_path / "ready.json"
    proc = subprocess.Popen([sys.executable, "-m", "lexweft_lite.cli", "serve", "--no-browser", "--ready-file", str(ready),
                             "--parent-pid", str(os.getpid())], env={**os.environ, "LEXWEFT_HOME": str(home)})
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.1)
        url = json.loads(ready.read_text())["url"]
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url + "api/overview", timeout=5) as r:
            assert json.loads(r.read())["version"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_security_headers_and_hosts(home):
    from lexweft_lite.web import app

    c = TestClient(app, base_url="http://127.0.0.1")
    r = c.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in r.text   # 画面にその場のスクリプトを書かない (CSP で止まるため)
    assert TestClient(app, base_url="http://testserver").get("/api/overview").status_code == 403    # テスト用の名前も断る
    assert TestClient(app, base_url="http://evil.example").get("/api/overview").status_code == 403
    assert c.post("/api/sources", json={"path": "/tmp"}).status_code == 403    # 書き込みは X-LexWeft ヘッダーが要る
