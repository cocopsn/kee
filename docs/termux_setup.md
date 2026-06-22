# Termux mobile edge — setup guide

Phase 7 ships a lean mobile path so Coco can talk to Kee from Android
without spinning up a full browser. The plan:

1. **Tailscale** on both phone + Alienware so the API is reachable
   without exposing port 7330 to the internet.
2. **`KEE_API_TOKEN`** in `D:\Kee\.env` — a long random string. The
   middleware in `kee/surfaces/api.py` waves through any loopback
   request, but every non-loopback request must present it as a
   bearer.
3. **`POST /edge/ask`** — the lean endpoint. JSON in, JSON out, one
   conversation per `session` id. No SSE, no large payloads.

## Server side (Alienware, one-time)

```powershell
# 1. Add a token to .env
$token = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Add-Content D:\Kee\.env "KEE_API_TOKEN=$token"
Write-Host "Token: $token  (save it on the phone)"

# 2. Open the API to the LAN (currently 127.0.0.1 only). The supervisor
#    spawns api with --api-host 127.0.0.1; switch to 0.0.0.0 for LAN
#    OR — preferred — keep it on 127.0.0.1 and rely on Tailscale to
#    proxy the connection over the Tailnet.
#
#    With Tailscale, edit kee/daemon/supervisor.py SURFACES list:
#       SurfaceSpec(name="api", args=["api", "--api-host", "0.0.0.0"], ...)
#    OR use `tailscale serve` on Windows:
#       tailscale serve https / http://127.0.0.1:7330
```

## Phone side (Termux on Android)

```bash
# 1. Install Termux (F-Droid, NOT Play Store — Play version is abandoned)
#    Then in Termux:
pkg update && pkg install curl jq

# 2. Install Tailscale (Play Store) on the phone, log in to the same
#    tailnet as the Alienware. The Alienware's tailnet IP looks like
#    100.x.y.z — find it via `tailscale status` on the desktop.

# 3. Drop a tiny launcher in $PREFIX/bin/kee
cat > $PREFIX/bin/kee <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
KEE_HOST="${KEE_HOST:-100.x.y.z:7330}"
KEE_TOKEN="${KEE_TOKEN:?Set KEE_TOKEN in ~/.bashrc}"
SESSION="termux-$(whoami)"

if [ $# -eq 0 ]; then
    echo "usage: kee <message>" >&2; exit 1
fi
PAYLOAD=$(jq -nc --arg t "$*" --arg s "$SESSION" '{text:$t, session:$s}')
curl -s -X POST "http://$KEE_HOST/edge/ask" \
    -H "Authorization: Bearer $KEE_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" | jq -r '.reply'
EOF
chmod +x $PREFIX/bin/kee

# 4. Persist Tailscale IP + token
echo 'export KEE_HOST="100.x.y.z:7330"' >> ~/.bashrc
echo 'export KEE_TOKEN="<paste from step 1>"' >> ~/.bashrc
source ~/.bashrc

# 5. Try it
kee "que hora es"
kee "manda un correo a luis@example.com con el resumen del día"
```

## Per-session state

The `session` field on `/edge/ask` is the conversation id. Use one stable
value per device (e.g. `termux-pixel`) so multi-turn context survives
reboots — the conversation lives in `data/kee.db` like every other
surface.

## Voice notes from the phone

Already supported via the Telegram surface — see `kee/surfaces/telegram.py::_on_voice`.
Send a voice memo to your Kee bot, Whisper transcribes, agent processes,
text reply lands in the chat.

## Security model

* **Same machine?** Loopback bypasses the bearer check (the dashboard
  doesn't need to know the token).
* **Same LAN?** Bearer required. CORS allows 192.168/10/100 ranges by
  default.
* **Internet?** Don't. Use Tailscale. If you really need it, wrap the
  API behind a Cloudflare Access / Tunnel and let CF do the auth.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 missing bearer` | Token not set in env, or `Authorization` header missing |
| `401 invalid bearer` | Token mismatch — re-read `.env` value |
| connection refused | API binding to 127.0.0.1; either bind 0.0.0.0 or use `tailscale serve` |
| CORS error in mobile browser | The IP you're hitting from isn't in the regex; either use Tailscale (100.x.y.z works) or set `KEE_CORS_ALLOWED_ORIGINS` |
