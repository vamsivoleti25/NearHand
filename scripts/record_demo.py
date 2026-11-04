"""Drive the Nearhand demo in a visible browser, at a steady pace, with captions, so it can be screen-recorded.

    pip install playwright
    python -m playwright install chromium      # once (skip if Google Chrome is installed)
    python scripts/record_demo.py              # then start the Mac screen recorder during the countdown

Options:
    --url URL       the app to drive (default: the live app)
    --pace 1.0      >1 slows every pause down, <1 speeds it up
    --no-captions   no captions
    --caption-seconds 5   longest a caption stays on screen

It clicks through the real app with the real model; nothing on screen is faked. Each run is a fresh, private
copy of the demo data, so it can be run as many times as needed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Page, TimeoutError as PWTimeout, async_playwright

LIVE = "https://nearhand.streamlit.app/~/+/"   # the app itself, without Streamlit Cloud's outer frame
TEAM = {"Ana", "Chris", "Diego", "Elena", "Jordan", "Kai", "Leo", "Lucia", "Maya", "Mei", "Nia", "Omar", "Priya",
        "Ravi", "Sam", "Tom"}   # Bean & Co.'s own staff (the rest of the phone row are part-timers)

# A caption bar and a click ripple, added to every page the script opens.
OVERLAY = """
(() => {
  if (window.__nhCaption || !document.documentElement) return;
  const css = document.createElement('style');
  css.textContent = `
    #nh-cap {position:fixed; z-index:2147483647; padding:12px 20px; border-radius:14px;
      background:rgba(17,24,39,.93); color:#fff; font:600 19px/1.35 Inter, system-ui, sans-serif;
      box-shadow:0 10px 30px rgba(0,0,0,.25); transition:opacity .35s; pointer-events:none; opacity:0}
    /* normal pages: a strip just under the top bar; the page is nudged down so it covers nothing */
    #nh-cap.top {left:50%; top:74px; transform:translateX(-50%); max-width:min(1100px, 92vw); text-align:center}
    /* phone view: a card to the left of the phone */
    #nh-cap.side {left:32px; top:50%; transform:translateY(-50%); max-width:min(380px, calc(50vw - 240px));
      text-align:left; font-size:21px}
    .block-container:not(:has(.st-key-nh-viewas)) {padding-top:calc(2.2rem + 64px) !important}
    #nh-cap b {color:#C4B5FD}
    #nh-end {position:fixed; inset:0; z-index:2147483646; display:none; align-items:center; justify-content:center;
      flex-direction:column; gap:14px; background:#fff; font-family:Inter, system-ui, sans-serif}
    #nh-end h1 {font-size:64px; margin:0; color:#111827} #nh-end p {font-size:26px; margin:0; color:#4B5563}
    #nh-end .u {color:#6D28D9; font-weight:700}
    #nh-end img {width:96px; height:96px}
    .nh-ripple {position:fixed; width:56px; height:56px; margin:-28px 0 0 -28px; border-radius:50%;
      border:4px solid #7C3AED; background:rgba(124,58,237,.25); pointer-events:none; z-index:2147483647;
      animation:nhr .7s ease-out forwards}
    @keyframes nhr {from {transform:scale(.3); opacity:1} to {transform:scale(1.8); opacity:0}}
    #nh-cursor {position:fixed; left:0; top:0; width:30px; height:30px; margin:-3px 0 0 -3px;
      pointer-events:none; z-index:2147483647; transition:transform .08s linear; display:none;
      filter:drop-shadow(0 2px 4px rgba(0,0,0,.45))}
    .nh-ring {position:fixed; border:3px solid #7C3AED; border-radius:14px; pointer-events:none;
      z-index:2147483646; box-shadow:0 0 0 6px rgba(124,58,237,.28), 0 0 24px rgba(124,58,237,.55);
      animation:nhp 1.1s ease-in-out infinite alternate}
    @keyframes nhp {from {box-shadow:0 0 0 4px rgba(124,58,237,.22), 0 0 12px rgba(124,58,237,.4)}
                    to {box-shadow:0 0 0 10px rgba(124,58,237,.30), 0 0 30px rgba(124,58,237,.65)}}`;
  document.documentElement.appendChild(css);
  const cap = document.createElement('div'); cap.id = 'nh-cap'; document.documentElement.appendChild(cap);
  const end = document.createElement('div'); end.id = 'nh-end'; document.documentElement.appendChild(end);
  window.__nhCaption = (html, maxMs) => {          // shown for at most maxMs, then it fades
    clearTimeout(window.__nhT);
    cap.className = document.querySelector('.st-key-nh-viewas') ? 'side' : 'top';
    cap.innerHTML = html || ''; cap.style.opacity = html ? 1 : 0;
    if (html) window.__nhT = setTimeout(() => { cap.style.opacity = 0; }, maxMs || 5000);
  };
  window.__nhEnd = (html) => { end.innerHTML = html; end.style.display = html ? 'flex' : 'none'; };
  const cur = document.createElement('div'); cur.id = 'nh-cursor';   // an arrow pointer, tip at the click point
  cur.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M3 2 L3 19 L8 14.5 L11.5 22 L14.5 20.6 '
    + 'L11 13.2 L17.5 13.2 Z" fill="#fff" stroke="#111827" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.documentElement.appendChild(cur);
  window.__nhRing = (x, y, w, h) => {               // a glowing outline around what's about to be clicked
    document.querySelectorAll('.nh-ring').forEach(e => e.remove());
    const r = document.createElement('div'); r.className = 'nh-ring';
    Object.assign(r.style, {left: (x - 7) + 'px', top: (y - 7) + 'px', width: (w + 14) + 'px', height: (h + 14) + 'px'});
    document.documentElement.appendChild(r); setTimeout(() => r.remove(), 1600);
  };
  if (window.__nhRipple) return; window.__nhRipple = true;
  document.addEventListener('mousemove', (e) => {
    const c = document.getElementById('nh-cursor'); if (!c) return;
    c.style.display = 'block'; c.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);
  document.addEventListener('mousedown', (e) => {
    document.querySelectorAll('.nh-ring').forEach(x => x.remove());   // the glow goes once it's clicked
    const r = document.createElement('div'); r.className = 'nh-ripple';
    r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    document.documentElement.appendChild(r); setTimeout(() => r.remove(), 800);
  }, true);
})();
"""


class Demo:
    def __init__(self, page: Page, pace: float, captions: bool, caption_s: float = 5.0):
        self.p, self.pace, self.captions, self.caption_ms = page, pace, captions, int(caption_s * 1000)
        self.t0, self.marks = None, []

    async def sync(self) -> None:
        """A half-second magenta flash: the editor finds it in the recording and lines the voice up from there."""
        await self.p.evaluate("""() => { const f = document.createElement('div'); f.id = 'nh-sync';
            f.style.cssText = 'position:fixed;inset:0;background:#FF00FF;z-index:2147483647';
            document.documentElement.appendChild(f); }""")
        await self.p.wait_for_timeout(120)
        self.t0 = time.monotonic()
        await self.p.wait_for_timeout(500)
        await self.p.evaluate("() => document.getElementById('nh-sync')?.remove()")

    def mark(self, beat: str) -> None:
        """When a narration beat starts (seconds after the sync flash)."""
        if self.t0 is not None:
            self.marks.append({"beat": beat, "t": round(time.monotonic() - self.t0, 2)})

    def save_timeline(self) -> None:
        out = Path(__file__).resolve().parent.parent / "video" / "timeline.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"marks": self.marks, "end": round(time.monotonic() - self.t0, 2)
                                   if self.t0 else None}, indent=1))
        print(f"  timeline saved: {out}")

    async def pause(self, seconds: float) -> None:
        await self.p.wait_for_timeout(int(seconds * 1000 * self.pace))

    async def busy(self) -> bool:
        return bool(await self.p.locator('[data-testid="stStatusWidget"], [data-testid="stSpinner"]').count()
                    or await self.p.get_by_text("is working on it", exact=False).count())

    async def idle(self, at_least: float = 1.0, limit: float = 120) -> None:
        """Wait until Streamlit has finished: no 'running' widget, no spinner, quiet for a moment."""
        await self.p.wait_for_timeout(int(at_least * 1000))
        waited, quiet = 0.0, 0
        while waited < limit and quiet < 3:
            quiet = 0 if await self.busy() else quiet + 1
            await self.p.wait_for_timeout(500)
            waited += 0.5

    async def say(self, html: str, hold: float = 0) -> None:
        if self.captions:
            await self.p.evaluate(OVERLAY)
            await self.p.evaluate("([h, ms]) => window.__nhCaption(h, ms)", [html, self.caption_ms])
        if hold:
            await self.pause(hold)

    async def click(self, name: str, *, role: str = "button", exact: bool = False, timeout: float = 30) -> bool:
        target = (self.p.get_by_text(name, exact=exact) if role == "text"
                  else self.p.get_by_role(role, name=name, exact=exact)).first
        try:
            await target.wait_for(state="visible", timeout=timeout * 1000)
        except PWTimeout:
            print(f"  (skipped: no '{name}' on screen)")
            return False
        await target.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")  # never under a caption
        await self.pause(0.6)
        box = await target.bounding_box()
        if box:   # the cursor glides over, the button glows, then the click (with a ripple)
            await self.p.evaluate(OVERLAY)
            # the arrow's tip just below the label, so the button text stays readable
            await self.p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.74, steps=22)
            await self.p.evaluate("([x, y, w, h]) => window.__nhRing(x, y, w, h)",
                                  [box["x"], box["y"], box["width"], box["height"]])
            await self.pause(0.75)
        await target.click()
        await self.p.wait_for_timeout(250)
        return True

    async def type_in(self, placeholder: str, text: str) -> bool:
        box = self.p.get_by_placeholder(placeholder).first
        try:
            await box.wait_for(state="visible", timeout=8000)
        except PWTimeout:
            print(f"  (skipped: no '{placeholder}' box)")
            return False
        await box.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
        await self.pause(0.5)
        b = await box.bounding_box()
        if b:
            await self.p.mouse.move(b["x"] + 40, b["y"] + b["height"] / 2, steps=18)
            await self.p.evaluate("([x, y, w, h]) => window.__nhRing(x, y, w, h)", [b["x"], b["y"], b["width"], b["height"]])
        await box.click()
        await box.type(text, delay=55)
        await self.pause(0.4)
        return True

    async def card(self, html: str, seconds: float) -> None:
        """A full-screen title card (the intro and the close)."""
        await self.p.evaluate(OVERLAY)
        await self.p.evaluate("(h) => window.__nhEnd(h)", html)
        await self.pause(seconds)

    async def scroll(self, pixels: int, steps: int = 8) -> None:
        """A smooth scroll, the way a person would, inside whatever part of the page scrolls."""
        await self.p.mouse.move(700, 450)
        for _ in range(steps):
            await self.p.mouse.wheel(0, pixels / steps)
            await self.p.wait_for_timeout(60)
        await self.pause(0.4)

    async def top(self) -> None:
        await self.p.evaluate("document.querySelectorAll('[data-testid=stMain], .block-container')"
                              ".forEach(e => e.scrollTo({top: 0, behavior: 'smooth'}))")
        await self.pause(0.8)

    async def phone_names(self) -> list[str]:
        names = await self.p.locator(".st-key-nh-viewas-row button").all_inner_texts()
        return list(dict.fromkeys(n.replace("→", "").strip() for n in names if n.strip()))


async def run(url: str, pace: float, captions: bool, headless: bool = False, video: str | None = None,
              caption_s: float = 5.0) -> None:
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(channel="chrome", headless=headless,
                                               args=["--window-size=1440,960", "--window-position=0,0"])
        except Exception:
            browser = await pw.chromium.launch(headless=headless,
                                               args=["--window-size=1440,960", "--window-position=0,0"])
        if headless:   # for testing the script without a screen
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=video,
                                            record_video_size={"width": 1440, "height": 900})
        else:
            ctx = await browser.new_context(no_viewport=True)
        await ctx.add_init_script(OVERLAY)
        page = await ctx.new_page()
        d = Demo(page, pace, captions, caption_s)

        print("Opening the app (it can take ~30 s to wake up)…")
        base = url.split("/~/+")[0].rstrip("/")
        if base.endswith(".streamlit.app"):
            # Streamlit Cloud shows the app inside a frame on its own page. Load that page first (it wakes the
            # app and sets the cookies the app needs), then open the app itself full-window.
            await page.goto(base + "/")
            # the app's own frame (the outer page also holds other frames, e.g. a status-page notice)
            frame = page.locator('iframe[src*="/~/+"], iframe[title="streamlitApp"]').first
            for _ in range(240):                                # up to 2 minutes
                wake = page.get_by_role("button", name="Yes, get this app back up", exact=False)
                if await wake.count():
                    print("  waking the app up…")
                    await wake.first.click()
                if await frame.count():
                    break
                await page.wait_for_timeout(500)
            src = await frame.get_attribute("src") if await frame.count() else None
            if not src or "/~/+" not in src:
                src = "/~/+/"
            if not src.startswith(("http", "/")) and src.split("/")[0].count("."):
                src = "https://" + src                          # "host/~/+/" written without its scheme
            inner = urljoin(page.url, src)                      # handles "//host/…", "/…" and full addresses
            if "/~/+" not in inner or not inner.startswith("http"):
                inner = base + "/~/+/"
            await page.wait_for_timeout(3000)                   # let the app inside the frame start
            await page.goto(inner)
        else:
            await page.goto(url)
        start = page.get_by_role("button", name="I need staff").first
        for _ in range(240):                                    # up to 2 minutes
            if await start.is_visible():
                break
            await page.wait_for_timeout(500)
        else:
            sys.exit("The app didn't load. Open https://nearhand.streamlit.app in your browser, wait until it shows "
                     "the home page, then run this again. (Page: " + page.url + ")")
        await d.idle(2)
        for n in range(5, 0, -1):   # time to start the screen recorder
            await d.say(f"Starting in {n}… (start your screen recording now)")
            print(f"  starting in {n}")
            await page.wait_for_timeout(1000)
        await d.say("")

        logo = Path(__file__).resolve().parent.parent / "assets" / "logo.png"   # the Muse Image logo
        img = (f"<img src='data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}'>"
               if logo.exists() else "")
        title = img + "<h1>Nearhand</h1><p>Your week, scheduled. Your gaps, filled from down the street.</p>"

        # 0:00-0:15 · Nearhand, and the problem ----------------------------------------------------------
        await d.sync()
        d.mark("intro")
        await d.card(title + "<p>Scheduling for small businesses, with an AI agent that fills open shifts "
                             "from the neighborhood.</p>", 4)
        await d.card("", 0)
        d.mark("problem")
        await d.say("It's Thursday morning. <b>Chris just called out sick</b>, and a café owner has to text around "
                    "to fill the shift.", 4.5)
        await d.say("<b>Nearhand</b> does that in one click. Here's a café owner's week.", 3)

        # 0:15-0:40 · Rules ------------------------------------------------------------------------------
        d.mark("rules")
        await d.click("I need staff"); await d.idle(2)
        await d.say("The owner types this week's rules <b>the way they'd say them</b>.", 3)
        await d.click("Simplify rules"); await d.idle(2)
        await d.say("<b>Muse Spark Open</b> turns them into rules the owner can check and untick.", 3.5)
        d.mark("schedule")
        await d.click("Build schedule"); await d.idle(2)
        await d.say("<b>OR-Tools</b> builds the cheapest week that follows every rule. 4 shifts are still open.", 1)
        await d.scroll(1300); await d.pause(3)

        # 0:40-1:15 · The agent --------------------------------------------------------------------------
        d.mark("agent")
        await d.say("One click: the <b>AI agent</b> fills them, asking the café's own team first, then people nearby.")
        await d.click("Fill gaps with AI"); await d.idle(3, limit=150)
        await d.scroll(900)
        d.mark("trace")
        await d.say("A plan, and exactly what it adds to the week.", 3)
        if await d.click("How the agent got here", role="text", timeout=8):
            await d.idle(1); await d.scroll(500)
            await d.say("Its steps: it asks the team first, tries what-ifs, skips the ones that cost more, and "
                        "<b>every number is checked</b> against the optimizer.", 5)
        await d.say("The owner approves. <b>Nothing is booked without a yes.</b>", 1)
        await d.click("Send offers"); await d.idle(2)
        await d.top(); await d.pause(1)

        # 1:15-1:50 · Both sides of the handoff ----------------------------------------------------------
        names = await d.phone_names()
        print("  offers went to:", ", ".join(names))
        team = [n for n in names if n in TEAM]
        no = "Lucia" if "Lucia" in names else (team[0] if team else None)
        if no:
            d.mark("decline")
            await d.say(f"Each person sees the offer on their phone. <b>{no}</b> can't make it.", 1)
            await d.click(f"{no} →"); await d.idle(2); await d.pause(2)
            await d.click("Decline"); await d.idle(1.5); await d.pause(0.8)
            await d.click("Back to Dana's planner", exact=False); await d.idle(3, limit=150)
            await d.top()
            await d.say("By the time the owner looks, <b>the agent has already re-offered the shift</b>.", 4.5)
        names = await d.phone_names()
        outside = [n for n in names if n not in TEAM]
        yes = "Bianca" if "Bianca" in names else (outside[0] if outside else None)
        if yes:
            d.mark("accept")
            await d.say(f"<b>{yes}</b> lives nearby and gets an offer at their own rate.", 1)
            await d.click(f"{yes} →"); await d.idle(2); await d.pause(1)
            if await d.click("Should I take this?", timeout=8):
                await d.idle(2)
                await d.say("An honest second opinion, checked against their calendar and pay.", 3.5)
            await d.click("Accept"); await d.idle(2); await d.pause(1.2)
            await d.click("Back to Dana's planner", exact=False); await d.idle(3, limit=150)
            await d.top()
            await d.say("<b>1 booked.</b> The owner sees it straight away.", 3)

        # 1:50-2:15 · A change of hours, by chat ---------------------------------------------------------
        names = await d.phone_names()
        asker = "Sam" if "Sam" in names else next((n for n in names if n in TEAM), names[0] if names else None)
        ask = "Can I leave at 3 instead of 5?" if asker == "Sam" else "Could I leave two hours early?"
        if asker:
            d.mark("change")
            await d.say(f"<b>{asker}</b> can say yes, but not to all of it.", 1)
            await d.click(f"{asker} →"); await d.idle(2)
            if await d.click("Message Bean & Co.", role="text", timeout=8):
                await d.idle(1)
                await d.type_in("Message Bean", ask)
                await d.click("Send"); await d.idle(2); await d.pause(1.5)
            await d.click("Back to Dana's planner", exact=False); await d.idle(2)
            await d.click("Offers & messages", role="tab"); await d.idle(1.5)
            await d.scroll(900)
            await d.say("Muse Spark Open reads the message as a <b>change request</b>, and the code checks what "
                        "it means for the week.")
            if await d.click(f"Read {asker}'s message", exact=False, timeout=10):
                await d.idle(2, limit=90)
                await d.scroll(400); await d.pause(3.5)
                if await d.click("Accept change", timeout=8):
                    await d.idle(3, limit=150)
                    await d.top()
                    await d.say(f"One click: {asker}'s shift changes, a reply goes out, and <b>the agent has already "
                                "offered the hours left open</b>.", 5)

        # 2:15-2:35 · The sick call ----------------------------------------------------------------------
        await d.top()
        await d.click("Week", role="tab"); await d.idle(1)
        d.mark("sick")
        await d.say("And that sick call? Just type it.", 1)
        if await d.type_in("e.g. Chris called out", "Chris called out sick Thursday"):
            await d.click("Re-plan"); await d.idle(2)
            await d.say("Chris's Thursday shift reopens, and the agent finds cover.", 1)
            if not await d.click("Find replacements with AI", timeout=8):
                await d.click("Fill gaps with AI", timeout=5)
            await d.idle(3, limit=150)
            await d.scroll(1200); await d.pause(3)

        # 2:35-2:55 · Proof ------------------------------------------------------------------------------
        d.mark("evals")
        await d.click("Evals", role="link"); await d.idle(3)
        await d.say("Every model job is checked against known answers: <b>25 of 25</b> rule sets read right, "
                    "<b>10 of 10</b> test weeks matched the optimizer.", 4.5)
        await d.scroll(650)
        await d.say("<b>100%</b> of the agent's numbers checked, at about <b>2.6¢</b> a plan.", 4.5)

        # 2:55-3:00 · Close ------------------------------------------------------------------------------
        await d.say("")
        d.mark("close")
        await d.card(title + "<p class='u'>nearhand.streamlit.app</p><p>Built with Muse Spark Open</p>", 0)
        await d.pause(6)
        d.save_timeline()
        print("Done. Stop the screen recording now.")
        await d.pause(2)
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=LIVE)
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--caption-seconds", type=float, default=5.0, help="longest a caption stays up (default 5)")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--video", help=argparse.SUPPRESS)
    a = ap.parse_args()
    try:
        asyncio.run(run(a.url, a.pace, not a.no_captions, a.headless, a.video, a.caption_seconds))
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
