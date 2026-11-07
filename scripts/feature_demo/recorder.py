"""Browser recorder driven over stdin (one JSON command per line, one JSON reply per line).

Runs with the system Python that has Playwright. Commands:
  {"cmd": "clip", "name": "c1", "url": "...", "token": "...?"}   new context recording a video
  {"cmd": "goto", "url": "..."}  {"cmd": "eval", "js": "..."}  {"cmd": "wait", "ms": 1000}
  {"cmd": "click", "selector": "..."}  {"cmd": "fill", "selector": "...", "text": "..."}
  {"cmd": "type", "selector": "...", "text": "...", "delay": 60}
  {"cmd": "scroll", "y": 600}  {"cmd": "shot", "path": "..."}  {"cmd": "end"} -> {"video": path}
  {"cmd": "quit"}
"""

import json
import sys

from playwright.sync_api import sync_playwright

W, H = 1600, 900


def main(out_dir):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = page = None
        for line in sys.stdin:
            c = json.loads(line)
            reply = {"ok": True}
            try:
                if c["cmd"] == "clip":
                    ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=out_dir,
                                              record_video_size={"width": W, "height": H}, locale="vi-VN",
                                              timezone_id="Asia/Ho_Chi_Minh")
                    if c.get("token"):
                        ctx.add_init_script(f"try{{localStorage.setItem('auth_token', {json.dumps(c['token'])})}}catch(e){{}}")
                    page = ctx.new_page()
                    page.goto(c["url"], wait_until="domcontentloaded")
                elif c["cmd"] == "goto":
                    page.goto(c["url"], wait_until="domcontentloaded")
                elif c["cmd"] == "eval":
                    reply["value"] = page.evaluate(c["js"])
                elif c["cmd"] == "wait":
                    page.wait_for_timeout(c["ms"])
                elif c["cmd"] == "click":
                    page.click(c["selector"])
                elif c["cmd"] == "fill":
                    page.fill(c["selector"], c["text"])
                elif c["cmd"] == "type":
                    page.click(c["selector"])
                    page.keyboard.type(c["text"], delay=c.get("delay", 60))
                elif c["cmd"] == "scroll":
                    page.mouse.wheel(0, c["y"])
                elif c["cmd"] == "shot":
                    page.screenshot(path=c["path"], type="jpeg", quality=92, full_page=c.get("full", False))
                elif c["cmd"] == "end":
                    video = page.video
                    ctx.close()
                    reply["video"] = video.path() if video else None
                    ctx = page = None
                elif c["cmd"] == "quit":
                    print(json.dumps(reply), flush=True)
                    break
            except Exception as exc:  # report, keep going
                reply = {"ok": False, "error": str(exc)[:400]}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1])
