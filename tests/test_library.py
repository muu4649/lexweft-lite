from __future__ import annotations

import shutil
import time

import pytest
from conftest import make_folder, register_and_import

from lexweft_lite import clusters as cl
from lexweft_lite import concepts as co
from lexweft_lite import jobs, runtime
from lexweft_lite.library import LAYER_DIR
from lexweft_lite.store import BASE

TEXTS = ["電池のセルの熱暴走を相変化材料で抑える。", "冷却板の流路で温度差を減らす。", "断熱材でセル間の熱伝播を遅らせる。",
         "相変化材料のシートで吸熱する。", "冷却板の圧力損失を測った。", "セルの温度上昇を断熱材で抑えた。"]


def test_layer_is_stored_inside_the_folder(home, tmp_path):
    folder = make_folder(tmp_path, "電池", TEXTS)
    st = register_and_import(folder)
    assert (folder / LAYER_DIR / "lexweft.sqlite3").exists() and (folder / LAYER_DIR / "markdown").is_dir()
    assert (folder / LAYER_DIR / "これは何？.txt").exists()
    assert st.conn.execute("SELECT source FROM documents ORDER BY id").fetchone()[0] == "電池_0.md"   # フォルダからの相対パス
    ids = [d["id"] for d in st.list_documents()]
    assert all(BASE <= i < 2 * BASE for i in ids)                                                   # 番号はフォルダごとの範囲
    assert runtime.library().for_id(ids[0]) is st
    # 2 回目の取り込みで、_LeXWeft の中身を資料として読まない
    job = jobs.start(st.key)
    while job.state == "running":
        time.sleep(0.05)
    assert job.counts == {"unchanged": 6}


def test_cloud_folder_is_stored_in_the_app(home, tmp_path):
    folder = make_folder(tmp_path / "Dropbox", "電池", TEXTS)
    row = runtime.library().register(str(folder))
    assert row["location"] == "central" and "クラウド" in row["reason"]
    assert not (folder / LAYER_DIR).exists()
    st = runtime.library().store_for_folder(row["path"])
    assert str(home) in st.path


def test_folder_can_be_moved_with_its_layer(home, tmp_path):
    folder = make_folder(tmp_path, "電池", TEXTS)
    st = register_and_import(folder)
    pid = st.paragraphs_of(st.list_documents()[0]["id"])[0]["id"]
    co.write(runtime.library(), "セルの熱暴走", "課題", [pid])
    lib = runtime.library()
    lib.unregister(st.key)                      # 登録を外しても _LeXWeft は残る
    assert (folder / LAYER_DIR).exists()
    moved = tmp_path / "移動先" / "電池"
    moved.parent.mkdir()
    shutil.move(str(folder), str(moved))
    st2 = register_and_import(moved)            # 移動先で登録し直すと、意味層と課題がそのまま使える
    assert co.get_concept(lib, "セルの熱暴走")["evidence_total"] == 1
    assert st2.list_documents()[0]["source"].startswith(str(moved.resolve()))
    job = jobs.start(st2.key)
    while job.state == "running":
        time.sleep(0.05)
    assert job.counts == {"unchanged": 6}       # 移動しても読み直しにならない (相対パス)


def test_number_conflict_is_renumbered(home, tmp_path):
    a = register_and_import(make_folder(tmp_path, "a", TEXTS))           # 番号 1
    lib = runtime.library()
    lib.unregister(a.key)
    b = register_and_import(make_folder(tmp_path, "b", TEXTS[:3]))       # 番号 2
    c = register_and_import(make_folder(tmp_path, "c", TEXTS[:3]))       # 番号 3
    lib.unregister(b.key)
    st_a = register_and_import(tmp_path / "a")                           # 前の番号 1 は空いているので、そのまま使う
    assert st_a.store_no == 1
    # 別の PC から、同じ番号を使うフォルダを持ってきたことにする
    lib.unregister(st_a.key)
    d = register_and_import(make_folder(tmp_path, "d", TEXTS[:2]))       # 番号 1 を使う
    st_a = register_and_import(tmp_path / "a")                           # 番号がぶつかるので振り直す
    assert st_a.store_no not in (d.store_no, c.store_no)
    ids = [x["id"] for x in st_a.list_documents()]
    assert all(st_a.store_no * BASE <= i < (st_a.store_no + 1) * BASE for i in ids)
    assert st_a.paragraphs_of(ids[0])                                    # 段落も一緒に振り直されている
    assert cl.status(st_a)["documents_now"] == 6


