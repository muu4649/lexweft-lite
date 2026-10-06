from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lexweft_lite import inbox as ib
from lexweft_lite import jobs, runtime

H = {"X-LexWeft": "1"}


@pytest.fixture()
def userhome(home, tmp_path, monkeypatch):
    """ホームフォルダを一時的な場所にする (資料を入れるフォルダはホームの下にしか作れないため)."""
    h = tmp_path / "user"
    (h / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


def _wait():
    for _ in range(200):
        if not jobs.running():
            return
        time.sleep(0.05)


def test_choose_creates_and_registers(userhome):
    lib = runtime.library()
    info = ib.info(lib)
    assert info["path"] is None and info["suggestion"].endswith("Documents/" + ib.DEFAULT_NAME)
    out = ib.choose(lib, info["suggestion"])
    assert Path(out["path"]).is_dir() and out["registered"] and out["auto"]   # 作って、登録して、自動で取り込む
    ib.add_group(lib, "材料")
    assert [g["name"] for g in ib.info(lib)["groups"]] == ["材料"]


@pytest.mark.parametrize("bad", ["/etc/lexweft", "/", "relative/path", ""])
def test_choose_refuses_outside_home(userhome, bad):
    with pytest.raises(ValueError):
        ib.choose(runtime.library(), bad)


@pytest.mark.parametrize("bad", ["..", ".hidden", "_LeXWeft", "", "   "])
def test_group_names_are_checked(userhome, bad):
    lib = runtime.library()
    ib.choose(lib, ib.suggestion())
    with pytest.raises(ValueError):
        ib.add_group(lib, bad)


def test_save_file_is_safe(userhome):
    lib = runtime.library()
    root = Path(ib.choose(lib, ib.suggestion())["path"])
    ib.add_group(lib, "ロボット")
    a = ib.save_file(lib, "../../evil.md", b"# a\n\nx", "ロボット")
    assert a.parent == root / "ロボット" and a.name == "evil.md"          # パスの部分は捨てて、グループの中に保存
    b = ib.save_file(lib, "evil.md", b"# b\n\ny", "ロボット")
    assert b.name == "evil (2).md" and a.read_text() == "# a\n\nx"         # 同じ名前は上書きしない
    with pytest.raises(ValueError):
        ib.save_file(lib, "run.sh", b"echo", None)                         # 取り込めない形式は保存しない
    with pytest.raises(ValueError):
        ib.save_file(lib, "x.md", b"x", "無いグループ")
    with pytest.raises(ValueError):
        ib.save_file(lib, "x.md", b"x", "../..")


def test_inbox_api_upload_and_text(userhome, monkeypatch):
    from lexweft_lite.web import app

    opened = []
    monkeypatch.setattr(ib.subprocess, "run", lambda args, **kw: opened.append(args))   # 本物の Finder は開かない
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.post("/api/inbox", json={"path": ib.suggestion()}).status_code == 403   # ヘッダーが要る
    r = c.post("/api/inbox", json={"path": ib.suggestion()}, headers=H).json()
    root = Path(r["path"])
    _wait()
    c.post("/api/inbox/group", json={"name": "電池"}, headers=H)
    up = c.post("/api/documents/upload", files=[("files", ("熱暴走.md", "# 熱暴走\n\n相変化材料で熱暴走を抑える。".encode(), "text/markdown"))],
                data={"group": "電池"}, headers=H).json()
    assert up[0]["status"] == "saved" and (root / "電池" / "熱暴走.md").exists()
    _wait()
    t = c.post("/api/documents/text", json={"title": "冷却板のメモ", "text": "冷却板の流路を対向流にする。", "group": "電池"}, headers=H).json()
    assert t["status"] == "saved" and (root / "電池" / "冷却板のメモ.md").read_text().startswith("# 冷却板のメモ")
    _wait()
    docs = c.get("/api/documents", params={"q": ""}).json()["documents"]
    assert {d["title"] for d in docs} >= {"熱暴走", "冷却板のメモ"}   # フォルダに保存して、取り込まれている
    assert c.post("/api/inbox/open", json={"group": "電池"}, headers=H).json()["opened"].endswith("電池")
    assert opened and opened[-1][-1].endswith("電池")
    assert c.post("/api/inbox/open", json={"group": "../.."}, headers=H).status_code == 400
