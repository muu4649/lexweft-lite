from __future__ import annotations

import io
import zipfile

import pytest

from lexweft_lite import layer as ly
from lexweft_lite import runtime
from lexweft_lite.ingest import ingest, ingest_text
from lexweft_lite.markdown import document_markdown, layer_markdown
from lexweft_lite.search import search


def _ids(store, doc_id):
    return [p["id"] for p in store.paragraphs_of(doc_id)]


def test_ingest_and_markdown(store, sample_file, home):
    [r] = ingest(store, str(sample_file), runtime.markdown_dir())
    assert r.status == "added" and r.paragraphs == 3
    md = document_markdown(store, r.document_id)
    assert md.startswith("---\nid: ")
    assert "title: 蓄電池の熱暴走対策" in md and "date: 2025-04-01" in md
    assert "## 課題" in md and "[¶" in md
    # 見出しは本文の段落に重ねて書かない
    assert "[¶2] ## 課題" not in md
    assert r.markdown_path and (home / "markdown").exists()
    # 同じ中身はもう一度取り込まない
    [r2] = ingest(store, str(sample_file), runtime.markdown_dir())
    assert r2.status == "unchanged"


def test_reingest_keeps_evidence_on_unchanged_paragraphs(store, sample_file):
    [r] = ingest(store, str(sample_file))
    ids = _ids(store, r.document_id)
    ly.upsert_concept(store, "セル間の熱伝播", "課題")
    ly.add_evidence(store, "セル間の熱伝播", [ids[1]])
    sample_file.write_text(sample_file.read_text(encoding="utf-8") + "\n## 効果\n\n隣のセルの温度上昇を抑えられる。\n", encoding="utf-8")
    [r2] = ingest(store, str(sample_file))
    assert r2.status == "updated"
    c = ly.get_concept(store, "セル間の熱伝播")
    assert c["evidence_total"] == 1 and "隣のセル" in c["evidence"][0]["text"]


def test_layer_write_relate_merge(store, sample_file):
    [r] = ingest(store, str(sample_file))
    ids = _ids(store, r.document_id)
    a = ly.upsert_concept(store, "セル間の熱伝播", "課題", "1 セルの発熱が隣へ広がる", ["熱連鎖"])
    assert a["created"] and a["aliases_added"] == 1
    ly.add_evidence(store, a["id"], [ids[1], 999999])
    b = ly.upsert_concept(store, "相変化材料シート", "解決手段")
    rel = ly.relate(store, "相変化材料シート", "熱連鎖", "解決する", ids[2])
    assert rel["created"] and rel["dst"] == "セル間の熱伝播"
    # 別名でも同じ概念に足される (型は変えない)
    again = ly.upsert_concept(store, "熱連鎖", "解決手段")
    assert again["id"] == a["id"] and not again["created"] and again["type"] == "課題"
    # 統合: drop の名前は別名に、関係は keep に
    c = ly.upsert_concept(store, "PCM シート", "解決手段")
    ly.add_evidence(store, c["id"], [ids[2]])
    merged = ly.merge_concepts(store, b["id"], c["id"])
    assert "PCM シート" in merged["aliases"] and merged["evidence_total"] == 1
    assert ly.resolve(store, "pcmシート")["id"] == b["id"]
    g = ly.graph(store)
    kinds = {n["kind"] for n in g["nodes"]}
    assert kinds == {"concept", "document"} and any(e["relation"] for e in g["edges"])
    md = layer_markdown(store)
    assert "## 課題" in md and "## 解決手段" in md and "解決する → セル間の熱伝播" in md


def test_types(store):
    assert [t["name"] for t in ly.list_types(store)][:2] == ["課題", "解決手段"]
    ly.add_type(store, "効果", "解決手段で得られること")
    ly.upsert_concept(store, "温度上昇の抑制", "効果")
    with pytest.raises(ValueError):
        ly.delete_type(store, "効果")
    ly.upsert_concept(store, "未定義の型の概念", "材料")
    assert "材料" in [t["name"] for t in ly.list_types(store)]


def test_search_multi_query_and_short_terms(store, sample_file):
    ingest(store, str(sample_file))
    ingest_text(store, "メモ", "全固体電池は電解質が固体なので、液漏れが起きにくい。")
    hits = search(store, ["熱暴走"])
    assert hits and "熱暴走" in hits[0]["text"]
    # 2 文字の語は部分一致で探す
    assert any("電池" in h["text"] for h in search(store, ["電池"]))
    # 言い換えを複数渡すと両方の段落が出る
    both = search(store, ["相変化材料", "全固体電池"], top_k=5)
    titles = {h["title"] for h in both}
    assert titles == {"蓄電池の熱暴走対策", "メモ"}
    # 語は AND で効く
    assert search(store, ["相変化材料 液漏れ"]) == []


def test_docx_loader(tmp_path, store):
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>報告書</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>試作した冷却板の熱抵抗を測った。</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    p = tmp_path / "r.docx"
    p.write_bytes(buf.getvalue())
    [r] = ingest(store, str(p))
    assert r.title == "報告書"
    assert "熱抵抗" in document_markdown(store, r.document_id)


def test_csv_loader(tmp_path, store):
    p = tmp_path / "t.csv"
    p.write_text("材料,熱伝導率\n銅,398\nアルミ,237\n", encoding="utf-8")
    [r] = ingest(store, str(p))
    assert r.paragraphs == 2
    assert search(store, ["アルミ"])


def test_title_heading_is_not_repeated(store, tmp_path):
    from lexweft_lite.ingest import ingest
    from lexweft_lite.markdown import document_markdown

    f = tmp_path / "doc.md"
    f.write_text("# 硬化性組成物\n\n- 番号: 特許1\n\n## 要約\n\n低誘電正接の硬化物を得る。\n", encoding="utf-8")
    doc_id = ingest(store, str(f))[0].document_id
    md = document_markdown(store, doc_id)
    assert md.count("硬化性組成物") == 2   # 前書きの title: と、本文の先頭の # 題名だけ
    assert "## 要約" in md