def test_concepts_stay_in_their_folder(home, tmp_path):
    a = register_and_import(make_folder(tmp_path, "a", TEXTS))
    b = register_and_import(make_folder(tmp_path, "b", TEXTS))
    lib = runtime.library()
    pa = a.paragraphs_of(a.list_documents()[0]["id"])[0]["id"]
    pb = b.paragraphs_of(b.list_documents()[0]["id"])[0]["id"]
    co.write(lib, "セルの熱暴走", "課題", [pa])
    co.write(lib, "相変化材料", "解決手段", [pa])
    co.write(lib, "セルの熱暴走", "課題", [pb])
    assert {c["folder"] for c in co.list_concepts(lib, query="熱暴走")} == {a.label, b.label}
    assert [c["folder"] for c in co.list_concepts(lib, a.key)] and all(c["scope"] == a.key for c in co.list_concepts(lib, a.key))
    with pytest.raises(ValueError):
        co.get_concept(lib, "セルの熱暴走")                     # 同じ名前が 2 つのフォルダにある
    assert co.get_concept(lib, "セルの熱暴走", a.key)["folder"] == a.label
    assert co.relate(lib, "相変化材料", "セルの熱暴走", "解決する", scope=a.key)["created"]
    with pytest.raises(ValueError):
        co.write(lib, "またぐ", "課題", [pa, pb])                # 根拠が 2 つのフォルダにまたがる


def test_changes_are_detected_and_vanished_files_removed(home, tmp_path):
    folder = make_folder(tmp_path, "電池", TEXTS)
    st = register_and_import(folder)
    assert jobs.changed_folders() == []
    (folder / "電池_0.md").unlink()
    (folder / "new.md").write_text("# 新しい\n\n冷却板の流路を変えた。", encoding="utf-8")
    assert [r["path"] for r in jobs.changed_folders()] == [st.key]
    job = jobs.auto_import_once()
    while job.state == "running":
        time.sleep(0.05)
    assert job.counts.get("added") == 1 and job.counts.get("removed") == 1 and job.auto
    assert jobs.changed_folders() == []
    titles = {d["title"] for d in st.list_documents()}
    assert "新しい" in titles and "電池 0" not in titles
    runtime.library().set_auto(st.key, False)
    (folder / "new2.md").write_text("# もう 1 つ\n\n断熱材。", encoding="utf-8")
    assert jobs.auto_import_once() is None                     # 自動取り込みを切ったフォルダは取り込まない


def test_legacy_single_database_is_migrated(home, tmp_path):
    """前の版 (すべてを 1 つのデータベースに入れていた) から、フォルダごとの保存先へ移す."""
    from lexweft_lite import layer as ly
    from lexweft_lite.ingest import ingest
    from lexweft_lite.store import Store

    folder = make_folder(tmp_path, "電池", TEXTS)
    home.mkdir(parents=True, exist_ok=True)
    old = Store(home / "lexweft.sqlite3")
    old.conn.execute("DROP TABLE sources")
    old.conn.execute("CREATE TABLE sources (path TEXT PRIMARY KEY, added_at TEXT NOT NULL, last_run TEXT)")
    old.conn.execute("INSERT INTO sources(path, added_at) VALUES (?, '2026-10-01')", (str(folder.resolve()),))
    old.conn.commit()
    ingest(old, str(folder))
    pid = old.paragraphs_of(old.list_documents()[0]["id"])[0]["id"]
    ly.upsert_concept(old, "セルの熱暴走", "課題")
    ly.add_evidence(old, "セルの熱暴走", [pid])
    old.close()
    runtime.reset()
    lib = runtime.library()
    assert lib.catalog.count_documents() == 0                       # アプリ側から移した
    st = lib.store_for_folder(str(folder))
    assert st.count_documents() == 6 and (folder / LAYER_DIR).exists()
    assert co.get_concept(lib, "セルの熱暴走")["evidence_total"] == 1   # 課題も付け直した
