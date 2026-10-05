from __future__ import annotations

import pytest

from lexweft_lite import runtime


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from lexweft_lite import clusters as cl
    from lexweft_lite import jobs

    monkeypatch.setenv("LEXWEFT_HOME", str(tmp_path / "home"))
    runtime.reset()
    yield tmp_path / "home"
    # 前のテストの取り込みや作り直しが、次のテストに残らないようにする
    for j in list(jobs._jobs.values()):
        j.cancel.set()
    import time

    for _ in range(600):
        if not jobs.running():
            break
        time.sleep(0.05)
    cl.wait_idle()
    runtime.reset()


@pytest.fixture()
def store(home):
    return runtime.store()


SAMPLE_MD = """---
title: 蓄電池の熱暴走対策
date: 2025-04-01
---
# 蓄電池の熱暴走対策

## 背景

リチウムイオン電池は、内部短絡が起きると発熱が連鎖し、熱暴走に至ることがある。

## 課題

セル間の熱の伝わりを抑え、1 つのセルの発熱が隣のセルに広がらないようにしたい。

## 解決手段

セルの間に相変化材料を入れたシートを挟み、発熱を潜熱として吸収する。
"""


@pytest.fixture()
def sample_file(tmp_path):
    p = tmp_path / "battery.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return p


def make_folder(root, name: str, texts: list[str]):
    """資料の入ったフォルダを作る (資料 1 件 = 見出し + 本文)."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(texts):
        (d / f"{name}_{i}.md").write_text(f"# {name} {i}\n\n{t}\n", encoding="utf-8")
    return d


def register_and_import(folder):
    """フォルダを登録して取り込み、意味層を作る. 保存先を返す."""
    from lexweft_lite import clusters as cl
    from lexweft_lite import jobs, runtime

    lib = runtime.library()
    row = lib.register(str(folder))
    job = jobs.start(row["path"])
    import time

    while job.state == "running":
        time.sleep(0.05)
    st = lib.store_for_folder(row["path"])
    for _ in range(2400):   # 最大 120 秒 (PC が重いときも待つ)
        s = cl.status(st)
        if s["built_at"] and not s["building"] and not s["stale"]:
            break
        time.sleep(0.05)
    return st
