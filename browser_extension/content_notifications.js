// Content script: hijack the Web Notifications API and forward to Kee.
// Runs on WhatsApp Web, Slack, Discord, Telegram Web. Best-effort —
// these apps mostly use Service Worker push under the hood, but some
// also use the native Notification API which we can intercept.

(function () {
    if (window.__keeBridgeInstalled) return;
    window.__keeBridgeInstalled = true;

    // Determine source from hostname
    function source() {
        const h = location.hostname;
        if (h.includes('whatsapp')) return 'whatsapp';
        if (h.includes('slack')) return 'slack';
        if (h.includes('discord')) return 'discord';
        if (h.includes('telegram')) return 'telegram';
        if (h.includes('mail.google')) return 'gmail';
        return h.split('.').slice(-2)[0] || 'browser';
    }

    function forward(title, body) {
        try {
            chrome.runtime.sendMessage({
                type: 'kee_inbound',
                payload: {
                    source: source(),
                    title: title?.slice(0, 200) || null,
                    body: (body || title || '').slice(0, 2000),
                    urgency: 1,
                    metadata: { url: location.href },
                },
            });
        } catch (e) { /* ignore */ }
    }

    // Wrap window.Notification constructor
    const _Original = window.Notification;
    if (typeof _Original === 'function') {
        const Wrapped = function (title, opts) {
            forward(title, opts?.body);
            return new _Original(title, opts);
        };
        Wrapped.prototype = _Original.prototype;
        Wrapped.permission = _Original.permission;
        Wrapped.requestPermission = _Original.requestPermission?.bind(_Original);
        try { window.Notification = Wrapped; } catch (e) { /* CSP may block */ }
    }
})();
