"""配布用 ZIP を作る.

    python scripts/build_release.py --feedback-url https://forms.gle/... [--deny-file 語の一覧.txt]

- git で管理しているファイルのうち、配布に要るものだけを入れる (tests/ と scripts/ は入れない)
- ZIP の中のフォルダ名は版によらず LeXWeftLite (更新で置き換えやすくするため)。ZIP のファイル名に版を付ける
- lexweft_lite/distribution.json は ZIP の中だけ書き換える (感想フォームの URL、channel=test)
- --deny-file を渡すと、その語 (1 行 1 語) が配布物に入っていないか確かめ、入っていれば止める
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = "LeXWeftLite"
INCLUDE_PREFIXES = ("lexweft_lite/", "samples/")
INCLUDE_FILES = {"pyproject.toml", "uv.lock", "install.sh", "install.ps1", "README.md", "START_HERE.html", "TERMS.txt"}
EXECUTABLE = {"install.sh"}


def version() -> str:
    init = (ROOT / "lexweft_lite/__init__.py").read_text(encoding="utf-8")
    v = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    pv = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    if v != pv:
        raise SystemExit(f"版が食い違っています: __init__.py {v} / pyproject.toml {pv}")
    return v


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode("utf-8")
    return sorted(f for f in out.split("\0") if f and (f in INCLUDE_FILES or f.startswith(INCLUDE_PREFIXES)))


def dirty() -> bool:
    return bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True).stdout.strip())


def main() -> int:
    p = argparse.ArgumentParser(description="LeXWeft Lite の配布用 ZIP を作る")
    p.add_argument("--feedback-url", default="", help="感想フォームの URL (https://...)")
    p.add_argument("--deny-file", help="配布物に入っていてはいけない語の一覧 (1 行 1 語)")
    p.add_argument("--out", default=str(ROOT / "dist"))
    p.add_argument("--allow-dirty", action="store_true", help="コミットしていない変更があっても作る")
    args = p.parse_args()

    if args.feedback_url and not args.feedback_url.startswith("https://"):
        raise SystemExit("--feedback-url は https:// で始めてください")
    if dirty() and not args.allow_dirty:
        raise SystemExit("コミットしていない変更があります。コミットしてから作るか、--allow-dirty を付けてください")
    ver = version()
    files = tracked_files()
    missing = sorted(INCLUDE_FILES - set(files))
    if missing:
        raise SystemExit(f"配布に要るファイルが git にありません: {missing}")

    deny = []
    if args.deny_file:
        deny = [w.strip() for w in Path(args.deny_file).expanduser().read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]

    dist = json.dumps({"channel": "test", "feedback_url": args.feedback_url}, ensure_ascii=False, indent=2) + "\n"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"LeXWeftLite-{ver}.zip"
    hits: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            data = dist.encode("utf-8") if f == "lexweft_lite/distribution.json" else (ROOT / f).read_bytes()
            if deny:
                text = data.decode("utf-8", errors="ignore")
                hits += [f"{f}: {w}" for w in deny if w in text]
            info = zipfile.ZipInfo(f"{TOP}/{f}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = ((0o755 if f in EXECUTABLE else 0o644) | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    if hits:
        zip_path.unlink()
        print("配布物に入っていてはいけない語が見つかったので、ZIP を消しました:", file=sys.stderr)
        for h in hits:
            print("  " + h, file=sys.stderr)
        return 1
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"作りました: {zip_path}  ({len(files)} ファイル, {zip_path.stat().st_size / 1024:.0f} KB)")
    print(f"sha256: {digest}")
    print(f"感想フォーム: {args.feedback_url or '(なし。画面に「感想を送る」は出ません)'}")
    if deny:
        print(f"確認した語: {len(deny)} 語、該当なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
