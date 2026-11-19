#!/usr/bin/env python3
"""Cloudflare challenge watcher for the Kasm Chromium session.

Polls the X11 window titles. When a Chromium window shows a Cloudflare
challenge title, it saves a screenshot, locates the Turnstile checkbox by
template match and clicks it with xdotool (real OS input, no CDP). It makes
at most MAX_ATTEMPTS clicks per challenge. After that, the extension's
Telegram alert and human wait take over.

While a DPD window is open, it also saves a screenshot every DPD_GAP_SECONDS
(files named dpd-*.png). These show in-page widgets that do not change the
window title, such as a second checkbox after login. It never clicks on them.

Set CF_WATCH_CLICK=0 to run in capture-only mode.
"""
import os
import random
import re
import subprocess
import sys
import time

import numpy as np
from PIL import Image

POLL_SECONDS = 3
MIN_GAP_SECONDS = 20
KEEP_FILES = 30
DPD_GAP_SECONDS = 15
DPD_KEEP_FILES = 60
SETTLE_SECONDS = 8
RETRY_GAP_SECONDS = 25
MAX_ATTEMPTS = 2
MATCH_THRESHOLD = 0.85
OUT_DIR = os.environ.get("CF_CAPTURE_DIR", "/home/chromium/cf-captures")
CLICK = os.environ.get("CF_WATCH_CLICK", "1") != "0"
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkbox.png")
TITLE_RE = re.compile(r"just a moment|verify you are human|checking your browser", re.I)
DPD_RE = re.compile(r"dpd", re.I)


def gray(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def find_checkbox(img, template, threshold=MATCH_THRESHOLD):
    """Return (x, y, score) of the template centre in img, or None."""
    a, t = gray(img), gray(template)
    th, tw = t.shape
    ah, aw = a.shape
    n = th * tw
    t0 = t - t.mean()
    t_norm = float(np.sqrt((t0 ** 2).sum()))
    if t_norm < 1e-6:
        return None
    shape = (ah + th, aw + tw)
    corr = np.fft.irfft2(
        np.fft.rfft2(a, shape) * np.conj(np.fft.rfft2(t0, shape)), shape
    )[: ah - th + 1, : aw - tw + 1]

    def box_sum(m):
        c = np.pad(m.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return c[th:, tw:] - c[:-th, tw:] - c[th:, :-tw] + c[:-th, :-tw]

    s1, s2 = box_sum(a), box_sum(a * a)
    var = s2 - s1 * s1 / n
    ok = var > 1e-3 * n
    ncc = np.full(corr.shape, -1.0, dtype=np.float64)
    ncc[ok] = corr[ok] / (np.sqrt(var[ok]) * t_norm)
    y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
    score = float(ncc[y, x])
    if score < threshold:
        return None
    return int(x + tw // 2), int(y + th // 2), score


def window_titles():
    ids = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", "."],
        capture_output=True, text=True, timeout=10,
    ).stdout.split()
    titles = []
    for wid in ids:
        name = subprocess.run(
            ["xdotool", "getwindowname", wid],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if name:
            titles.append(name)
    return titles


def screenshot(path):
    subprocess.run(["scrot", "-o", path], check=True, timeout=20)


def prune(prefix="", keep=KEEP_FILES):
    files = sorted(
        f for f in os.listdir(OUT_DIR)
        if f.endswith(".png") and (f.startswith(prefix) if prefix else f[0].isdigit())
    )
    for old in files[:-keep]:
        os.remove(os.path.join(OUT_DIR, old))


def xdo(*args):
    subprocess.run(["xdotool", *args], check=True, timeout=10)


def mouse_position():
    out = subprocess.run(
        ["xdotool", "getmouselocation", "--shell"], capture_output=True, text=True, timeout=10
    ).stdout
    pos = dict(line.split("=") for line in out.split())
    return int(pos["X"]), int(pos["Y"])


def human_click(x, y):
    """Move along a curved, jittered path to near (x, y), then click."""
    tx, ty = x + random.randint(-5, 5), y + random.randint(-5, 5)
    sx, sy = mouse_position()
    if abs(sx - tx) + abs(sy - ty) < 150:
        sx, sy = tx + random.randint(-400, 400), ty + random.randint(150, 350)
    cx = (sx + tx) / 2 + random.randint(-120, 120)
    cy = (sy + ty) / 2 + random.randint(-120, 120)
    steps = random.randint(14, 24)
    for i in range(1, steps + 1):
        t = i / steps
        px = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * tx
        py = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ty
        xdo("mousemove", str(int(px + random.uniform(-1.5, 1.5))), str(int(py + random.uniform(-1.5, 1.5))))
        time.sleep(random.uniform(0.008, 0.03) * (1.6 - abs(0.5 - t)))
    xdo("mousemove", str(tx), str(ty))
    time.sleep(random.uniform(0.15, 0.4))
    xdo("mousedown", "1")
    time.sleep(random.uniform(0.06, 0.14))
    xdo("mouseup", "1")
    return tx, ty


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    template = Image.open(TEMPLATE_PATH).convert("RGB")
    last_capture = 0.0
    last_dpd_capture = 0.0
    active_since = None
    attempts = 0
    last_attempt = 0.0
    scratch = os.path.join(OUT_DIR, "scratch.png")
    print(f"cf-watch started (click={'on' if CLICK else 'off'})", flush=True)
    while True:
        try:
            titles = window_titles()
            hits = [t for t in titles if TITLE_RE.search(t)]
            now = time.time()
            if not hits and now - last_dpd_capture >= DPD_GAP_SECONDS:
                dpd = [t for t in titles if DPD_RE.search(t)]
                if dpd:
                    path = os.path.join(OUT_DIR, "dpd-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
                    screenshot(path)
                    last_dpd_capture = now
                    prune("dpd-", DPD_KEEP_FILES)
                    print(f"dpd window {dpd[0]!r}: saved {path}", flush=True)
            if not hits:
                if active_since is not None:
                    print(f"challenge gone after {now - active_since:.0f}s, {attempts} click(s)", flush=True)
                active_since, attempts = None, 0
            else:
                if active_since is None:
                    active_since, attempts, last_attempt = now, 0, 0.0
                    print(f"challenge started: {hits[0]!r}", flush=True)
                if now - last_capture >= MIN_GAP_SECONDS:
                    path = os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + ".png")
                    screenshot(path)
                    last_capture = now
                    prune()
                    print(f"saved {path}", flush=True)
                can_click = (
                    CLICK
                    and attempts < MAX_ATTEMPTS
                    and now - active_since >= SETTLE_SECONDS
                    and now - last_attempt >= RETRY_GAP_SECONDS
                )
                if can_click:
                    screenshot(scratch)
                    found = find_checkbox(Image.open(scratch).convert("RGB"), template)
                    if found:
                        x, y, score = found
                        attempts += 1
                        last_attempt = time.time()
                        cx, cy = human_click(x, y)
                        print(f"click {attempts}/{MAX_ATTEMPTS} at ({cx},{cy}) score={score:.3f}", flush=True)
                        time.sleep(4)
                        after = os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + "-after.png")
                        screenshot(after)
        except Exception as exc:
            print(f"poll error: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
