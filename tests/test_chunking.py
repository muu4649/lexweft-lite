"""段落の分け方: 見出しは段落に持たせず節の表に置く。段落は割らない。前の版の資料は取り込み直す."""
from __future__ import annotations

from conftest import register_and_import

from lexweft_lite import jobs, runtime
from lexweft_lite.chunker import CHUNKING_VERSION, from_labeled, split_document
from lexweft_lite.ingest import ingest, ingest_text
from lexweft_lite.markdown import document_markdown

LONG = "。".join(f"これは長い段落の{i}番目の文で、熱暴走と冷却について書いている" for i in range(80)) + "。"
DOC = f"""# 電池の資料

## 課題

セルの熱暴走が隣のセルに広がる。

温度が上がると止まらない。

## 解決手段

{LONG}

## 空の節

## 効果

延焼を防げる。
"""


def test_split_keeps_headings_out_of_paragraphs():
    paras, sections = split_document(DOC)
    assert all(not p.startswith("#") for p in paras)                  # 見出しを段落に付けない
    assert LONG in paras and len(LONG) > 2000                          # 長い段落を割らない
    assert [h for h, _, _ in sections] == ["## 課題", "## 解決手段", "## 効果"]   # 本文の無い節と題名だけの節は捨てる
    first, last = sections[0][1], sections[0][2]
    assert paras[first].startswith("セルの熱暴走") and paras[last].startswith("温度が上がる") and last == first + 1
    assert split_document("見出しの無い文章。\n\n二つ目の段落。") == (["見出しの無い文章。", "二つ目の段落。"], [])


def test_labeled_pairs_become_one_section_each():
    paras, sections = from_labeled([("列", "列: a, b"), ("行 1〜2", "a=1; b=2"), ("空", "  ")])
    assert paras == ["列: a, b", "a=1; b=2"] and sections == [("列", 0, 0), ("行 1〜2", 1, 1)]


def test_ingest_stores_sections_not_headings(store, tmp_path):
    f = tmp_path / "battery.md"
    f.write_text(DOC, encoding="utf-8")
    doc_id = ingest(store, str(f))[0].document_id
    paras = store.paragraphs_of(doc_id)
    assert all(p["heading"] == "" and not p["text"].startswith("#") for p in paras)
    assert [s["heading"] for s in store.sections_of(doc_id)] == ["## 課題", "## 解決手段", "## 効果"]
    md = document_markdown(store, doc_id)
    assert md.count("## 課題") == 1 and md.index("## 課題") < md.index("セルの熱暴走") < md.index("## 解決手段")


def _make_legacy(st, doc_id):
    """前の版の形 (段落に見出しを持たせ、本文の先頭にも見出しを付ける) に戻し、版も戻す."""
    secs = st.sections_of(doc_id)
    with st.tx() as c:
        for s in secs:
            for p in st.paragraphs_of(doc_id)[s["first_ordinal"]: s["last_ordinal"] + 1]:
                c.execute("UPDATE paragraphs SET heading = ?, text = ? WHERE id = ?", (s["heading"], s["heading"] + "\n" + p["text"], p["id"]))
        c.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))
        c.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES ('rebuild')")   # 本文を直接書き換えたので、全文索引を合わせる
    st.set_meta("chunking", "1")


def test_old_documents_are_rechunked_and_evidence_follows(home, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "battery.md").write_text(DOC, encoding="utf-8")
    st = register_and_import(folder)
    doc_id = st.list_documents()[0]["id"]
    _make_legacy(st, doc_id)
    old = st.paragraphs_of(doc_id)
    assert old[0]["text"].startswith("## 課題")
    from lexweft_lite import layer as ly

    c = ly.upsert_concept(st, "セルの熱暴走", "課題", None, [])
    ly.add_evidence(st, c["id"], [old[0]["id"]], "")
    text_doc = ingest_text(runtime.store(), "メモ", "## 見出し\n\n本文の段落。").document_id
    _make_legacy(runtime.store(), text_doc)

    done = jobs.rechunk_all(runtime.library())
    assert st.key in done and st.meta("chunking") == CHUNKING_VERSION
    new = st.paragraphs_of(doc_id)
    assert st.list_documents()[0]["id"] == doc_id                                       # 資料の番号は変わらない
    assert all(p["heading"] == "" and not p["text"].startswith("#") for p in new)        # 見出しは段落から外れた
    assert [s["heading"] for s in st.sections_of(doc_id)] == ["## 課題", "## 解決手段", "## 効果"]
    ev = ly.get_concept(st, c["id"])["evidence"]
    assert [e["paragraph_id"] for e in ev] == [new[0]["id"]]                             # 根拠は新しい段落に付け直された
    # 元のファイルが無い資料 (貼り付けた文章) も、見出しが節の表に移る
    cs = runtime.store()
    assert [p["text"] for p in cs.paragraphs_of(text_doc)] == ["本文の段落。"] and cs.sections_of(text_doc)[0]["heading"] == "## 見出し"
    assert jobs.rechunk_all(runtime.library()) == {}                                     # 2 回目は何もしない
