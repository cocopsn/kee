// Kee Bridge — service worker.
// Routes events from content scripts + context menu + action click into
// POST /notifications/inbound on the local Kee API.

const KEE_API = 'http://127.0.0.1:7330/notifications/inbound';

async function sendToKee(payload) {
    try {
        const r = await fetch(KEE_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        return r.ok;
    } catch (e) {
        console.warn('[Kee Bridge] POST failed:', e);
        return false;
    }
}

// Context menu: right-click selection → send to Kee
chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: 'kee-send-selection',
        title: 'Send selection to Kee inbox',
        contexts: ['selection'],
    });
    chrome.contextMenus.create({
        id: 'kee-send-page',
        title: 'Send page link to Kee inbox',
        contexts: ['page'],
    });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    if (info.menuItemId === 'kee-send-selection' && info.selectionText) {
        await sendToKee({
            source: 'browser',
            title: tab?.title?.slice(0, 80),
            body: info.selectionText.slice(0, 2000),
            urgency: 1,
            metadata: { url: tab?.url },
        });
    } else if (info.menuItemId === 'kee-send-page' && tab?.url) {
        await sendToKee({
            source: 'browser',
            title: tab.title,
            body: tab.url,
            urgency: 1,
            metadata: { url: tab.url },
        });
    }
});

// Action click: send current page title
chrome.action.onClicked.addListener(async (tab) => {
    if (!tab?.title) return;
    await sendToKee({
        source: 'browser',
        title: tab.title,
        body: tab.url || '',
        urgency: 1,
        metadata: { url: tab.url },
    });
});

// Receive forwarded notifications from content scripts
chrome.runtime.onMessage.addListener((msg, _sender, _sendResponse) => {
    if (msg?.type === 'kee_inbound') {
        sendToKee(msg.payload);
    }
});
