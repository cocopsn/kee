<script lang="ts">
    import '../../app.css';
    import { onMount, onDestroy } from 'svelte';
    import { openStream } from '$lib/api';
    import JarvisOrb from '$lib/components/JarvisOrb.svelte';

    const pywebview = typeof window !== 'undefined' ? (window as any).pywebview : null;
    const API_BASE = (import.meta as any).env?.VITE_KEE_API ?? 'http://127.0.0.1:7330';

    type AgentState = 'idle' | 'thinking' | 'executing' | 'speaking' | 'error';
    let agentState = $state<AgentState>('idle');
    let lastReply = $state('');
    let lastHeard = $state('');     // last STT transcript (shown briefly when wake fires)
    let input = $state('');
    let sending = $state(false);
    let sessionId = $state(`hologram-${Date.now()}`);
    let watchOn = $state(false);
    let lastObservation = $state('');
    let watchInterval: any = null;

    let canvasEl: HTMLCanvasElement;
    let inputEl: HTMLInputElement;
    let raf: number;
    let cleanupStream: (() => void) | null = null;
    let resetTimer: any = null;

    function bumpState(next: AgentState, holdMs = 1500) {
        agentState = next;
        if (resetTimer) clearTimeout(resetTimer);
        resetTimer = setTimeout(() => (agentState = 'idle'), holdMs);
    }

    async function speak(text: string) {
        if (!text) return;
        try {
            await fetch(`${API_BASE}/voice/speak`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text.slice(0, 800), play: true }),
            });
        } catch {}
    }

    onMount(() => {
        // ── Orb canvas ────────────────────────────────────────────────
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const resize = () => {
            const r = canvasEl.getBoundingClientRect();
            canvasEl.width = r.width * dpr;
            canvasEl.height = r.height * dpr;
        };
        resize();
        window.addEventListener('resize', resize);

        const ctx = canvasEl.getContext('2d', { alpha: true })!;
        let t = 0;

        const palette = (s: AgentState) => {
            switch (s) {
                case 'thinking':  return { core: '#fbbf24', halo: 'rgba(251, 191, 36, 0.55)' };
                case 'executing': return { core: '#22d3ee', halo: 'rgba(34, 211, 238, 0.65)' };
                case 'speaking':  return { core: '#67e8f9', halo: 'rgba(103, 232, 249, 0.75)' };
                case 'error':     return { core: '#f0abfc', halo: 'rgba(232, 121, 249, 0.65)' };
                default:          return { core: '#67e8f9', halo: 'rgba(34, 211, 238, 0.4)' };
            }
        };

        const N = 90;
        const RINGS = 5;
        const points: { angle: number; speed: number; radius: number; phase: number }[] = [];
        for (let i = 0; i < N; i++) {
            points.push({
                angle: Math.random() * Math.PI * 2,
                speed: 0.0003 + Math.random() * 0.0008,
                radius: 0.55 + Math.random() * 0.45,
                phase: Math.random() * Math.PI * 2,
            });
        }

        function frame() {
            t += 1;
            const w = canvasEl.width;
            const h = canvasEl.height;
            const cx = w / 2;
            const cy = h / 2;
            const R = Math.min(w, h) * 0.42;
            const { core, halo } = palette(agentState);

            ctx.clearRect(0, 0, w, h);

            const grad = ctx.createRadialGradient(cx, cy, R * 0.05, cx, cy, R);
            grad.addColorStop(0, halo);
            grad.addColorStop(0.4, halo.replace(/[\d.]+\)/, '0.18)'));
            grad.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = grad;
            ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();

            ctx.strokeStyle = halo;
            ctx.lineWidth = 1 * dpr;
            for (let r = 0; r < RINGS; r++) {
                const rr = R * (0.4 + r * 0.12);
                const tilt = Math.sin(t * 0.005 + r * 0.7) * 0.3;
                ctx.save(); ctx.translate(cx, cy); ctx.rotate(tilt);
                ctx.beginPath();
                ctx.ellipse(0, 0, rr, rr * 0.35, 0, 0, Math.PI * 2);
                ctx.stroke();
                ctx.restore();
            }

            const pulseSpeed = agentState === 'thinking' ? 2.5
                : agentState === 'executing' ? 3.2
                : agentState === 'speaking' ? 2.8 : 1;
            for (const p of points) {
                p.angle += p.speed * pulseSpeed;
                const x = cx + Math.cos(p.angle) * R * p.radius;
                const y = cy + Math.sin(p.angle) * R * p.radius * 0.45;
                const flicker = 0.5 + 0.5 * Math.sin(t * 0.04 + p.phase);
                ctx.fillStyle = halo.replace(/[\d.]+\)/, `${0.3 + flicker * 0.6})`);
                ctx.beginPath();
                ctx.arc(x, y, 1.5 * dpr * (0.7 + flicker * 0.6), 0, Math.PI * 2);
                ctx.fill();
            }

            const corePulse = 0.85 + 0.15 * Math.sin(t * 0.05 * pulseSpeed);
            const coreR = R * 0.18 * corePulse;
            const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 1.5);
            coreGrad.addColorStop(0, core);
            coreGrad.addColorStop(0.5, halo);
            coreGrad.addColorStop(1, 'rgba(0,0,0,0)');
            ctx.fillStyle = coreGrad;
            ctx.beginPath(); ctx.arc(cx, cy, coreR * 1.5, 0, Math.PI * 2); ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.shadowBlur = 12 * dpr;
            ctx.shadowColor = core;
            ctx.beginPath(); ctx.arc(cx, cy, 2 * dpr * corePulse, 0, Math.PI * 2); ctx.fill();
            ctx.shadowBlur = 0;

            raf = requestAnimationFrame(frame);
        }
        raf = requestAnimationFrame(frame);

        // ── Live event stream ───────────────────────────────────────
        try {
            cleanupStream = openStream((e: any) => {
                // Wake-word event: orb pulses cyan + we briefly show what
                // Whisper heard so the user knows Kee picked it up.
                if (e?.action === 'wake_word' || e?.type === 'wake_word') {
                    bumpState('executing', 1500);
                    const transcript = e?.payload?.transcript || e?.summary || '';
                    if (transcript) {
                        lastHeard = `🎤 ${transcript}`;
                        setTimeout(() => (lastHeard = ''), 6000);
                    }
                    return;
                }
                if (e?.kind === 'tool' || e?.type === 'tool_call') bumpState('executing', 1200);
                else if (e?.kind === 'message' || e?.type === 'agent_response') bumpState('speaking', 1500);
                else if (e?.kind === 'error' || e?.type === 'error') bumpState('error', 2500);
                else bumpState('thinking', 800);
            });
        } catch {}

        // Drag handled by CSS -webkit-app-region: drag on .titlebar
        // (the official pywebview pattern, no JS bookkeeping needed).
    });

    onDestroy(() => {
        if (raf) cancelAnimationFrame(raf);
        if (cleanupStream) try { cleanupStream(); } catch {}
        if (watchInterval) clearInterval(watchInterval);
        if (resetTimer) clearTimeout(resetTimer);
    });

    async function send() {
        if (!input.trim() || sending) return;
        const text = input.trim();
        input = '';
        sending = true;
        bumpState('thinking', 60000);
        try {
            const r: any = await fetch(`${API_BASE}/edge/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, session: sessionId }),
            }).then(r => r.json());
            const reply = (r.reply || '').trim();
            if (reply) {
                lastReply = reply;
                bumpState('speaking', Math.min(20000, 2000 + reply.length * 60));
                speak(reply);
                const ms = Math.max(8000, Math.min(25000, reply.length * 70));
                setTimeout(() => { if (lastReply === reply) lastReply = ''; }, ms);
            }
        } catch (e) {
            lastReply = `error: ${(e as Error).message}`;
            bumpState('error', 4000);
            setTimeout(() => (lastReply = ''), 5000);
        }
        sending = false;
        inputEl?.focus();
    }

    function hide()     { pywebview?.api?.hide?.(); }
    function minimize() { pywebview?.api?.minimize?.(); }
    function expand()   { pywebview?.api?.switch_mode?.('full'); }

    async function toggleWatch() {
        watchOn = !watchOn;
        if (watchOn) { await observe(); watchInterval = setInterval(observe, 30_000); }
        else if (watchInterval) { clearInterval(watchInterval); watchInterval = null; lastObservation = ''; }
    }
    async function observe() {
        if (!pywebview?.api?.watch_observe) return;
        bumpState('thinking', 25000);
        try {
            const r = await pywebview.api.watch_observe();
            if (r?.ok && r.description) {
                lastObservation = r.description;
                bumpState('speaking', 2000);
                setTimeout(() => (lastObservation = ''), 25000);
            }
        } catch {}
    }
</script>

<!-- TITLEBAR with drag region. The buttons are SIBLINGS (not children)
     so the drag region doesn't include them at all — guaranteed clickable. -->
<div class="titlebar pywebview-drag-region">
    <span class="dot dot-{agentState}"></span>
    <span class="brand">KEE</span>
    <span class="drag-hint">arrastra aquí</span>
</div>

<!-- Chrome buttons: positioned absolute in the top-right, OUTSIDE the
     drag region entirely. Higher z-index than titlebar. -->
<div class="chrome">
    <button class="ctrl-btn" onclick={toggleWatch} title="watch screen ({watchOn ? 'on' : 'off'})">
        👁
    </button>
    <button class="ctrl-btn" onclick={expand} title="full dashboard">▢</button>
    <button class="ctrl-btn" onclick={minimize} title="minimize">_</button>
    <button class="ctrl-btn ctrl-x" onclick={hide} title="hide">×</button>
</div>

<!-- ORB — Three.js JarvisOrb ported from bertrandmbanwi/Jarvis -->
<div class="orb-wrap">
    <JarvisOrb state={agentState} size={260} />
</div>

<!-- HEARD BANNER (transient — shown when wake-word fires, what Whisper got) -->
{#if lastHeard}
    <div class="heard-banner">{lastHeard}</div>
{/if}

<!-- WATCH BANNER (when watch mode is active) -->
{#if lastObservation}
    <div class="watch-banner">
        <span class="prefix">👁</span> {lastObservation}
    </div>
{/if}

<!-- REPLY PANE (subtitle) -->
{#if lastReply}
    <div class="reply">{lastReply}</div>
{/if}

<!-- ALWAYS-VISIBLE CHAT INPUT at the bottom -->
<div class="chat">
    <input
        bind:this={inputEl}
        bind:value={input}
        placeholder="dile algo a Kee…"
        onkeydown={(e) => { if (e.key === 'Enter') send(); }}
        disabled={sending}
    />
    <button class="send-btn" onclick={send} disabled={sending || !input.trim()}>
        {sending ? '…' : '↵'}
    </button>
</div>

<style>
    :global(html), :global(body), :global(#svelte) {
        background: #0A0A0F !important;
        margin: 0; padding: 0;
        height: 100%; width: 100%;
        overflow: hidden;
        color-scheme: dark;
        color: #f4f4f5;
        font-family: 'Inter', system-ui, sans-serif;
    }

    /* ── Titlebar (pywebview drag-region pattern) ────────────────────
       CRITICAL: titlebar's `right` STOPS at 156px — it does NOT extend
       under the chrome buttons. WebView2's `-webkit-app-region: drag`
       works at the OS hit-test layer (BEFORE DOM events), so even with
       chrome buttons positioned on top with z-index, the drag region
       captures clicks first. Cutting the titlebar's width leaves the
       button area free of any drag-region. */
    .titlebar {
        position: fixed;
        top: 0; left: 0;
        right: 156px;            /* leaves 156px on the right for chrome buttons */
        height: 32px;
        display: flex;
        align-items: center;
        gap: 8px;
        padding-left: 12px;
        background: rgba(0, 0, 0, 0.55);
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        z-index: 100;
        user-select: none;
        -webkit-app-region: drag;
        app-region: drag;
        cursor: grab;
    }
    .titlebar:active { cursor: grabbing; }
    .dot {
        width: 7px; height: 7px;
        border-radius: 50%;
        background: #67e8f9;
        flex-shrink: 0;
    }
    .dot-thinking  { background: #fbbf24; animation: pulse 0.8s infinite; }
    .dot-executing { background: #22d3ee; animation: pulse 0.5s infinite; }
    .dot-speaking  { background: #67e8f9; }
    .dot-error     { background: #f0abfc; }
    .brand {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        letter-spacing: 0.18em;
        color: #f4f4f5;
        font-weight: 600;
    }
    .drag-hint {
        font-family: 'JetBrains Mono', monospace;
        font-size: 8px;
        color: rgba(255, 255, 255, 0.25);
        margin-left: auto;
    }
    /* Chrome lives OUTSIDE the titlebar (sibling), absolutely positioned
       in the top-right. Higher z-index than the titlebar. NOT inside any
       drag region — guaranteed clickable on WebView2. */
    .chrome {
        position: fixed;
        top: 0;
        right: 0;
        height: 32px;
        display: flex;
        z-index: 200;
        background: rgba(0, 0, 0, 0.55);
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        border-left: 1px solid rgba(255, 255, 255, 0.08);
        border-bottom-left-radius: 6px;
    }
    .ctrl-btn {
        height: 32px;
        min-width: 36px;
        padding: 0 8px;
        background: transparent;
        border: none;
        color: rgba(244, 244, 245, 0.95);
        font-size: 14px;
        font-weight: 600;
        line-height: 1;
        cursor: pointer;
        transition: all 0.12s;
        font-family: monospace;
    }
    .ctrl-btn:hover { color: #fff; background: rgba(255, 255, 255, 0.15); }
    .ctrl-x:hover { background: rgba(232, 121, 249, 0.5); color: #fff; }

    /* ── Orb wrapper (Three.js JarvisOrb) ──────────────────────── */
    .orb-wrap {
        position: fixed;
        top: 32px;
        left: 0;
        right: 0;
        bottom: 56px;
        display: flex;
        align-items: center;
        justify-content: center;
        pointer-events: none;
    }

    /* ── Reply subtitle (above chat) ─────────────────────────────── */
    .reply {
        position: fixed;
        bottom: 64px;
        left: 8px; right: 8px;
        padding: 8px 12px;
        background: rgba(8, 9, 11, 0.94);
        border: 1px solid rgba(34, 211, 238, 0.4);
        border-radius: 8px;
        font-size: 12px;
        line-height: 1.4;
        color: #e4faff;
        text-align: center;
        max-height: 96px;
        overflow-y: auto;
        z-index: 30;
        animation: fadeIn 0.3s ease-out;
    }

    /* ── Heard banner (transient — when wake fires, shows STT) ───── */
    .heard-banner {
        position: fixed;
        top: 36px;
        left: 8px; right: 8px;
        padding: 6px 10px;
        background: rgba(34, 211, 238, 0.18);
        border: 1px solid rgba(34, 211, 238, 0.6);
        border-radius: 6px;
        font-size: 11px;
        color: #cffafe;
        z-index: 41;
        text-align: center;
        animation: fadeInBanner 0.2s ease-out;
    }
    @keyframes fadeInBanner {
        from { opacity: 0; transform: translateY(-4px); }
        to   { opacity: 1; transform: translateY(0); }
    }

    /* ── Watch banner (top, below titlebar) ──────────────────────── */
    .watch-banner {
        position: fixed;
        top: 36px;
        left: 8px; right: 8px;
        padding: 6px 10px;
        background: rgba(8, 9, 11, 0.92);
        border: 1px solid rgba(251, 191, 36, 0.4);
        border-radius: 6px;
        font-size: 11px;
        color: #fde68a;
        z-index: 40;
        display: flex;
        gap: 6px;
        align-items: center;
    }
    .prefix { opacity: 0.6; }

    /* ── Chat input (always visible, bottom) ─────────────────────── */
    .chat {
        position: fixed;
        bottom: 0; left: 0; right: 0;
        height: 56px;
        display: flex;
        gap: 4px;
        padding: 8px;
        background: rgba(0, 0, 0, 0.55);
        border-top: 1px solid rgba(255, 255, 255, 0.08);
        z-index: 100;
        box-sizing: border-box;
    }
    .chat input {
        flex: 1;
        padding: 0 12px;
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(34, 211, 238, 0.25);
        border-radius: 6px;
        color: #f4f4f5;
        font-size: 13px;
        outline: none;
        font-family: 'Inter', system-ui, sans-serif;
    }
    .chat input:focus {
        border-color: rgba(34, 211, 238, 0.6);
        background: rgba(255, 255, 255, 0.06);
    }
    .chat input::placeholder { color: rgba(255, 255, 255, 0.3); }
    .send-btn {
        width: 40px;
        background: rgba(34, 211, 238, 0.12);
        border: 1px solid rgba(34, 211, 238, 0.4);
        border-radius: 6px;
        color: #67e8f9;
        font-size: 16px;
        cursor: pointer;
        font-family: monospace;
    }
    .send-btn:hover:not(:disabled) {
        background: rgba(34, 211, 238, 0.25);
    }
    .send-btn:disabled { opacity: 0.3; cursor: not-allowed; }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(4px); }
        to   { opacity: 1; transform: translateY(0); }
    }
</style>
