# cf-watch

Capture-only watcher for the Kasm Chromium session that runs the DPD sync.

When a Chromium window shows a Cloudflare challenge ("Just a moment", "Verify you are human"),
it saves a screenshot. It sends no input. The screenshots are the input for a later
xdotool click on the Turnstile checkbox.

## Install (in the browser container)

Requires `xdotool`, `scrot` and `python3`.

```bash
install -D -m 755 cf_watch.py /opt/cf-watch/cf_watch.py
install -m 644 cf-watch.service /etc/systemd/system/cf-watch.service
systemctl daemon-reload && systemctl enable --now cf-watch.service
```

The service runs as user `chromium` on `DISPLAY=:1`. Screenshots go to
`/home/chromium/cf-captures/` (newest 30 kept; override with `CF_CAPTURE_DIR`).
Logs: `journalctl -u cf-watch`.
