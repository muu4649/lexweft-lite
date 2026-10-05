"""コマンド: lexweft serve / mcp / add / status / export / mcp-config."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, config, runtime


def _claude_desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        import os

        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def command_path() -> str:
    """lexweft コマンドの絶対パス (仮想環境の中を優先)."""
    for name in ("lexweft", "lexweft.exe"):
        exe = Path(sys.executable).parent / name
        if exe.exists():
            return str(exe)
    return shutil.which("lexweft") or "lexweft"


def server_entry() -> dict:
    return {"command": command_path(), "args": ["mcp"], "env": {"LEXWEFT_HOME": str(config.home())}}


def cmd_mcp_config(args: argparse.Namespace) -> int:
    entry = {"mcpServers": {"lexweft-lite": server_entry()}}
    if not args.write:
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        print(f"\nClaude Desktop の設定ファイル: {_claude_desktop_config_path()}", file=sys.stderr)
        print("この内容を mcpServers に足すか、`lexweft mcp-config --write` で書き込めます。", file=sys.stderr)
        return 0
    path = _claude_desktop_config_path()
    current: dict = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            print(f"設定ファイルが JSON として読めないので書き込みません: {path}", file=sys.stderr)
            return 1
    if not args.yes:
        ans = input(f"{path} に lexweft-lite を足します (元のファイルは .bak に残します)。よろしいですか [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("書き込みませんでした。")
            return 0
    if path.exists():
        backup = path.with_suffix(f".json.{datetime.now():%Y%m%d%H%M%S}.bak")
        shutil.copy2(path, backup)
        print(f"元の設定を残しました: {backup}")
    current.setdefault("mcpServers", {})["lexweft-lite"] = server_entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"書き込みました: {path}\n"
          "次に、Claude Desktop をいったん終了して (Mac は ⌘Q) 開き直し、新しいチャットで「LeXWeft Lite に入っている資料は何件？」と聞いてください。\n"
          "件数が返ってくれば、つながっています。")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve

    serve(port=args.port, open_browser=not args.no_browser, ready_file=args.ready_file, parent_pid=args.parent_pid)
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    from .mcp_server import main

    main()
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """取り込む. 登録したフォルダの中のファイルはそのフォルダの保存先へ、それ以外はアプリ側へ. --register でフォルダを登録してから取り込む."""
    from .ingest import ingest

    lib = runtime.library()
    code = 0
    for target in args.targets:
        try:
            if getattr(args, "register", False) and Path(target).expanduser().is_dir():
                row = lib.register(target)
                target = row["path"]
                print(f"登録しました: {target}  (意味層の保存先: {'フォルダの中の _LeXWeft' if row['location'] == 'folder' else 'アプリ側'})")
            st = lib.catalog if target.startswith(("http://", "https://")) else lib.for_source(target)
            for r in ingest(st, target, st.markdown_dir):
                print(f"{r.status:9s} #{r.document_id}  {r.title}  ({r.paragraphs} 段落)")
            if st is not lib.catalog:
                from .clusters import build
                from .jobs import scan_signature

                lib.touch(st.key, scan_signature(st.key))
                build(st)
        except (FileNotFoundError, ValueError) as e:
            print(f"取り込めません: {target}: {e}", file=sys.stderr)
            code = 1
    return code


def cmd_status(_: argparse.Namespace) -> int:
    from .clusters import status

    lib = runtime.library()
    print(f"LeXWeft Lite {__version__}  アプリ側の保存先 {config.home()}")
    for st in lib.stores():
        s = st.stats()
        if st is lib.catalog and not s["documents"]:
            continue
        lay = status(st)
        where = st.path.rsplit("/", 1)[0]
        print(f"\n[{st.label}]  ({where})")
        print(f"  資料 {s['documents']} / 段落 {s['paragraphs']} / まとまり {lay['clusters']} (版 {lay['version']}) / 概念 {s['concepts']} / 関係 {s['relations']}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """前の版で入ってしまった、隠しフォルダ・開発用のフォルダ・ライセンス文などの資料を消す (元のファイルは消さない)."""
    from collections import Counter

    from .loaders import excluded_by_rules
    from .markdown import remove_document_file

    s = runtime.store()
    targets = [(int(r["id"]), r["source"]) for r in s.conn.execute("SELECT id, source FROM documents")
               if not r["source"].startswith(("text:", "http://", "https://", str(config.home() / "files")))
               and excluded_by_rules(r["source"])]
    total = s.count_documents()
    print(f"資料 {total} 件のうち、規則に当たるもの {len(targets)} 件")
    groups = Counter(str(Path(src).parent)[:90] for _, src in targets)
    for folder, n in groups.most_common(15):
        print(f"  {n:6d}  {folder}")
    if not targets:
        return 0
    if not args.yes:
        ans = input("これらを LeXWeft Lite から消します (元のファイルは消しません)。よろしいですか [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("消しませんでした。")
            return 0
    ids = [i for i, _ in targets]
    with s.tx() as c:
        for k in range(0, len(ids), 500):
            chunk = ids[k:k + 500]
            c.execute(f"DELETE FROM documents WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    for i in ids:
        remove_document_file(i, runtime.markdown_dir())
    print(f"{len(ids)} 件を消しました。残り {s.count_documents()} 件。索引を詰めています…")
    s.compact()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .concepts import layer_markdown
    from .markdown import write_document_file

    lib = runtime.library()
    out = Path(args.out).expanduser() if args.out else config.home() / "export"
    out.mkdir(parents=True, exist_ok=True)
    for st in lib.stores():
        sub = out / "documents" / (st.label.replace("/", "_") if st is not lib.catalog else "フォルダの外")
        for d in st.list_documents():
            write_document_file(st, d["id"], sub)
    (out / "layer.md").write_text(layer_markdown(lib), encoding="utf-8")
    print(f"書き出しました: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lexweft", description="LeXWeft Lite: 資料の蓄積、Markdown 変換、意味層、可視化、MCP")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="画面を開く (http://127.0.0.1:8765/)")
    sp.add_argument("--port", type=int, default=None, help="使う番号 (省略すると 8765 から空いている番号)")
    sp.add_argument("--no-browser", action="store_true")
    sp.add_argument("--ready-file", help=argparse.SUPPRESS)   # Mac アプリ用: 空いているポートで開き、URL をこのファイルに書く
    sp.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_serve)
    sub.add_parser("mcp", help="MCP サーバー (stdio) として動く").set_defaults(func=cmd_mcp)
    sp = sub.add_parser("add", help="ファイル・フォルダ・URL を取り込む")
    sp.add_argument("targets", nargs="+")
    sp.add_argument("--register", action="store_true", help="フォルダを登録してから取り込む (意味層をフォルダごとに作る)")
    sp.set_defaults(func=cmd_add)
    sub.add_parser("status", help="件数を見る").set_defaults(func=cmd_status)
    sp = sub.add_parser("prune", help="隠しフォルダ・開発用のフォルダ・ライセンス文などの資料を消す (元のファイルは消さない)")
    sp.add_argument("--yes", action="store_true", help="確認を省く")
    sp.set_defaults(func=cmd_prune)
    sp = sub.add_parser("export", help="資料ごとの Markdown と意味層 (layer.md) を書き出す")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_export)
    sp = sub.add_parser("mcp-config", help="Claude Desktop に登録する設定を表示する (--write で書き込む)")
    sp.add_argument("--write", action="store_true")
    sp.add_argument("--yes", action="store_true", help="確認を省く")
    sp.set_defaults(func=cmd_mcp_config)
    args = p.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
