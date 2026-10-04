from __future__ import annotations

import pytest

from lexweft_lite import runtime


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LEXWEFT_HOME", str(tmp_path / "home"))
    runtime.reset()
    yield tmp_path / "home"
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
