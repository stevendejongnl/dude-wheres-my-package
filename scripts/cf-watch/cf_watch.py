#!/usr/bin/env python3
"""Capture-only Cloudflare challenge watcher for the Kasm Chromium session.

Polls the X11 window titles. When a Chromium window shows a Cloudflare
challenge title, it saves one screenshot. It sends no input.
"""
import os
import re
import subprocess
import sys
import time

POLL_SECONDS = 3
MIN_GAP_SECONDS = 20
KEEP_FILES = 30
OUT_DIR = os.environ.get("CF_CAPTURE_DIR", "/home/chromium/cf-captures")
TITLE_RE = re.compile(r"just a moment|verify you are human|checking your browser", re.I)


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


def prune():
    files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".png"))
    for old in files[:-KEEP_FILES]:
        os.remove(os.path.join(OUT_DIR, old))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    last_capture = 0.0
    print("cf-watch started", flush=True)
    while True:
        try:
            hits = [t for t in window_titles() if TITLE_RE.search(t)]
            now = time.time()
            if hits and now - last_capture >= MIN_GAP_SECONDS:
                path = os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + ".png")
                subprocess.run(["scrot", "-o", path], check=True, timeout=20)
                last_capture = now
                prune()
                print(f"challenge title {hits[0]!r}: saved {path}", flush=True)
        except Exception as exc:
            print(f"poll error: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
