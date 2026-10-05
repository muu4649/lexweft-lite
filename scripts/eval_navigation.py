"""意味層をたどると、関係する資料にどれだけ届くかを測る (架空の資料で).

同じ話題でも資料ごとに言い方を変えた資料を作り (熱暴走 / 延焼 / 類焼 / 熱連鎖 など)、
1 つの言い方で質問したときに、同じ話題の資料のうち何件に届くかを比べる.

  A. 文字の一致だけ (lw_search に質問の語を 1 つ)
  B. 文字の一致 + 言い換え 2 つ (Claude が思いつきそうな言い換えを足した場合)
  C. 意味層の道案内 (lw_route 1 回)
  D. C + 読み残しの確認 (lw_unread 1 回)
  E. C + 近くをたどる (lw_expand 1 回)

    ./.venv/bin/python scripts/eval_navigation.py [--out docs/eval/navigation.md]
"""

from __future__ import annotations

import argparse
import os
import random
import tempfile
from pathlib import Path

THEMES = {
    "電池の熱対策": dict(
        q="熱暴走を防ぐ方法", alt=["熱暴走 対策", "熱暴走 抑制"],
        variants=[["熱暴走", "延焼", "類焼", "熱連鎖", "発火の連鎖"], ["電池", "蓄電池", "バッテリー", "二次電池"],
                  ["相変化材料", "潜熱蓄熱材", "PCM のシート", "断熱材", "耐熱シート"], ["温度上昇", "発熱の広がり", "温度の上がり方"]],
        ctx=["セル", "モジュール", "隣接セル", "異常発熱", "冷却", "吸熱", "セル間", "パック"]),
    "冷却板の設計": dict(
        q="冷却板の流路設計", alt=["冷却板 流路", "冷却板 冷媒"],
        variants=[["冷却板", "コールドプレート", "液冷プレート", "放熱板"], ["流路", "冷媒通路", "チャネル"],
                  ["圧力損失", "圧損", "流動抵抗"], ["温度ばらつき", "温度差", "温度むら"]],
        ctx=["冷媒", "ポンプ", "熱伝達率", "流速", "入口", "出口", "ピンフィン", "対向流"]),
    "ロボットの把持": dict(
        q="ロボットハンドの把持", alt=["ロボットハンド 把持", "ロボットハンド 物体"],
        variants=[["ロボットハンド", "グリッパ", "エンドエフェクタ", "ロボットの手"], ["把持", "つかむ動作", "ピッキング"],
                  ["触覚センサ", "力覚センサ", "接触センサ"], ["滑り", "スリップ", "ずれ"]],
        ctx=["物体", "指先", "把持力", "物流倉庫", "強化学習", "形状", "柔らかい", "成功率"]),
    "ペロブスカイト太陽電池": dict(
        q="ペロブスカイト太陽電池の耐久性", alt=["ペロブスカイト 耐久", "ペロブスカイト 劣化"],
        variants=[["ペロブスカイト太陽電池", "ペロブスカイト型セル", "PSC"], ["耐久性", "長期安定性", "寿命"],
                  ["封止", "バリア層", "封止材"], ["劣化", "分解", "性能低下"]],
        ctx=["水分", "湿度", "変換効率", "正孔輸送層", "結晶粒", "大面積", "塗布", "タンデム"]),
}
PER_THEME = 15


