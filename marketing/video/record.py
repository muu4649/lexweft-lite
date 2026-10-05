"""ヘッドレス Chrome で LeXWeft Lite を録画する (画面の変化をコマ送りで保存し、あとで動画にする)."""
import asyncio, base64, json, os, subprocess, sys, time, urllib.request
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9335
URL = sys.argv[1]
DEMO = sys.argv[2]


async def main():
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={HERE}/prof",
                             "--hide-scrollbars", "--no-first-run", "--force-color-profile=srgb", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(100):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")) if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.3)
        frames = []
        pending: dict[int, asyncio.Future] = {}
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=100_000_000) as ws:
            n = 0

            async def reader():
                async for raw in ws:
                    msg = json.loads(raw)
                    if "id" in msg and msg["id"] in pending:
                        pending.pop(msg["id"]).set_result(msg)
                    elif msg.get("method") == "Page.screencastFrame":
                        p = msg["params"]
                        i = len(frames)
                        path = os.path.join(HERE, "frames", f"f{i:05d}.jpg")
                        with open(path, "wb") as f:
                            f.write(base64.b64decode(p["data"]))
                        frames.append((p["metadata"]["timestamp"], path))
                        await send("Page.screencastFrameAck", sessionId=p["sessionId"], wait=False)

            async def send(method, wait=True, **params):
                nonlocal n
                n += 1
                fut = asyncio.get_event_loop().create_future()
                pending[n] = fut
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                if not wait:
                    return None
                msg = await fut
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

            task = asyncio.create_task(reader())
            await send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1.5, mobile=False)
            await send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
            await send("Page.enable")
            await send("Page.navigate", url=URL)
            await asyncio.sleep(3)
            await send("Runtime.evaluate", expression=f"window.__DEMO_PATH__ = {json.dumps(DEMO, ensure_ascii=False)}; localStorage.clear(); 1")
            await send("Page.startScreencast", format="jpeg", quality=88, maxWidth=2160, maxHeight=1350, everyNthFrame=1)
            await asyncio.sleep(0.5)
            director = open(os.path.join(HERE, "director.js"), encoding="utf-8").read()
            r = await send("Runtime.evaluate", expression=director, awaitPromise=True, returnByValue=True, timeout=600000)
            if r.get("exceptionDetails"):
                print("director error:", json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
            else:
                print("director:", r.get("result", {}).get("value"))
            await asyncio.sleep(0.8)
            await send("Page.stopScreencast")
            task.cancel()
        # 各コマの表示時間 (次のコマまで) を並べた一覧
        with open(os.path.join(HERE, "frames.txt"), "w") as f:
            for (t, path), nxt in zip(frames, frames[1:] + [(frames[-1][0] + 1.0, None)]):
                f.write(f"file '{path}'\nduration {max(0.001, nxt[0] - t):.4f}\n")
            f.write(f"file '{frames[-1][1]}'\n")
        print(len(frames), "frames,", round(frames[-1][0] - frames[0][0], 1), "seconds")
    finally:
        proc.terminate()

asyncio.run(main())
