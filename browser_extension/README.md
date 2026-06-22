# Kee Bridge — browser extension

Forwards web notifications + right-click selections to Kee at
`http://127.0.0.1:7330/notifications/inbound`.

## Install (Chrome / Edge / Brave)

1. Open `chrome://extensions`
2. Enable **Developer mode** (toggle top-right)
3. Click **Load unpacked**
4. Select `D:\Kee\browser_extension`

## Usage

- **Right-click any text** → "Send selection to Kee inbox"
- **Right-click anywhere** → "Send page link to Kee inbox"
- **Click the extension icon** → sends current page title + URL
- **WhatsApp Web / Slack / Discord / Telegram Web**: native `Notification`
  events are auto-forwarded (best-effort — some apps use Service Worker
  push which can't be intercepted from a content script)

## What gets sent

```json
POST http://127.0.0.1:7330/notifications/inbound
{
  "source": "whatsapp" | "slack" | "discord" | "telegram" | "gmail" | "browser",
  "title": "<sender or page title>",
  "body":  "<message or selection>",
  "urgency": 1,
  "metadata": { "url": "<current page>" }
}
```

Appears in the Kee dashboard bell icon + `/notifications` page within
~1 second of arrival.
