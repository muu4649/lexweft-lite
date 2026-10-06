"""意味層をたどると、Claude が読む量がどれだけ減るかを測る (登録済みのフォルダで).

Claude が受け取る道具の結果 (JSON) の文字数で比べる。

  全部を渡す        : フォルダの全資料の本文 (lw_read_document で全部読んだ場合)
  文字で探す        : lw_search(質問の語) 1 回の結果
  意味層でたどる    : lw_route(質問) + lw_unread 1 回ずつの結果 (段落の抜き出しと理由つき)
  たどって全文を読む: 上に加えて、道案内に出た資料を全部 lw_read_document で読んだ場合

    ./.venv/bin/python scripts/eval_efficiency.py <登録済みのフォルダ> "質問1" "質問2" ... [--out docs/eval/efficiency.md]

正解の資料が決まっていないフォルダでは、届いた資料の良し悪しは測れない (読む量だけを比べる)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("questions", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    from lexweft_lite import clusters as cl
    from lexweft_lite import navigate as nav
    from lexweft_lite import runtime
    from lexweft_lite.markdown import document_markdown
    from lexweft_lite.search import search

    lib = runtime.library()
    st = lib.store_for_folder(args.folder)
    if cl.status(st)["stale"] or not cl.status(st)["built_at"]:
        cl.build(st)
    docs = st.list_documents()
    full = {d["id"]: len(document_markdown(st, d["id"])) for d in docs}
    total = sum(full.values())
    rows = []
    for q in args.questions:
        parts = [w.strip() for w in q.split("|") if w.strip()]
        word = parts[-1] if len(parts) > 1 else parts[0]   # 「質問 | キーワード」ならキーワードで、文字で探す
        s_out = search(st, [word], top_k=10)
        r = nav.route(lib, q, scope=st.key, max_documents=8)
        shown = [d["document_id"] for d in r["documents"]]
        u = nav.unread(lib, q, read_document_ids=shown, limit=8, scope=st.key)
        routed = size(r) + size(u)
        read_all = routed + sum(full.get(d, 0) for d in shown)
        rows.append({"q": q.replace("|", "／"), "word": word, "search": size(s_out), "search_docs": len({h["document_id"] for h in s_out}), "route": routed, "read": read_all,
                     "docs": len(shown), "unread": len(u["unread"]), "found": len(set(shown) | {x["document_id"] for x in u["unread"]})})
    name = args.label or Path(args.folder).name
    pct = lambda n: f"{100 * n / total:.1f}%"
    lines = [f"# Claude が読む量 ({name}: 資料 {len(docs):,} 件)", "",
             f"全部を渡すと **{total:,} 文字**（1 件平均 {total // max(1, len(docs)):,} 文字）。", "",
             "| 質問 | 文字で探す（語・届いた資料） | 意味層でたどる（道具 2 回） | たどって出た資料も全文で読む | たどって届いた資料 |", "|---|---|---|---|---|"]
    for x in rows:
        lines.append(f"| {x['q']} | {x['search']:,} 字（「{x['word']}」{x['search_docs']} 件） | **{x['route']:,} 字（全体の {pct(x['route'])}）** | {x['read']:,} 字（{pct(x['read'])}） | {x['found']} 件 |")
    avg = sum(x["route"] for x in rows) // len(rows)
    lines += ["", f"意味層でたどる 1 回あたり平均 **{avg:,} 文字**（全部を渡す場合の **約 {total // max(1, avg):,} 分の 1**）。",
              "", "- 文字数は、Claude が受け取る道具の結果 (JSON) の長さ",
              "- 意味層でたどる = lw_route（関係するまとまり・資料 8 件・各 2 段落の抜き出しと理由）+ lw_unread（読み残しの候補と確かめる段落）",
              "- 文字で探す = 質問の「|」の後ろの語で lw_search（上位 10 段落）。言い方が違う資料には届かない",
              "- このフォルダには正解の資料が決まっていないので、届いた資料の良し悪しは測っていない（読む量だけの比較）"]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
