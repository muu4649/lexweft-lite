"""紹介動画 (プレゼン形式) を作る: slides.html の各スライドを写真にし、プレイ動画の場面をはめ込んでつなぐ.

    python marketing/video/build_intro.py <プレイ動画.mp4> <出力.mp4> [--voice-dir 声のフォルダ] [--music]

--voice-dir: スライドごとの説明音声 slide<番号>.wav / .mp3 / .m4a / .aiff を重ねる (台本は narration.json)。
             読み上げがスライドより長ければ、そのスライドを延ばす (映像は最後のコマを止める)
--music    : make_music.py で背景音楽を合成して重ねる (声が入っている間は音楽を下げる)

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


def _length(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _voice(voice_dir: Path | None, s: int) -> Path | None:
    if voice_dir is None:
        return None
    for ext in (".wav", ".mp3", ".m4a", ".aiff"):
        f = voice_dir / f"slide{s}{ext}"
        if f.exists():
            return f
    return None


VOICE_LEAD = 0.35   # スライドが出てから声が始まるまで
VOICE_TAIL = 0.6    # 声が終わってから次のスライドまで


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--voice-dir")
    ap.add_argument("--music", action="store_true")
    args = ap.parse_args()
    video, out = Path(args.video).resolve(), Path(args.out).resolve()
    voice_dir = Path(args.voice_dir).resolve() if args.voice_dir else None
    work = Path(tempfile.mkdtemp(prefix="lw-intro-"))
    asyncio.run(_shoot(work, [s for s, _ in PLAN]))
    enc = ["-c:v", "libx264", "-preset", "slow", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p"]
    parts, durs, voices = [], [], []
    for i, (s, what) in enumerate(PLAN):
        img = work / f"slide{s:02d}.png"
        seg = work / f"seg{i:02d}.mp4"
        base = float(what) if isinstance(what, (int, float)) else sum(b - a for a, b in what)
        v = _voice(voice_dir, s)
        dur = max(base, VOICE_LEAD + _length(v) + VOICE_TAIL) if v else base
        durs.append(dur)
        voices.append(v)
        if isinstance(what, (int, float)):
            _run(["-loop", "1", "-t", f"{dur}", "-framerate", "30", "-i", str(img),
                  "-vf", f"format=yuv420p,fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE}:d={FADE}", *enc, str(seg)])
        else:
            x, y, w, h = CLIP_BOX
            trims = "".join(f"[1:v]trim={a}:{b},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos,setsar=1[c{k}];" for k, (a, b) in enumerate(what))
            hold = f",tpad=stop_mode=clone:stop_duration={dur - base:.3f}" if dur > base else ""   # 声が長いときは最後のコマを止めて延ばす
            joined = "".join(f"[c{k}]" for k in range(len(what))) + f"concat=n={len(what)}:v=1:a=0{hold}[clip];"
            _run(["-loop", "1", "-t", f"{dur}", "-framerate", "30", "-i", str(img), "-i", str(video),
                  "-filter_complex", trims + joined + f"[0:v][clip]overlay={x}:{y}:shortest=1,format=yuv420p,"
                  f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE}:d={FADE}[v]", "-map", "[v]", "-t", f"{dur}", *enc, str(seg)])
        parts.append(seg)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    if not voice_dir and not args.music:
        _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])
        print("作りました:", out)
        return 0
    silent = work / "video.mp4"
    _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])
    total = sum(durs)
    # 声の音声: スライドごとに、声 (少し遅らせる) か無音を、スライドの長さにそろえてつなぐ
    vparts = []
    for i, (dur, v) in enumerate(zip(durs, voices)):
        a = work / f"voice{i:02d}.wav"
        if v:
            _run(["-i", str(v), "-af", f"aresample=44100,aformat=channel_layouts=stereo,adelay={int(VOICE_LEAD * 1000)}:all=1,apad,atrim=0:{dur:.3f}", str(a)])
        else:
            _run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{dur:.3f}", str(a)])
        vparts.append(a)
    vlist = work / "voice.txt"
    vlist.write_text("".join(f"file '{p}'\n" for p in vparts), encoding="utf-8")
    voice = work / "voice.wav"
    _run(["-f", "concat", "-safe", "0", "-i", str(vlist), "-c", "copy", str(voice)])
    inputs = ["-i", str(silent), "-i", str(voice)]
    if args.music:
        music = work / "music.wav"
        subprocess.run([sys.executable, str(HERE / "make_music.py"), f"{total:.2f}", str(music)], check=True)
        inputs += ["-i", str(music)]
        # 声が入っている間は音楽を下げる (sidechain)。全体の音量は配信向けにそろえる
        mix = ("[2:a]volume=0.5[m];[1:a]asplit=2[v1][v2];[m][v1]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[md];"
               "[md][v2]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        mix = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    _run([*inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
          "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out)])
    print("作りました:", out, f"({total:.1f} 秒)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
