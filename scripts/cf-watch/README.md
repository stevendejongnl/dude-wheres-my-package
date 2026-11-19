# cf-watch

Watcher for the Kasm Chromium session that runs the DPD sync.

When a Chromium window shows a Cloudflare challenge ("Just a moment", "Verify you are human"),
it saves a screenshot. After 8 seconds it finds the Turnstile checkbox with a template match
(`checkbox.png`) and clicks it with `xdotool` (real OS input, no CDP). It makes at most
2 clicks per challenge. After that, the extension's Telegram alert and 3-minute human wait
take over.

While a DPD window is open, it also saves a screenshot every 15 seconds (`dpd-*.png`, newest 60
kept). These show in-page widgets that do not change the window title. It never clicks on them.

Set `CF_WATCH_CLICK=0` in the service environment to run capture-only.

## Install (in the browser container)

Requires `xdotool`, `scrot`, `python3`, `python3-pil` and `python3-numpy`.

```bash
install -D -m 755 cf_watch.py /opt/cf-watch/cf_watch.py
install -m 644 checkbox.png /opt/cf-watch/checkbox.png
install -m 644 cf-watch.service /etc/systemd/system/cf-watch.service
systemctl daemon-reload && systemctl enable --now cf-watch.service
```

The service runs as user `chromium` on `DISPLAY=:1`. Screenshots go to
`/home/chromium/cf-captures/` (newest 30 kept; override with `CF_CAPTURE_DIR`).
Logs: `journalctl -u cf-watch`.

## Limits

- `checkbox.png` is the dark-theme widget, cut from a 3428x1286 screen. A light theme or a
  different widget style needs a second template.
- The threshold is 0.85. A frame without the widget scores about 0.4.
