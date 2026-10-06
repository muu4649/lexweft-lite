"""紹介動画 (プレゼン形式) を作る: slides.html の各スライドを写真にし、プレイ動画の場面をはめ込んでつなぐ.

    python marketing/video/build_intro.py <プレイ動画.mp4> <出力.mp4>

プレイ動画は架空の資料で撮ったもの (marketing/LeXWeftLite_プレイ動画.mp4) を使う。
場面の時刻 (CLIPS) は、そのプレイ動画に合わせてある。台本を変えて撮り直したら、時刻も見直す。
必要なもの: Google Chrome、ffmpeg、Python の websockets
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H = 1920, 1080
CLIP_BOX = (240, 162, 1440, 900)   # 映像をはめる枠 (x, y, 幅, 高さ). slides.html の .frame に合わせる
FADE = 0.3

# (スライド番号, 秒数) または (スライド番号, [(始め, 終わり), ...] = プレイ動画のはめ込む場面)
PLAN: list[tuple[int, float | list[tuple[float, float]]]] = [
    (1, 4.0),
    (2, 8.0),
    (3, 7.0),
    (4, [(13.0, 21.0)]),                 # 資料を入れるフォルダを決めて取り込む
    (5, [(40.0, 47.5)]),                 # 地図とまとまり
    (6, [(56.0, 61.5), (63.0, 72.0)]),   # グループの色分け → つながり図
    (7, [(110.0, 117.0), (119.0, 124.0)]),   # 意味層でたどる検索 → 読み残し
    (8, 8.0),
    (9, 9.5),
    (13, 9.0),   # ユースケース 4 つ
    (14, 10.0),  # 用途探索の例
    (15, 8.5),   # 取りこぼさない例
    (10, 6.0),
    (12, 7.0),
]


async def _shoot(out_dir: Path, numbers: list[int]) -> None:
    port = 9337
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={out_dir}/prof", "--hide-scrollbars",
                             "--no-first-run", "--force-color-profile=srgb", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import websockets

    try:
        page = None
        for _ in range(200):   # 新しいプロファイルだと、起動に 15 秒ほどかかることがある
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if t["type"] == "page")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        if page is None:
            raise SystemExit("Chrome が起動しませんでした")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=100_000_000) as ws:
            n = 0

            async def send(method: str, **params):
                nonlocal n
                n += 1
                my = n
                await ws.send(json.dumps({"id": my, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == my:
                        return m.get("result", {})

            await send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
            await send("Page.navigate", url=(HERE / "slides.html").as_uri())
            await asyncio.sleep(2.0)
            for s in numbers:
                r = await send("Runtime.evaluate", expression=f"show({s})", returnByValue=True)
                if str(r.get("result", {}).get("value")) != str(s):
                    raise SystemExit(f"スライド {s} を表示できませんでした")
                await asyncio.sleep(0.5)
                shot = await send("Page.captureScreenshot", format="png")
                (out_dir / f"slide{s:02d}.png").write_bytes(base64.b64decode(shot["data"]))
    finally:
        proc.terminate()


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    video, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    work = Path(tempfile.mkdtemp(prefix="lw-intro-"))
    asyncio.run(_shoot(work, [s for s, _ in PLAN]))
    enc = ["-c:v", "libx264", "-preset", "slow", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p"]
    parts = []
    for i, (s, what) in enumerate(PLAN):
        img = work / f"slide{s:02d}.png"
        seg = work / f"seg{i:02d}.mp4"
        if isinstance(what, (int, float)):
            dur = float(what)
            _run(["-loop", "1", "-t", f"{dur}", "-framerate", "30", "-i", str(img),
                  "-vf", f"format=yuv420p,fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE}:d={FADE}", *enc, str(seg)])
        else:
            x, y, w, h = CLIP_BOX
            dur = sum(b - a for a, b in what)
            trims = "".join(f"[1:v]trim={a}:{b},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos,setsar=1[c{k}];" for k, (a, b) in enumerate(what))
            joined = "".join(f"[c{k}]" for k in range(len(what))) + f"concat=n={len(what)}:v=1:a=0[clip];"
            _run(["-loop", "1", "-t", f"{dur}", "-framerate", "30", "-i", str(img), "-i", str(video),
                  "-filter_complex", trims + joined + f"[0:v][clip]overlay={x}:{y}:shortest=1,format=yuv420p,"
                  f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE}:d={FADE}[v]", "-map", "[v]", "-t", f"{dur}", *enc, str(seg)])
        parts.append(seg)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
    print("作りました:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
