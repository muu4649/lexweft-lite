"""本文の段落分割 (見出しを境界にし、段落優先、文字数ベース、重なり付き)."""

from __future__ import annotations

import re


_HEADING_RE = re.compile(r"^(#{1,6}\s+.+|【[^】]{1,30}】\s*|\d+(?:\.\d+)*[.．)]\s+\S.{0,40})$", re.M)


def split_located(text: str, chunk_size: int = 800, overlap: int = 120) -> list[tuple[str, str]]:
    """(本文, 位置情報) の組で返す.

    位置情報は出典側で安定する値にする。見出しがあれば `head:<見出し>/<節内の通番>`、無ければ `ord:<通番>`。
    再取り込みのとき、この値が同じで本文も同じチャンクは ID を保ったまま残す (引用と言及が生き残る)。
    """
    pairs: list[tuple[str, str]] = []
    for i, c in enumerate(_split_with_heads(text, chunk_size, overlap)):
        head, body = c
        pairs.append((body, f"head:{head}/{i}" if head else f"ord:{i}"))
    return pairs


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """本文だけを返す (従来互換)."""
    return [t for t, _ in split_located(text, chunk_size, overlap)]


def _split_with_heads(text: str, chunk_size: int = 800, overlap: int = 120) -> list[tuple[str, str]]:
    """見出しを硬い境界にして節ごとに分割し、各節を段落優先で chunk_size に収める.

    レポート・論文は節が意味の単位なので、節をまたいで混ぜない。節が複数チャンクに割れたら見出しを各チャンク先頭に付ける。
    戻り値は (見出し, 本文) の組。
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    sections: list[tuple[str, str]] = []
    pos = 0
    heads = list(_HEADING_RE.finditer(text))
    if len(heads) >= 2:
        for i, h in enumerate(heads):
            body_start = h.end()
            body_end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            if i == 0 and h.start() > 0:
                sections.append(("", text[: h.start()]))
            sections.append((h.group(0).strip(), text[body_start:body_end]))
        out: list[tuple[str, str]] = []
        for head, body in sections:
            parts = _split_paragraphs(body, chunk_size, overlap)
            if not parts and head:
                parts = [""]
            for part in parts:
                whole = (head + "\n" + part).strip() if head else part
                if whole.strip():
                    out.append((head, whole))
        return out
    return [("", c) for c in _split_paragraphs(text, chunk_size, overlap)]


def _split_paragraphs(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for p in paragraphs:
        if len(p) <= chunk_size:
            units.append(p)
        else:
            # 長い段落は文単位に割る (日本語の句点と英語のピリオドの両方)
            sentences = re.split(r"(?<=[。．！？!?\.])\s*", p)
            buf = ""
            for s in sentences:
                if not s:
                    continue
                if len(buf) + len(s) > chunk_size and buf:
                    units.append(buf)
                    buf = s
                else:
                    buf += s
            if buf:
                units.append(buf)

    chunks: list[str] = []
    buf = ""
    for u in units:
        if len(buf) + len(u) + 2 > chunk_size and buf:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = (tail + "\n" + u) if tail else u
        else:
            buf = (buf + "\n\n" + u) if buf else u
    if buf:
        chunks.append(buf)
    # 極端に長い単一ユニット (改行なしの巨大テキスト) を強制分割
    out: list[str] = []
    for c in chunks:
        while len(c) > chunk_size * 2:
            out.append(c[:chunk_size])
            c = c[chunk_size - overlap :]
        out.append(c)
    return out
