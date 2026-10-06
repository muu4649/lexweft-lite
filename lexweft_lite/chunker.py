"""本文を段落に分ける.

見出しで節に分け、節の中を空行で段落に分ける。1 つの段落は 1 つの段落番号 (¶) になり、割ったりまとめたりしない。
見出しは段落の本文に入れず、段落ごとにも持たせない。見出しは「節の表」(見出し、その節の最初と最後の段落) として別に持つ。
(段落より細かい単位に見出しを受け継がせない、という設計の決まりによる)
"""

from __future__ import annotations

import re

# 分け方を変えたら上げる (取り込み済みの資料を、版が違えば取り込み直す)
CHUNKING_VERSION = "2"

_HEADING_RE = re.compile(r"^(#{1,6}\s+.+|【[^】]{1,30}】\s*|\d+(?:\.\d+)*[.．)]\s+\S.{0,40})$", re.M)

Section = tuple[str, int, int]   # (見出し, 最初の段落の位置, 最後の段落の位置)  位置は 0 始まり


def split_document(text: str) -> tuple[list[str], list[Section]]:
    """本文を (段落の一覧, 節の表) に分ける. 本文の無い節 (見出しだけ) は捨てる."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return [], []
    raw: list[tuple[str, str]] = []
    heads = list(_HEADING_RE.finditer(text))
    if heads:
        if heads[0].start() > 0:
            raw.append(("", text[: heads[0].start()]))
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            raw.append((h.group(0).strip(), text[h.end(): end]))
    else:
        raw.append(("", text))
    paragraphs: list[str] = []
    sections: list[Section] = []
    for head, body in raw:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        if not paras:
            continue
        first = len(paragraphs)
        paragraphs += paras
        if head:
            sections.append((head, first, len(paragraphs) - 1))
    return paragraphs, sections


def from_labeled(pairs: list[tuple[str, str]]) -> tuple[list[str], list[Section]]:
    """取り込み側で分けてある (見出し, 本文) の組 (表の資料など) を、段落と節の表にする. 1 つの組 = 1 つの節."""
    paragraphs: list[str] = []
    sections: list[Section] = []
    for head, body in pairs:
        body = (body or "").strip()
        if not body:
            continue
        paragraphs.append(body)
        if head:
            sections.append((head.strip(), len(paragraphs) - 1, len(paragraphs) - 1))
    return paragraphs, sections


def split_text(text: str) -> list[str]:
    """段落の本文だけを返す."""
    return split_document(text)[0]
