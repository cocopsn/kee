<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api, type DaemonRow, type SupervisorState } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';
    import Sparkline from '$lib/components/Sparkline.svelte';

    // Per-daemon RSS history (last N samples)
    const HISTORY_LEN = 30;
    let history = $state<Record<string, number[]>>({});

    let daemons = $state<DaemonRow[]>([]);
    let supervisor = $state<SupervisorState>({ running: false, surfaces: [] });
    let loading = $state(true);
    let activeLog = $state<string>('api');
    let logLines = $state<string[]>([]);
    let logPath = $state('');
    let refreshTimer: any;

    const knownLogs = [
        { id: 'api', label: 'API server', tone: 'cyan' },
        { id: 'telegram', label: 'Telegram bot', tone: 'cyan' },
        { id: 'voice', label: 'Voice pipeline', tone: 'amber' },
        { id: 'notif_bridge', label: 'Notif bridge', tone: 'amber' },
        { id: 'wake_train', label: 'Wake-word training', tone: 'violet' },
        { id: 'vite', label: 'Dashboard dev', tone: 'zinc' },
    ];

    async function refreshSupervisor() {
        try { supervisor = await api.systemSupervisor(); } catch {}
    }

    async function refreshDaemons() {
        try {
            const r = await api.systemDaemons();
            daemons = r.rows;
            // Update RSS history per pid
            const fresh: Record<string, number[]> = { ...history };
            for (const d of r.rows) {
                const key = `${d.surface}:${d.pid}`;
                const arr = fresh[key] ?? [];
                arr.push(d.rss_mb);
                if (arr.length > HISTORY_LEN) arr.shift();
                fresh[key] = arr;
            }
            history = fresh;
        } catch {}
        loading = false;
    }
    async function loadLog(name: string) {
        activeLog = name;
        try {
            const r = await api.systemLogs(name, 200);
            logLines = r.lines;
            logPath = r.path;
        } catch (e) {
            logLines = [`Error: ${(e as Error).message}`];
        }
    }

    let totalRssMb = $derived(daemons.reduce((s, d) => s + d.rss_mb, 0));

    onMount(() => {
        refreshDaemons();
        refreshSupervisor();
        loadLog('api');
        refreshTimer = setInterval(() => {
            refreshDaemons();
            refreshSupervisor();
            loadLog(activeLog);
        }, 4000);
    });
    onDestroy(() => clearInterval(refreshTimer));

    function fmtUptime(ts: number): string {
        if (!ts) return '?';
        const ms = Date.now() - ts * 1000;
        const m = Math.floor(ms / 60000);
        if (m < 60) return `${m}m`;
        const h = Math.floor(m / 60);
        return `${h}h ${m % 60}m`;
    }
    function fmtSecs(s: number): string {
        if (!s || s < 60) return `${Math.round(s || 0)}s`;
        if (s < 3600) return `${Math.floor(s / 60)}m`;
        const h = Math.floor(s / 3600);
        return `${h}h ${Math.floor((s % 3600) / 60)}m`;
    }
    function surfaceTone(s: string): string {
        return ({api:'cyan', telegram:'cyan', voice:'amber', 'notif-bridge':'amber', 'sleep-cycle':'violet', heartbeat:'violet', watch:'zinc', terminal:'zinc'} as any)[s] ?? 'zinc';
    }

    async function rebuildAgent() {
        try {
            const r = await api.rebuildAgent();
            alert(`Chain rebuilt. Primary: ${r.primary}. Order: ${r.chain_providers.join(' → ')}`);
        } catch (e) {
            alert(`Error: ${(e as Error).message}`);
        }
    }
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">vitals</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Health</h1>
                <p class="text-sm text-zinc-500 mt-1">Daemons, logs, agent control.</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="daemons" value={daemons.length} />
                <Stat label="total rss" value={Number(totalRssMb.toFixed(1))} unit="mb" accent="cyan" />
                <button onclick={rebuildAgent}
                    class="self-end mono text-[0.7rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-4 py-2 rounded-lg">
                    rebuild agent
                </button>
            </div>
        </header>

        <!-- Supervisor strip -->
        <div class="mb-4 hairline rounded-xl px-5 py-3 flex items-center gap-6 bg-white/[0.015]">
            <div class="flex items-center gap-3">
                <PulseDot tone={supervisor.running ? 'live' : 'alert'} size={8} />
                <div>
                    <div class="mono text-[0.7rem] uppercase tracking-wider text-zinc-500">supervisor</div>
                    <div class="text-sm text-zinc-100">
                        {#if supervisor.running}
                            up · pid {supervisor.supervisor_pid}
                        {:else}
                            <span class="text-fuchsia-300">offline</span>
                            <span class="text-zinc-600 ml-2 text-[0.7rem]">run <code class="text-amber-200">python -m kee.main all</code></span>
                        {/if}
                    </div>
                </div>
            </div>
            {#if supervisor.surfaces.length}
                <div class="flex gap-2 flex-wrap ml-auto">
                    {#each supervisor.surfaces as s (s.name)}
                        {@const tone = !s.enabled ? 'mute' : (s.alive ? 'live' : (s.backoff_s ? 'warm' : 'alert'))}
                        <div class="hairline rounded-lg px-3 py-1.5 flex items-center gap-2 min-w-[8rem]"
                             title={s.description + (s.last_exit_code !== null ? ` · last exit ${s.last_exit_code}` : '')}>
                            <PulseDot {tone} size={6}/>
                            <div class="flex-1 min-w-0">
                                <div class="mono text-[0.7rem] text-zinc-200">{s.name}</div>
                                <div class="mono text-[0.55rem] text-zinc-600 tabular">
                                    {#if s.alive}
                                        up {fmtSecs(s.uptime_s)}{s.restarts ? ` · ${s.restarts}r` : ''}
                                    {:else if !s.enabled}
                                        disabled
                                    {:else if s.backoff_s}
                                        retry {fmtSecs(s.backoff_s)}
                                    {:else}
                                        down
                                    {/if}
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <div class="grid gap-4 lg:grid-cols-3">
            <!-- Daemons -->
            <div class="lg:col-span-1">
                <Glass eyebrow="processes" title="Daemons">
                    {#if loading}
                        <div class="h-32 skeleton"></div>
                    {:else if daemons.length === 0}
                        <p class="text-[0.78rem] text-zinc-500">Sin procesos kee detectados.</p>
                    {:else}
                        <ul class="space-y-2">
                            {#each daemons as d (d.pid)}
                                {@const tone = surfaceTone(d.surface)}
                                {@const hist = history[`${d.surface}:${d.pid}`] ?? [d.rss_mb]}
                                <li class="px-3 py-2.5 hairline rounded-xl flex items-center gap-3">
                                    <PulseDot tone="live" size={6}/>
                                    <div class="flex-1 min-w-0">
                                        <div class="flex items-baseline gap-2">
                                            <span class="mono text-[0.85rem] text-zinc-100">{d.surface}</span>
                                            <span class="mono text-[0.6rem] text-zinc-600 tabular">pid {d.pid}</span>
                                        </div>
                                        <div class="flex items-center gap-3 mt-0.5">
                                            <span class="mono text-[0.65rem] text-zinc-500 tabular">{d.rss_mb}MB</span>
                                            <span class="mono text-[0.65rem] text-zinc-600 tabular">·</span>
                                            <span class="mono text-[0.65rem] text-zinc-500 tabular">{fmtUptime(d.started_at)} up</span>
                                        </div>
                                    </div>
                                    <Sparkline values={hist} width={80} height={24} />
                                </li>
                            {/each}
                        </ul>
                    {/if}
                </Glass>
            </div>

            <!-- Logs viewer -->
            <div class="lg:col-span-2">
                <Glass eyebrow="logs" title={knownLogs.find(l => l.id === activeLog)?.label ?? activeLog}>
                    {#snippet action()}
                        <div class="flex items-center gap-1 flex-wrap">
                            {#each knownLogs as log (log.id)}
                                <button onclick={() => loadLog(log.id)}
                                    class="mono text-[0.6rem] uppercase tracking-wider px-2 py-1 rounded transition-colors
                                        {activeLog === log.id
                                            ? 'bg-white/[0.06] text-zinc-100'
                                            : 'text-zinc-500 hover:text-zinc-300'}">
                                    {log.id}
                                </button>
                            {/each}
                        </div>
                    {/snippet}
                    <div class="mb-2 mono text-[0.6rem] text-zinc-600 tabular">{logPath} · {logLines.length} lines</div>
                    <pre class="mono text-[0.7rem] text-zinc-400 leading-snug whitespace-pre overflow-auto bg-white/[0.015] p-3 rounded-lg" style="max-height: 60vh;">{logLines.join('\n') || '(empty)'}</pre>
                </Glass>
            </div>
        </div>
    </div>
</div>
