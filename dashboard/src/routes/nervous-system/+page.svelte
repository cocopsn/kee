<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import {
        api, openStream,
        type AuditRow, type HeartbeatRow, type StreamEvent
    } from '$lib/api';
    import NeuralCanvas, { type AgentState } from '$lib/components/NeuralCanvas.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';

    let audit = $state<AuditRow[]>([]);
    let heartbeats = $state<HeartbeatRow[]>([]);
    let live = $state<StreamEvent[]>([]);
    let pulse = $state(0);
    let loading = $state(true);
    let unsub: (() => void) | null = null;

    // Inferred agent state from recent activity
    let agentState = $state<AgentState>('idle');
    let lastActivityAt = 0;
    let lastErrorAt = 0;

    let lastHeartbeat = $derived(heartbeats[0]);
    let sysh = $derived(lastHeartbeat?.checks?.system_health ?? {});
    let totalCalls = $derived(audit.filter(r => r.action === 'tool_call').length);
    let failures = $derived(audit.filter(r => !r.success).length);

    function applyEvent(e: StreamEvent) {
        live = [e, ...live].slice(0, 80);
        pulse++;
        lastActivityAt = Date.now();

        // Respect manual state lock — preview / debug mode
        if (stateLock !== 'auto') return;

        const now = Date.now();
        const t = (e.type ?? '').toString().toLowerCase();
        const ok = (e as any).ok;
        const tool = ((e as any).tool ?? '').toString();

        if (ok === false || /error|anomaly|fail/.test(t)) {
            agentState = 'error';
            lastErrorAt = now;
        } else if (/voice|tts|speak/.test(t) || /piper|whisper/.test(tool)) {
            agentState = 'speaking';
        } else if (/^tool_call$|^tool$/.test(t)) {
            agentState = 'executing';
        } else if (/^llm_call$/.test(t) || /^chat$/.test(t) || /response|agent/.test(t)) {
            agentState = 'thinking';
        }
    }

    // Decay back to idle if nothing happens for ~3.5s
    function decayLoop() {
        const since = Date.now() - lastActivityAt;
        if (since > 3500 && agentState !== 'idle') agentState = 'idle';
        // sticky error for at least 4s
        if (agentState === 'error' && (Date.now() - lastErrorAt) > 4000) agentState = 'idle';
    }

    onMount(async () => {
        try { audit = (await api.audit(120)).rows; } catch {}
        try { heartbeats = (await api.heartbeats(8)).rows; } catch {}
        loading = false;
        unsub = openStream((e) => {
            applyEvent(e);
            if (e.type === 'chat' || e.type === 'tool_call' || e.type === 'heartbeat') {
                api.audit(120).then((r) => (audit = r.rows)).catch(() => {});
            }
            if (e.type === 'heartbeat') {
                api.heartbeats(8).then((r) => (heartbeats = r.rows)).catch(() => {});
            }
        });
        const decayInt = setInterval(decayLoop, 500);
        return () => clearInterval(decayInt);
    });
    onDestroy(() => unsub?.());

    function chipTone(row: AuditRow): { bg: string; fg: string } {
        if (!row.success) return { bg: 'bg-fuchsia-300/15', fg: 'text-fuchsia-200' };
        if (row.action === 'tool_call')   return { bg: 'bg-cyan-400/10',   fg: 'text-cyan-200' };
        if (row.action === 'heartbeat')   return { bg: 'bg-zinc-700/30',   fg: 'text-zinc-400' };
        if (row.action === 'response')    return { bg: 'bg-amber-300/10',  fg: 'text-amber-200' };
        return { bg: 'bg-zinc-700/30', fg: 'text-zinc-400' };
    }

    const stateMeta: Record<AgentState, { label: string; tone: 'live' | 'warm' | 'mute' | 'alert'; color: string }> = {
        idle:      { label: 'idle',      tone: 'live',  color: 'text-cyan-200' },
        thinking:  { label: 'thinking',  tone: 'warm',  color: 'text-violet-200' },
        executing: { label: 'executing', tone: 'warm',  color: 'text-fuchsia-200' },
        speaking:  { label: 'speaking',  tone: 'live',  color: 'text-cyan-100' },
        error:     { label: 'error',     tone: 'alert', color: 'text-rose-200' },
    };
    let meta = $derived(stateMeta[agentState]);

    function fmtTs(ts: string): string {
        try { return new Date(ts).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
        catch { return ts; }
    }

    // Manual trigger to test the canvas reactively
    function manualPulse() { pulse++; lastActivityAt = Date.now(); agentState = 'executing'; }

    // Manual state preview / lock (overrides auto-inferred)
    let stateLock = $state<AgentState | 'auto'>('auto');
    let liveFilter = $state<string>('');

    function setStateLock(s: AgentState | 'auto') {
        stateLock = s;
        if (s !== 'auto') agentState = s;
    }
    let filteredLive = $derived(
        liveFilter
            ? live.filter((e) => String(e.type ?? '').toLowerCase().includes(liveFilter.toLowerCase()))
            : live
    );
</script>

<div class="relative h-full w-full">
    <!-- The canvas IS the page. Everything else floats on top. -->
    <div class="absolute inset-0">
        <NeuralCanvas agentState={agentState} pulseTrigger={pulse} label="KEE" density={1.0} />
    </div>

    <!-- TOP: state pill + state selector + manual pulse -->
    <div class="pointer-events-none absolute top-4 left-1/2 -translate-x-1/2 z-10">
        <div class="glass rounded-full px-4 py-2 flex items-center gap-2 pointer-events-auto text-[0.65rem]">
            <PulseDot tone={meta.tone}/>
            <span class="mono uppercase tracking-[0.25em] {meta.color} mr-2">{meta.label}</span>
            <span class="text-zinc-700">·</span>
            {#each ['auto', 'idle', 'thinking', 'executing', 'speaking', 'error'] as s (s)}
                <button onclick={() => setStateLock(s as any)}
                    class="mono uppercase tracking-[0.18em] px-2 py-1 rounded transition-colors
                        {stateLock === s
                            ? 'bg-white/[0.06] text-zinc-100'
                            : 'text-zinc-500 hover:text-zinc-200'}"
                    title="Lock canvas to {s}">
                    {s}
                </button>
            {/each}
            <span class="text-zinc-700">·</span>
            <button onclick={manualPulse}
                class="mono uppercase tracking-[0.2em] text-amber-200/70 hover:text-amber-200 px-2"
                title="Spawn pulse">
                pulse →
            </button>
        </div>
    </div>

    <!-- TOP-LEFT: vital signs -->
    <div class="pointer-events-none absolute top-4 left-4 z-10 flex flex-col gap-2 w-64">
        <div class="glass rounded-2xl px-4 py-3 pointer-events-auto">
            <div class="eyebrow mb-2">vital signs</div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[0.78rem]">
                <span class="text-zinc-600">cpu</span>
                <span class="mono text-zinc-200 tabular text-right">{sysh.cpu_pct ?? '—'}%</span>
                <span class="text-zinc-600">ram</span>
                <span class="mono text-zinc-200 tabular text-right">{sysh.ram_used_pct ?? '—'}%</span>
                <span class="text-zinc-600">disk</span>
                <span class="mono text-zinc-200 tabular text-right">{sysh.disk_free_gb ?? '—'} gb</span>
                <span class="text-zinc-600">vram</span>
                <span class="mono text-cyan-200 tabular text-right">{sysh.vram?.free_mb ?? '—'} mb</span>
            </div>
        </div>
        <div class="glass rounded-2xl px-4 py-3 pointer-events-auto">
            <div class="eyebrow mb-2">activity</div>
            <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[0.78rem]">
                <span class="text-zinc-600">tool calls</span>
                <span class="mono text-amber-200 tabular text-right">{totalCalls}</span>
                <span class="text-zinc-600">failures</span>
                <span class="mono {failures > 0 ? 'text-fuchsia-200' : 'text-zinc-300'} tabular text-right">{failures}</span>
            </div>
        </div>
    </div>

    <!-- TOP-RIGHT: live stream -->
    <div class="pointer-events-none absolute top-4 right-4 z-10 w-80">
        <div class="glass rounded-2xl flex flex-col pointer-events-auto" style="max-height: calc(100vh - 32px);">
            <header class="flex items-baseline gap-3 px-4 pt-3 pb-2 hairline-b">
                <span class="eyebrow">signal</span>
                <h2 class="text-[0.78rem] font-medium text-zinc-200 tracking-tight">Live stream</h2>
                <PulseDot tone="live" size={5}/>
                <button onclick={() => (live = [])} class="ml-auto mono text-[0.55rem] uppercase tracking-wider text-zinc-600 hover:text-zinc-300" title="Clear">clear</button>
            </header>
            <div class="px-4 pt-2 pb-1">
                <input bind:value={liveFilter} placeholder="filter type…"
                    class="w-full mono text-[0.7rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none placeholder:text-zinc-700"/>
            </div>
            <div class="flex-1 overflow-y-auto">
                {#if filteredLive.length === 0}
                    <div class="px-4 py-8 text-center">
                        <p class="text-[0.78rem] text-zinc-500">{liveFilter ? `Sin "${liveFilter}"` : 'Esperando eventos…'}</p>
                    </div>
                {/if}
                {#each filteredLive.slice(0, 20) as e, i (i + String(e.ts))}
                    <div class="px-4 py-1.5 hairline-b last:border-b-0 hover:bg-white/[0.02] transition-colors">
                        <div class="flex items-center gap-2 text-[0.7rem]">
                            <span class="mono text-[0.6rem] text-zinc-600 tabular w-12 flex-shrink-0">{fmtTs(String(e.ts ?? ''))}</span>
                            <span class="chip bg-cyan-400/10 text-cyan-200 text-[0.6rem]">{e.type}</span>
                            {#if typeof (e as any).tool === 'string'}
                                <span class="mono text-amber-200/80 truncate text-[0.65rem]">{(e as any).tool}</span>
                            {/if}
                        </div>
                    </div>
                {/each}
            </div>
        </div>
    </div>

    <!-- BOTTOM: audit log strip -->
    <div class="pointer-events-none absolute bottom-4 left-4 right-4 z-10">
        <div class="glass rounded-2xl flex flex-col pointer-events-auto" style="max-height: 220px;">
            <header class="flex items-baseline gap-3 px-4 pt-3 pb-2 hairline-b">
                <span class="eyebrow">trace</span>
                <h2 class="text-[0.78rem] font-medium text-zinc-200 tracking-tight">Audit log</h2>
                <span class="mono text-[0.6rem] text-zinc-600 tabular ml-auto">{audit.length} rows</span>
            </header>
            <div class="flex-1 overflow-y-auto">
                {#if loading}
                    {#each Array(4) as _, i (i)}
                        <div class="px-4 py-2 hairline-b">
                            <div class="h-3 w-2/3 skeleton"></div>
                        </div>
                    {/each}
                {/if}
                {#each audit.slice(0, 40) as row (row.id)}
                    {@const tone = chipTone(row)}
                    <div class="px-4 py-1.5 hairline-b last:border-b-0 hover:bg-white/[0.02] transition-colors">
                        <div class="flex items-center gap-3 text-[0.72rem]">
                            <span class="mono text-[0.6rem] text-zinc-600 tabular w-14 flex-shrink-0">{fmtTs(row.timestamp)}</span>
                            <span class="chip {tone.bg} {tone.fg} text-[0.6rem]">{row.action}</span>
                            <span class="mono text-zinc-300 truncate flex-1">{row.tool_name}</span>
                            {#if row.risk_level >= 2}
                                <span class="chip bg-amber-300/10 text-amber-200 text-[0.6rem]">r{row.risk_level}</span>
                            {/if}
                            {#if !row.success}
                                <span class="chip bg-fuchsia-300/15 text-fuchsia-200 text-[0.6rem]">fail</span>
                            {/if}
                        </div>
                    </div>
                {/each}
            </div>
        </div>
    </div>

    <!-- BOTTOM-RIGHT corner: future Obsidian + Chroma anchors -->
    <div class="pointer-events-none absolute bottom-[260px] left-1/2 -translate-x-1/2 z-10 flex gap-12 mono text-[0.55rem] uppercase tracking-[0.25em] text-zinc-700">
        <span>obsidian · soon</span>
        <span>chroma · soon</span>
    </div>
</div>
