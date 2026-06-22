<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api, openStream } from '$lib/api';

    // pywebview API surface (when running under desktop shell)
    const pywebview = typeof window !== 'undefined' ? (window as any).pywebview : null;

    // — Live state —
    let healthy = $state<boolean | null>(null);
    let model = $state<string>('');
    let unread = $state(0);
    let recentNotifs = $state<any[]>([]);
    let now = $state(new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' }));
    let voiceState = $state<any>(null);
    let agentBusy = $state(false);
    let lastEvent = $state<string>('');
    let costToday = $state<number | null>(null);

    // — Quick chat —
    let input = $state('');
    let lastReply = $state('');
    let sending = $state(false);
    let sessionId = $state(`hud-${Date.now()}`);

    // — Watch mode —
    let watchOn = $state(false);
    let watchInterval: any = null;
    let lastObservation = $state<string>('');
    let observing = $state(false);

    // — Streams + polls —
    let cleanupStream: (() => void) | null = null;
    let pollTimer: any = null;
    let clockTimer: any = null;

    async function refresh() {
        try {
            const h = await api.health();
            healthy = (h.status === 'ok' || h.status === 'healthy') as any || true;
            model = h.model;
        } catch { healthy = false; }
        try {
            const u = await api.notificationsUnreadCount();
            unread = u.count;
            if (unread > 0) recentNotifs = (await api.notifications({ handled: false, limit: 5 })).rows ?? [];
        } catch {}
        try {
            const v = await api.voiceState();
            voiceState = v;
        } catch {}
        try {
            const c = await api.llmCost();
            costToday = c?.today?.today_usd ?? 0;
        } catch {}
    }

    onMount(() => {
        refresh();
        pollTimer = setInterval(refresh, 5000);
        clockTimer = setInterval(() => (now = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })), 30_000);
        try {
            cleanupStream = openStream((msg: any) => {
                if (msg?.kind === 'tool') agentBusy = true;
                if (msg?.kind === 'message') agentBusy = false;
                if (msg?.summary) lastEvent = msg.summary.toString().slice(0, 60);
            });
        } catch {}
    });

    onDestroy(() => {
        if (pollTimer) clearInterval(pollTimer);
        if (clockTimer) clearInterval(clockTimer);
        if (watchInterval) clearInterval(watchInterval);
        if (cleanupStream) try { cleanupStream(); } catch {}
    });

    async function send() {
        if (!input.trim() || sending) return;
        const text = input.trim();
        input = '';
        sending = true;
        agentBusy = true;
        try {
            const r: any = await fetch(`${(import.meta as any).env?.VITE_KEE_API ?? 'http://127.0.0.1:7330'}/edge/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, session: sessionId }),
            }).then(r => r.json());
            lastReply = r.reply || '(no reply)';
        } catch (e) {
            lastReply = `error: ${(e as Error).message}`;
        }
        sending = false;
        agentBusy = false;
    }

    function expand() {
        if (pywebview?.api?.switch_mode) pywebview.api.switch_mode('full');
    }
    function hide() {
        if (pywebview?.api?.hide) pywebview.api.hide();
    }
    function quit() {
        if (pywebview?.api?.quit) pywebview.api.quit();
    }

    async function toggleWatch() {
        watchOn = !watchOn;
        if (watchOn) {
            await observe();   // immediate first pass
            watchInterval = setInterval(observe, 30_000);
        } else if (watchInterval) {
            clearInterval(watchInterval);
            watchInterval = null;
        }
    }
    async function observe() {
        if (!pywebview?.api?.watch_observe || observing) return;
        observing = true;
        try {
            const r = await pywebview.api.watch_observe();
            if (r?.ok && r.description) lastObservation = r.description;
        } catch {}
        observing = false;
    }

    function dotTone(): string {
        if (agentBusy) return 'bg-amber-300';
        if (healthy === false) return 'bg-fuchsia-400';
        if (healthy === null) return 'bg-zinc-500';
        return 'bg-cyan-400';
    }
</script>

<!-- Drag handle / titlebar -->
<div class="title-bar">
    <div class="flex items-center gap-2 flex-1 min-w-0 drag-region">
        <span class="dot {dotTone()} {agentBusy ? 'animate-pulse' : ''}"></span>
        <span class="font-mono text-xs tracking-wider text-zinc-300 select-none">KEE</span>
        <span class="font-mono text-[0.65rem] text-zinc-500 tabular ml-auto pr-2">{now}</span>
    </div>
    <button onclick={hide}    class="ctrl-btn" title="hide">_</button>
    <button onclick={expand}  class="ctrl-btn" title="expand">▢</button>
    <button onclick={quit}    class="ctrl-btn ctrl-x" title="quit">×</button>
</div>

<div class="hud-body">
    <!-- Status pills -->
    <div class="px-3 pt-2 flex items-center gap-2 flex-wrap">
        <span class="pill pill-cyan">{model || 'no-model'}</span>
        {#if costToday !== null}
            <span class="pill pill-mute">${costToday.toFixed(3)}</span>
        {/if}
        {#if voiceState?.wake_word?.exists}
            <span class="pill pill-cyan">wake✓</span>
        {/if}
        {#if unread > 0}
            <span class="pill pill-fuchsia">📬 {unread}</span>
        {/if}
        {#if watchOn}
            <span class="pill pill-amber">{observing ? 'watching…' : '👁 watch'}</span>
        {/if}
    </div>

    {#if lastEvent}
        <div class="px-3 pt-2 text-[0.65rem] text-zinc-500 font-mono truncate">{lastEvent}</div>
    {/if}

    {#if lastObservation}
        <div class="mx-3 mt-2 p-2 rounded-lg bg-amber-300/5 border border-amber-300/20 text-[0.7rem] text-amber-100/90 leading-snug">
            <span class="opacity-50 mr-1">👁</span>{lastObservation}
        </div>
    {/if}

    <!-- Recent notifications (live) -->
    {#if recentNotifs.length > 0}
        <div class="mx-3 mt-3 space-y-1">
            <div class="text-[0.55rem] uppercase tracking-[0.2em] text-zinc-600">recent</div>
            {#each recentNotifs.slice(0, 3) as n (n.id)}
                <div class="px-2 py-1.5 rounded-md hairline text-[0.7rem] text-zinc-300 truncate">
                    <span class="text-zinc-500 mr-2 text-[0.6rem]">{n.source}</span>{n.body || n.title}
                </div>
            {/each}
        </div>
    {/if}

    <!-- Last reply -->
    {#if lastReply}
        <div class="mx-3 mt-3 p-3 rounded-lg bg-cyan-400/5 border border-cyan-400/20 text-[0.78rem] text-zinc-200 leading-relaxed max-h-32 overflow-y-auto">
            {lastReply}
        </div>
    {/if}

    <!-- Spacer pushes input to bottom -->
    <div class="flex-1"></div>

    <!-- Input bar -->
    <div class="hud-input">
        <input
            bind:value={input}
            placeholder="dile algo a Kee…"
            onkeydown={(e) => e.key === 'Enter' && send()}
            disabled={sending}
            class="flex-1 bg-transparent outline-none text-sm text-zinc-100 placeholder:text-zinc-600 px-2"
        />
        <button onclick={toggleWatch}
            class="text-[0.6rem] uppercase tracking-wider px-2.5 py-1 rounded-md
                {watchOn ? 'text-amber-200 bg-amber-300/10' : 'text-zinc-500 hover:text-amber-200'}"
            title="ojo en pantalla cada 30s">
            👁
        </button>
        <button onclick={send} disabled={sending || !input.trim()}
            class="text-[0.65rem] uppercase tracking-wider px-3 py-1 rounded-md text-cyan-200 bg-cyan-400/10 border border-cyan-400/30 hover:bg-cyan-400/20 disabled:opacity-30">
            {sending ? '…' : 'send'}
        </button>
    </div>
</div>

<style>
    .title-bar {
        display: flex;
        align-items: center;
        height: 32px;
        padding-left: 10px;
        background: rgba(10, 10, 15, 0.92);
        border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        backdrop-filter: blur(20px);
        flex-shrink: 0;
    }
    .drag-region {
        -webkit-app-region: drag;
        app-region: drag;
        height: 32px;
    }
    .ctrl-btn {
        -webkit-app-region: no-drag;
        app-region: no-drag;
        width: 32px;
        height: 32px;
        font-family: monospace;
        color: #71717a;
        background: transparent;
        border: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .ctrl-btn:hover { color: #f4f4f5; background: rgba(255,255,255,0.04); }
    .ctrl-x:hover   { color: #f4f4f5; background: rgba(232, 121, 249, 0.4); }
    .dot {
        width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
    }
    .hud-body {
        flex: 1;
        display: flex;
        flex-direction: column;
        background: rgba(10, 10, 15, 0.92);
        backdrop-filter: blur(20px);
        overflow: hidden;
    }
    .pill {
        display: inline-flex;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.55rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        padding: 0.15rem 0.45rem;
        border-radius: 0.3rem;
        border: 1px solid;
    }
    .pill-cyan    { color: #67e8f9; border-color: rgba(34, 211, 238, 0.3); background: rgba(34, 211, 238, 0.08); }
    .pill-amber   { color: #fcd34d; border-color: rgba(251, 191, 36, 0.3); background: rgba(251, 191, 36, 0.08); }
    .pill-fuchsia { color: #f0abfc; border-color: rgba(232, 121, 249, 0.3); background: rgba(232, 121, 249, 0.08); }
    .pill-mute    { color: #71717a; border-color: rgba(255, 255, 255, 0.06); background: rgba(255, 255, 255, 0.02); }
    .hairline { border: 1px solid rgba(255, 255, 255, 0.06); }
    .hud-input {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem;
        margin: 0.5rem 0.75rem 0.75rem;
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 0.5rem;
        flex-shrink: 0;
    }
    .hud-input:focus-within {
        border-color: rgba(34, 211, 238, 0.4);
    }
</style>