def make_corpus(root: Path, seed: int = 11) -> dict[str, list[str]]:
    """話題ごとに、言い方を変えた資料を作る. 返り値は 話題 → ファイル名の一覧."""
    rnd = random.Random(seed)
    files: dict[str, list[str]] = {}
    for ti, (theme, t) in enumerate(THEMES.items()):
        files[theme] = []
        for i in range(PER_THEME):
            words = [rnd.choice(v) for v in t["variants"]]
            ctx = rnd.sample(t["ctx"], 5)
            body = (f"# {theme}の検討メモ {i + 1:02d}（架空）\n\n## 背景\n\n{words[1]}の{ctx[0]}では、{ctx[1]}が課題になる。{words[0]}が起きると影響が大きい。\n\n"
                    f"## 課題\n\n{words[0]}を抑えたい。{ctx[2]}と{ctx[3]}の関係を確かめる。{words[3]}も問題になる。\n\n"
                    f"## 解決手段\n\n{words[2]}を使い、{ctx[4]}を改善する。{words[3]}への対策として{words[2]}の配置を変える。\n")
            name = f"{ti}{i:02d}.md"
            (root / name).write_text(body, encoding="utf-8")
            files[theme].append(name)
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="lw-eval-"))
    os.environ["LEXWEFT_HOME"] = str(tmp / "home")
    from lexweft_lite import clusters as cl
    from lexweft_lite import navigate as nav
    from lexweft_lite import runtime
    from lexweft_lite.ingest import ingest
    from lexweft_lite.search import search

    folder = tmp / "資料"
    folder.mkdir()
    files = make_corpus(folder)
    s = runtime.store()
    scope = s.add_source(str(folder))
    ingest(s, str(folder))
    cl.build(s, scope)
    id_of = {Path(d["source"]).name: d["id"] for d in s.list_documents()}
    rows = []
    for theme, t in THEMES.items():
        relevant = {id_of[f] for f in files[theme]}
        # 質問の言い方が入っている資料 (文字の一致で届きうる資料)
        word = t["q"].split("の")[0] if "の" in t["q"] else t["q"]

        def recall(docs) -> str:
            got = set(docs) & relevant
            return f"{len(got)}/{len(relevant)}"

        def prec(docs) -> str:
            docs = set(docs)
            return f"{len(docs & relevant)}/{len(docs)}" if docs else "-"

        a = {h["document_id"] for h in search(s, [t["alt"][0].split()[0]], top_k=10)}
        b = {h["document_id"] for h in search(s, [t["alt"][0].split()[0], *t["alt"]], top_k=10)}
        r = nav.route(s, t["q"], max_documents=8)
        c = {d["document_id"] for res in r["results"] for d in res["documents"]}
        for d in c:
            nav.mark_read(d)
        u = nav.unread(s, t["q"], limit=10)
        d_ = c | {x["document_id"] for res in u["results"] for x in res["unread"]}
        e_ = c | {x["document_id"] for res in nav.expand(s, sorted(c), t["q"], limit=10)["results"] for x in res["documents"]}
        rows.append((theme, t["q"], recall(a), recall(b), recall(c), recall(d_), recall(e_), prec(a), prec(c), prec(d_), prec(e_)))
    lines = ["# 意味層をたどったときの到達 (架空の資料 60 件)", "",
             "同じ話題の 15 件の資料は、言い方を変えて書いてある（例: 熱暴走 / 延焼 / 類焼 / 熱連鎖 / 発火の連鎖）。質問は 1 つの言い方だけを使う。",
             "", "## 届いた資料（同じ話題の資料のうち何件に届いたか）", "",
             "| 話題 | 質問 | A 文字の一致 | B 言い換え 2 つ | C 道案内 1 回 | D C + 読み残し 1 回 | E C + 近く 1 回 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |" for r in rows]
    lines += ["", "## 出てきた資料のうち、同じ話題だったもの", "", "| 話題 | A | C | D | E |", "|---|---|---|---|---|"]
    lines += [f"| {r[0]} | {r[7]} | {r[8]} | {r[9]} | {r[10]} |" for r in rows]
    lines += ["", "- A: lw_search に質問の最初の語を 1 つ（上位 10 段落）", "- B: A + Claude が足しそうな言い換え 2 つ", "- C: lw_route(質問) の上位 8 件",
              "- D: C の 8 件を読んだことにして lw_unread（10 件）", "- E: C の 8 件から lw_expand（10 件）",
              "", "注意: 架空の資料で、言い方の違いを人工的に作っている。実際の資料での効果は別に確かめる必要がある。"]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
