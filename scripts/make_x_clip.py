"""A short vertical cut for X (4:5, 1080x1350, ~45 s), made from the same screen recording as the demo.

    python scripts/make_x_clip.py            # -> video/nearhand_x.mp4

Not a trim of the demo: it opens on a hook card, tells one story (a sick call fixed in one sentence, then
everyone answering on their phone), zooms into the phone screens from the full-resolution recording, puts
big captions above each shot so it works with the sound off, and lays an original synthesized music bed
under it (no licensed audio). Needs ffmpeg, Pillow and numpy. Shot times are seconds on the recording
script's clock (video/timeline.json); boxes are x, y, w, h in the app's own pixels (inside the browser).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edit_video import find_content, find_flash  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VID = ROOT / "video"
W, H, FPS, XF = 1080, 1350, 30, 0.3          # frame, fps, crossfade seconds
PURPLE, INK, BG1, BG2 = (91, 46, 224), (23, 17, 43), (247, 244, 255), (236, 230, 255)
AREA = (40, 350, 1000, 920)                    # where the screen shot sits: x, y, w, h

PHONE = 1030, 810                              # x and width of the phone in the app's pixels
SHOTS = [
    {"card": "hook", "dur": 3.0},
    {"at": 204.0, "to": 207.1, "speed": 1.0, "box": (280, 730, 1180, 450),
     "cap": "The owner just types it: **“Chris called out sick Thursday”**"},
    {"at": 224.9, "to": 228.1, "speed": 1.0, "box": (300, 400, 1700, 1180),
     "cap": "An AI agent finds cover **0.8 mi away.** Every number checked."},
    {"card": "phones", "dur": 2.0},
    {"at": 76.9, "to": 80.5, "speed": 1.2, "box": (PHONE[0], 790, PHONE[1], 810),
     "cap": "Lucia gets the offer on her phone. **She can't make it.**"},
    {"at": 81.1, "to": 85.5, "speed": 1.3, "box": (PHONE[0], 200, PHONE[1], 810),
     "cap": "She taps **Decline.**"},
    {"at": 90.4, "to": 93.4, "speed": 1.0, "box": (280, 300, 1300, 620),
     "cap": "The agent re-offers her shift. **Automatically.**"},
    {"at": 107.3, "to": 114.3, "speed": 1.4, "box": (PHONE[0], 600, PHONE[1], 810),
     "cap": "Bianca asks **“Should I take this?”** An honest answer, on her side."},
    {"at": 117.7, "to": 121.5, "speed": 1.2, "box": (PHONE[0], 500, PHONE[1], 810),
     "cap": "She accepts. **Booked.**"},
    {"at": 145.9, "to": 153.9, "speed": 1.6, "box": (PHONE[0], 380, PHONE[1], 810),
     "cap": "Sam messages: **“Can I leave at 3 instead of 5?”**"},
    {"at": 175.1, "to": 182.5, "speed": 1.5, "box": (280, 700, 1540, 820),
     "cap": "Muse Spark Open turns it into a **one-click change.**"},
    {"at": 189.5, "to": 192.5, "speed": 1.0, "box": (280, 190, 1560, 740),
     "cap": "His freed-up hours? **Already re-offered.**"},
    {"card": "end", "dur": 4.2},
]


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for f in (VID / "fonts" / f"Inter-{weight}.otf", Path(f"/usr/share/fonts/opentype/inter/Inter-{weight}.otf"),
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if f.exists():
            return ImageFont.truetype(str(f), size)
    return ImageFont.load_default()


def gradient(top: tuple, bottom: tuple) -> Image.Image:
    a = np.linspace(0, 1, H)[:, None, None]
    arr = (np.array(top)[None, None] * (1 - a) + np.array(bottom)[None, None] * a).repeat(W, axis=1)
    return Image.fromarray(arr.astype("uint8"), "RGB")


def rich(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, f: ImageFont.FreeTypeFont,
         width: int, color: tuple, accent: tuple, line: int, center: bool = False) -> int:
    """Draw text with **accent** spans, wrapped to `width`. Returns the y after the last line."""
    words = []
    for i, part in enumerate(re.split(r"\*\*", text)):
        words += [(w, accent if i % 2 else color) for w in part.split()]
    lines, cur = [], []
    for w in words:
        trial = " ".join(x for x, _ in cur + [w])
        if cur and draw.textlength(trial, font=f) > width:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    lines.append(cur)
    x0, y = xy
    for ln in lines:
        full = " ".join(x for x, _ in ln)
        x = x0 + (width - draw.textlength(full, font=f)) / 2 if center else x0
        for k, (w, c) in enumerate(ln):
            s = w + (" " if k < len(ln) - 1 else "")
            draw.text((x, y), s, font=f, fill=c)
            x += draw.textlength(s, font=f)
        y += line
    return y


def brand(img: Image.Image, dark: bool) -> None:
    logo = Image.open(ROOT / "assets" / "logo.png").convert("RGBA").resize((46, 46))
    if dark:                                   # white silhouette on the purple cards
        logo = Image.merge("RGBA", (*[Image.new("L", logo.size, 255)] * 3, logo.getchannel("A")))
    img.paste(logo, (60, 44), logo)
    ImageDraw.Draw(img).text((116, 48), "Nearhand", font=font("Bold", 32), fill=(255, 255, 255) if dark else INK)


def shot_frame(cap: str, box: tuple[int, int], out_bg: Path, out_mask: Path) -> tuple[int, int]:
    """Background with the caption, a soft shadow where the video goes, and a rounded-corner mask."""
    bw, bh = box
    img = gradient(BG1, BG2)
    brand(img, False)
    d = ImageDraw.Draw(img)
    rich(d, (60, 130), cap, font("ExtraBold", 58), 960, INK, PURPLE, 70)
    d.text((W / 2, H - 34), "nearhand.streamlit.app", font=font("SemiBold", 26), fill=(120, 110, 150), anchor="mm")
    x = AREA[0] + (AREA[2] - bw) // 2
    y = AREA[1] + (AREA[3] - bh) // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 14, x + bw, y + bh + 14), 30, fill=(60, 30, 140, 70))
    img.paste(sh.filter(ImageFilter.GaussianBlur(22)), (0, 0), sh.filter(ImageFilter.GaussianBlur(22)))
    img.save(out_bg)
    # mask: background colour outside a rounded rectangle, transparent inside, thin border
    m = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    bgcrop = img.crop((x, y, x + bw, y + bh)).convert("RGBA")
    hole = Image.new("L", (bw, bh), 255)
    ImageDraw.Draw(hole).rounded_rectangle((0, 0, bw - 1, bh - 1), 28, fill=0)
    m.paste(bgcrop, (0, 0), hole)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, bw - 1, bh - 1), 28, outline=(214, 204, 245, 255), width=3)
    m.save(out_mask)
    return x, y


def card(kind: str, out: Path) -> None:
    if kind == "end":
        img = gradient(BG1, BG2)
        d = ImageDraw.Draw(img)
        logo = Image.open(ROOT / "assets" / "logo.png").convert("RGBA").resize((190, 190))
        img.paste(logo, ((W - 190) // 2, 250), logo)
        d.text((W / 2, 520), "Nearhand", font=font("ExtraBold", 96), fill=INK, anchor="mm")
        rich(d, (90, 600), "Your week, scheduled. Your gaps, filled **from down the street.**", font("Bold", 50),
             900, (70, 60, 100), PURPLE, 64, center=True)
        pill = "Built with Muse Spark Open"
        f = font("Bold", 40)
        tw = d.textlength(pill, font=f)
        d.rounded_rectangle(((W - tw) / 2 - 34, 800, (W + tw) / 2 + 34, 876), 38, fill=PURPLE)
        d.text((W / 2, 838), pill, font=f, fill="white", anchor="mm")
        d.text((W / 2, 920), "Meta's open-weight model", font=font("SemiBold", 32), fill=(110, 100, 140), anchor="mm")
        d.text((W / 2, 1060), "nearhand.streamlit.app", font=font("ExtraBold", 54), fill=PURPLE, anchor="mm")
        d.text((W / 2, 1130), "Try it: no sign-up, no install", font=font("SemiBold", 32), fill=(110, 100, 140),
               anchor="mm")
    else:
        img = gradient((76, 32, 208), (122, 75, 255))
        brand(img, True)
        d = ImageDraw.Draw(img)
        if kind == "hook":
            d.text((80, 420), "7:02 AM.", font=font("ExtraBold", 150), fill="white")
            rich(d, (80, 620), "Chris just called out sick.", font("ExtraBold", 84), 920, (255, 255, 255),
                 (255, 255, 255), 100)
            d.text((80, 860), "The café opens at 8.", font=font("SemiBold", 54), fill=(225, 215, 255))
        else:
            rich(d, (80, 520), "Then everyone answers **from their phone.**", font("ExtraBold", 84), 920,
                 (255, 255, 255), (255, 214, 102), 104)
    img.save(out)


def music(seconds: float, out: Path) -> None:
    """A soft, original 100 bpm bed: electric-piano chords, a plucked arpeggio, light kick and shaker."""
    sr, beat = 44100, 0.6
    n = int(seconds * sr)
    t = np.arange(n) / sr
    mix = np.zeros(n)
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)  # noqa: E731
    chords = [(53, 57, 60, 64), (55, 59, 62, 67), (52, 55, 59, 62), (57, 60, 64, 67)]  # Fmaj7 G Em7 Am7
    bar = 4 * beat

    def note(start: float, length: float, f: float, amp: float, decay: float) -> None:
        i0, i1 = int(start * sr), min(n, int((start + length) * sr))
        if i0 >= n:
            return
        tt = np.arange(i1 - i0) / sr
        env = np.minimum(1, tt / 0.01) * np.exp(-tt * decay)
        tone = np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(4 * np.pi * f * tt) + 0.12 * np.sin(6 * np.pi * f * tt)
        mix[i0:i1] += amp * env * tone

    k = 0
    while k * bar < seconds:
        ch = chords[k % 4]
        for m in ch:                                              # chord, softly
            note(k * bar, bar + 0.4, hz(m), 0.05, 0.9)
        note(k * bar, bar, hz(ch[0] - 12), 0.12, 1.2)             # bass
        arp = [ch[0] + 12, ch[2] + 12, ch[1] + 12, ch[3] + 12, ch[2] + 12, ch[3] + 12, ch[1] + 12, ch[2] + 12]
        for j, m in enumerate(arp):                               # eighth-note pluck
            note(k * bar + j * beat / 2, 0.5, hz(m), 0.035, 7)
        k += 1
    rng = np.random.default_rng(7)
    for b in range(int(seconds / beat) + 1):
        s = int(b * beat * sr)
        if b % 2 == 0 and s < n:                                  # kick on 1 and 3
            L = min(n - s, int(0.3 * sr))
            tt = np.arange(L) / sr
            mix[s:s + L] += 0.30 * np.sin(2 * np.pi * (50 + 70 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 9)
        for h in (0.5,):                                          # shaker on the off-beat
            s2 = int((b + h) * beat * sr)
            if s2 < n:
                L = min(n - s2, int(0.06 * sr))
                noise = np.diff(rng.standard_normal(L + 1))
                mix[s2:s2 + L] += 0.025 * noise * np.exp(-np.arange(L) / sr * 60)
    fade = np.minimum(1, np.minimum(t / 0.6, (seconds - t) / 2.0))
    mix *= np.clip(fade, 0, 1)
    mix = 0.45 * mix / (np.abs(mix).max() or 1)
    pcm = (np.stack([mix, mix], axis=1) * 32767).astype("<i2")
    import wave
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def run(*args: str) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed:\n{r.stderr[-1500:]}")


def main() -> None:
    recs = sorted([p for p in VID.glob("*") if p.suffix.lower() in (".mov", ".mp4", ".webm", ".mkv")
                   and not p.name.startswith(("nearhand_", "seg_"))], key=lambda p: p.stat().st_mtime)
    if not recs:
        sys.exit("No recording found in video/.")
    rec = recs[-1]
    t0 = find_flash(rec) + 0.12
    app = find_content(rec, t0 + 2.0) or (0, 0, 0, 0)
    ox, oy = app[2], app[3]
    work = VID / "_x"
    work.mkdir(exist_ok=True)
    segs, durs = [], []
    for i, s in enumerate(SHOTS):
        out = work / f"x_{i:02d}.mp4"
        if "card" in s:
            png = work / f"card_{s['card']}.png"
            card(s["card"], png)
            run("ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{s['dur']}", "-i", str(png), "-vf",
                f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out))
            d = s["dur"]
        else:
            bx, by, bw, bh = s["box"]
            scale = min(AREA[2] / bw, AREA[3] / bh)
            vw, vh = int(bw * scale) // 2 * 2, int(bh * scale) // 2 * 2
            bg, mask = work / f"bg_{i:02d}.png", work / f"mask_{i:02d}.png"
            x, y = shot_frame(s["cap"], (vw, vh), bg, mask)
            src = s["to"] - s["at"]
            d = src / s["speed"]
            run("ffmpeg", "-y", "-v", "error", "-ss", f"{t0 + s['at']:.3f}", "-t", f"{src:.3f}", "-i", str(rec),
                "-i", str(bg), "-i", str(mask), "-filter_complex",
                f"[0:v]crop={bw}:{bh}:{ox + bx}:{oy + by},setpts=(PTS-STARTPTS)/{s['speed']},fps={FPS},"
                f"scale={vw}:{vh}:flags=lanczos[v];[1:v][v]overlay={x}:{y}[b];[b][2:v]overlay={x}:{y},"
                f"format=yuv420p[o]", "-map", "[o]", "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", str(out))
        segs.append(out)
        durs.append(d)
        print(f"  {i:2d} {s.get('card', s.get('cap', ''))[:60]:60s} {d:4.1f}s")

    total = sum(durs) - XF * (len(durs) - 1)
    wav = work / "music.wav"
    music(total, wav)
    inputs = sum((["-i", str(p)] for p in segs), [])
    chain = [f"[{k}:v]settb=AVTB,fps={FPS},format=yuv420p[s{k}]" for k in range(len(segs))]
    last, off = "[s0]", 0.0
    for k in range(1, len(segs)):
        off += durs[k - 1] - XF
        lab = f"[x{k}]"
        chain.append(f"{last}[s{k}]xfade=transition=fade:duration={XF}:offset={off:.3f}{lab}")
        last = lab
    final = VID / "nearhand_x.mp4"
    run("ffmpeg", "-y", "-v", "error", *inputs, "-i", str(wav), "-filter_complex", ";".join(chain),
        "-map", last, "-map", f"{len(segs)}:a", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium",
        "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final))
    print(f"\nX video: {final}  ({total:.1f}s, {W}x{H})")
    (work / "shots.json").write_text(json.dumps(SHOTS, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
