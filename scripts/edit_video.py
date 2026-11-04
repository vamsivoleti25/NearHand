"""Turn a screen recording of scripts/record_demo.py plus the ElevenLabs voice lines into the final video.

    python scripts/edit_video.py                 # newest recording in video/ -> video/nearhand_demo.mp4
    python scripts/edit_video.py --rec "video/Screen Recording ….mov"
    python scripts/edit_video.py --gap 0.4       # seconds of quiet after each line (default 0.8)

Needs ffmpeg and ffprobe. How it works: it finds the magenta sync flash in the recording, cuts the recording
at the beat times in video/timeline.json, and time-warps each part so it lasts as long as its voice line plus a
short gap: what moves on screen plays at normal speed, the still waits in between shrink, lays the voice lines over it, burns in subtitles from
scripts/narration.json, and also writes a ~25-second clip of the phone handoff for X.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VID = ROOT / "video"
W, H, FPS = 1920, 1080, 30
STRIP = 112                     # white band under the app where the subtitles go


def sh(*args: str) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{args[0]} failed:\n{r.stderr[-1500:]}")
    return r.stdout


def duration(f: Path) -> float:
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)).strip())


def find_flash(rec: Path, search: float = 240) -> float:
    """Seconds into the recording where the magenta sync flash appears."""
    fps, w, h = 20, 32, 18
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-t", str(search), "-i", str(rec), "-vf",
                          f"fps={fps},scale={w}:{h},format=rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    n, size = 0, w * h * 3
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        mag = sum(1 for i in range(0, size, 3) if buf[i] > 190 and buf[i + 1] < 90 and buf[i + 2] > 190)
        if mag > 0.5 * w * h:
            p.kill()
            return n / fps
        n += 1
    sys.exit("Couldn't find the magenta flash in the recording. Was the recording started before the countdown ended?")


def find_content(rec: Path, at: float) -> tuple[int, int, int, int] | None:
    """The app's area in the recording (no browser bars, no desktop around the window), found on the
    all-white title card: the biggest block of near-white rows and columns."""
    info = sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
              "-of", "csv=p=0", str(rec)).strip().split(",")
    w, h = int(info[0]), int(info[1])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(rec), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    if len(raw) < w * h:
        return None

    def runs(flags: list[bool]) -> tuple[int, int]:
        best, cur, start = (0, 0), 0, 0
        for i, f in enumerate(flags + [False]):
            if f:
                if not cur:
                    start = i
                cur += 1
            else:
                if cur > best[1] - best[0]:
                    best = (start, start + cur)
                cur = 0
        return best

    # the browser window's page is light; the desktop around it and Chrome's own bars are dark.
    # Columns first (over the whole height), then rows inside those columns, so lines of text don't
    # break the run.
    cols = [sum(1 for y in range(0, h, 4) if raw[y * w + x] > 200) > 0.5 * (h / 4) for x in range(w)]
    left, right = runs(cols)
    span = range(left, right, 4)
    rows = [sum(1 for x in span if raw[y * w + x] > 200) > 0.3 * len(span) for y in range(h)]
    top, bottom = runs(rows)
    pad = max(4, w // 400)                 # stay clear of the window's rounded corners and edges
    left, right, top, bottom = left + pad, right - pad, top + pad, bottom - 3 * pad
    cw, ch = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    if cw < w * 0.4 or ch < h * 0.4:
        return None
    return cw, ch, left, top


def motion(rec: Path, cache: Path) -> tuple[int, list[int]]:
    """How much of the screen changes between frames, 10 times a second (cached)."""
    fps, w, h = 10, 160, 110
    key = f"{rec.name}:{rec.stat().st_size}"
    if cache.exists():
        m = json.loads(cache.read_text())
        if m.get("key") == key:
            return m["fps"], m["diff"]
    print("measuring motion in the recording (about half a minute)...")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(rec), "-vf", f"fps={fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    prev, diff = None, []
    while True:
        b = p.stdout.read(w * h)
        if len(b) < w * h:
            break
        diff.append(0 if prev is None else sum(1 for x, y in zip(b, prev) if abs(x - y) > 14))
        prev = b
    cache.write_text(json.dumps({"key": key, "fps": fps, "diff": diff}))
    return fps, diff


def plan_beat(diff: list[int], fps: int, start: float, end: float, target: float,
              max_speed: float, read: float) -> list[tuple[float, float, float]]:
    """Time-warp one beat: what moves plays at normal speed (plus `read` seconds after each big change, so
    the result can be read), the still parts in between shrink or stretch to make the beat last `target`.
    Returns (from, to, speed) pieces in recording seconds."""
    i0, i1 = int(start * fps), max(int(start * fps) + 1, int(end * fps))
    d = diff[i0:i1] + [0] * max(0, i1 - i0 - len(diff[i0:i1]))
    n = len(d)
    keep = [False] * n
    for i, v in enumerate(d):
        if v > 15:                                     # something moved: the cursor, typing, a page change
            for j in range(max(0, i - 2), min(n, i + 3)):
                keep[j] = True
        if v > 800:                                    # a big change: give the eye time to read the result
            for j in range(i, min(n, i + int(read * fps))):
                keep[j] = True
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and keep[j] == keep[i]:
            j += 1
        runs.append((i / fps, j / fps, keep[i]))
        i = j
    act = sum(b - a for a, b, k in runs if k)
    still = [(b - a) for a, b, k in runs if not k]
    lo = [min(x, 0.35) for x in still]                 # every pause keeps a beat of stillness
    total_still, min_still = sum(still), sum(lo)
    if act + min_still <= target:                      # room to spare: actions at 1x, pauses share the rest
        s_act = 1.0
        room = target - act
        # pauses shrink, or stretch (they're still frames) when the line is longer than the footage
        r = 1.0 if total_still <= min_still else (room - min_still) / (total_still - min_still)
        shown_still = iter(l + (x - l) * r for x, l in zip(still, lo))
    else:                                              # tight: pauses at their minimum, actions sped up
        s_act = min(max_speed, act / max(0.1, target - min_still))
        shown_still = iter(lo)
    pieces = []
    for a, b, k in runs:
        shown = (b - a) / s_act if k else next(shown_still)
        pieces.append((start + a, start + b, (b - a) / max(shown, 0.04)))
    return pieces


def voice_chunks(f: Path, length: float, text: str) -> list[tuple[float, float, str]]:
    """Split a voice line into its sentences: (from, to, sentence). Each break is guessed from the text's
    length and moved to the nearest pause in the audio; sentences with no pause near the guess stay together."""
    err = subprocess.run(["ffmpeg", "-v", "info", "-i", str(f), "-af", "silencedetect=noise=-35dB:d=0.12",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    pauses = [(x + y) / 2 for x, y in zip(starts, ends) if x > 0.3 and y < length - 0.3]
    sent = sentences(text)
    total = sum(len(x) for x in sent) or 1
    out, begin, said, used = [], 0.0, "", 0
    for k, x in enumerate(sent):
        said = f"{said} {x}".strip()
        used += len(x)
        if k == len(sent) - 1:
            out.append((begin, length, said))
            break
        guess = length * used / total
        near = [q for q in pauses if abs(q - guess) < 0.9 and q > begin + 0.4]
        if near:
            cut = min(near, key=lambda q: abs(q - guess))
            out.append((begin, cut, said))
            begin, said = cut, ""
    return out


def sentences(text: str) -> list[str]:
    out = []
    for x in (x for x in re.split(r"(?<=[.!?:])\s+", text) if x):
        if out and x[0].islower():            # "Should I take this? gives her…": a button name, not an end
            out[-1] += " " + x
        else:
            out.append(x)
    return out


def ass_time(t: float) -> str:
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def subtitles(lines: list[tuple[float, float, str]], out: Path) -> None:
    """One subtitle per (start, length, sentence); long sentences split into even one-line pieces."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: Sub,Inter,36,&H00FFFFFF,&H00000000,&H28182711,1,3,12,0,2,60,60,30\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    rows = []
    for start, length, text in lines:
        parts = []
        for sent in [text]:
            n = -(-len(sent) // 78)          # one line each: long sentences split into even pieces
            words, cur, made = sent.split(), "", 0
            for w in words:
                if made < n - 1 and cur and len(cur) >= len(sent) / n - 4:
                    parts.append(cur)
                    made += 1
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
            parts.append(cur)
        total = sum(len(s) for s in parts) or 1
        t = start
        for s in parts:
            d = length * len(s) / total
            rows.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Sub,{s}")
            t += d
    out.write_text(head + "\n".join(rows) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec")
    ap.add_argument("--gap", type=float, default=0.8, help="quiet seconds after each voice line")
    ap.add_argument("--lead", type=float, default=0.2, help="seconds before each voice line starts")
    ap.add_argument("--read", type=float, default=1.0, help="seconds kept at normal speed after a screen change")
    ap.add_argument("--max-speed", type=float, default=2.0, help="never speed up actions more than this")
    ap.add_argument("--no-crop", action="store_true")
    ap.add_argument("--no-subs", action="store_true")
    a = ap.parse_args()

    recs = sorted([p for p in VID.glob("*") if p.suffix.lower() in (".mov", ".mp4", ".webm", ".mkv")
                   and not p.name.startswith(("nearhand_", "seg_"))], key=lambda p: p.stat().st_mtime)
    rec = Path(a.rec) if a.rec else (recs[-1] if recs else None)
    if not rec or not rec.exists():
        sys.exit("No recording found in video/. Save the screen recording there, or pass --rec.")
    timeline = json.loads((VID / "timeline.json").read_text())
    beats = json.loads((ROOT / "scripts" / "narration.json").read_text())["beats"]
    voices = {p.stem.split("_", 1)[1]: p for p in sorted((VID / "voice").glob("*.mp3"))}
    print(f"recording: {rec.name}")

    flash = find_flash(rec)
    t0 = flash + 0.12                      # the script starts its clock just after showing the flash
    print(f"sync flash at {flash:.2f}s")
    crop = None if a.no_crop else find_content(rec, t0 + 2.0)   # the title card is on screen then
    print("cropping to the app:", f"{crop[0]}x{crop[1]} at {crop[2]},{crop[3]}" if crop else "no crop")
    crop_f = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""
    marks = {m["beat"]: m["t"] for m in timeline["marks"]}
    work = VID / "_work"
    work.mkdir(exist_ok=True)
    order = [b for b in beats if b["id"] in marks]
    fps_m, diff = motion(rec, work / "motion.json")
    segs, subs, clock = [], [], 0.0
    for i, b in enumerate(order):
        start = t0 + marks[b["id"]]
        end = t0 + (marks[order[i + 1]["id"]] if i + 1 < len(order) else timeline["end"])
        voice = voices.get(b["id"])
        v = duration(voice) if voice else 0.0
        want = max(a.lead + v + a.gap, 2.0)
        pieces = plan_beat(diff, fps_m, start, end, want, a.max_speed, a.read)
        wait = float(b.get("wait", 0))     # narration.json can hold the screen still while the voice catches up
        shown = wait + sum((y - x) / sp for x, y, sp in pieces)
        target = max(want, shown)
        hold = max(0.0, target - shown)    # if it's still short, hold the last frame

        # the time-warp as one piecewise-linear setpts expression (recording time -> video time)
        expr, o = None, 0.0
        segs_out = []
        for x, y, sp in pieces:
            segs_out.append((x - start, y - start, o, sp))
            o += (y - x) / sp
        for x, y, oo, sp in reversed(segs_out):
            here = f"({oo:.4f}+(T-{x:.4f})/{sp:.4f})"
            expr = here if expr is None else f"if(lt(T,{y:.4f}),{here},{expr})"
        vchain = (f"[0:v]setpts='({expr})/TB',fps={FPS},"
                  + (f"tpad=start_mode=clone:start_duration={wait:.3f}," if wait else "") + f"{crop_f}"
                  f"scale={W}:{H - STRIP}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:6:color=white"
                  + (f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0.01 else "") + "[v]")

        # the voice: when the screen needs longer than the line, spread its sentences over the beat
        inputs = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(rec)]
        if voice:
            chunks = voice_chunks(voice, v, b["text"])
            spare = target - want - wait      # the held start already covers `wait`
            gaps = len(chunks) - 1
            each = min(4.0, spare * 0.9 / gaps) if gaps and spare > 0.5 else 0.0
            achain = f";[1:a]asplit={len(chunks)}" + "".join(f"[s{k}]" for k in range(len(chunks)))
            at = a.lead
            for k, (c0, c1, said) in enumerate(chunks):
                delay = a.lead if k == 0 else each
                achain += (f";[s{k}]atrim={c0:.3f}:{c1:.3f},asetpts=PTS-STARTPTS,"
                           f"adelay={int(delay * 1000)}:all=1[c{k}]")
                if k:
                    at += each
                parts = sentences(said)        # time the sentences inside a chunk by their length
                n_chars = sum(len(x) for x in parts) or 1
                t = clock + at
                for x in parts:
                    d = (c1 - c0) * len(x) / n_chars
                    subs.append((t, d, x))
                    t += d
                at += c1 - c0
            achain += (";" + "".join(f"[c{k}]" for k in range(len(chunks)))
                       + f"concat=n={len(chunks)}:v=0:a=1,apad=whole_dur={target:.3f}[a]")
            inputs += ["-i", str(voice)]
        else:
            inputs += ["-f", "lavfi", "-t", f"{target:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            achain, each = ";[1:a]anull[a]", 0.0
        out = work / f"seg_{i:02d}.mp4"
        sh("ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", vchain + achain, "-map", "[v]", "-map", "[a]",
           "-t", f"{target:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(out))
        fast = max(sp for _, _, sp in pieces if sp < 4) if any(sp < 4 for _, _, sp in pieces) else 1.0
        print(f"  {b['id']:9s} {end - start:5.1f}s of screen -> {target:5.1f}s  (voice {v:4.1f}s"
              f"{f', +{each:.1f}s between sentences' if each else ''}; actions up to x{fast:.1f})")
        segs.append(out)
        clock += target

    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in segs))
    joined = work / "joined.mp4"
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined))
    final = VID / "nearhand_demo.mp4"
    if a.no_subs:
        sh("ffmpeg", "-y", "-v", "error", "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(final))
    else:
        ass = work / "subs.ass"
        subtitles(subs, ass)
        sh("ffmpeg", "-y", "-v", "error", "-i", str(joined), "-vf", f"ass={ass}", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
           str(final))
    total = duration(final)
    print(f"\nfinal: {final}  ({total:.1f}s{' — over 3:00, shorten a line or lower --gap' if total > 180 else ''})")

    # the X clip: the phone handoff (decline, then accept)
    starts = {}
    t = 0.0
    for b, p in zip(order, segs):
        starts[b["id"]] = (t, duration(p))
        t += starts[b["id"]][1]
    if "decline" in starts:
        s0 = starts["decline"][0]
        e0 = starts.get("accept", starts["decline"])
        clip = VID / "nearhand_x_clip.mp4"
        sh("ffmpeg", "-y", "-v", "error", "-ss", f"{s0:.2f}", "-t", f"{e0[0] + e0[1] - s0:.2f}", "-i", str(final),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-movflags", "+faststart", str(clip))
        print(f"X clip: {clip}  ({duration(clip):.1f}s)")


if __name__ == "__main__":
    main()
