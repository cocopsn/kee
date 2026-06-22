<script lang="ts">
    import { onMount } from 'svelte';
    import { API_BASE } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';

    type Subsystem = {
        name: string;
        ok: boolean;
        elapsed_ms?: number;
        status_code?: number;
        url?: string;
        // GPU
        model?: string;
        mem_used_mb?: number;
        mem_total_mb?: number;
        util_pct?: number;
        mem_used_pct?: number;
        // Disk / load
        free_gb?: number;
        total_gb?: number;
        percent?: number;
        cpu_pct?: number;
        ram_pct?: number;
        ram_used_gb?: number;
        ram_total_gb?: number;
        error?: string;
    };

    type WorkerHealth = {
        host: string;
        ts: number;
        ok: boolean;
        subsystems: Subsystem[];
    };

    let health = $state<WorkerHealth | null>(null);
    let loading = $state(true);
    let error = $state<string | null>(null);

    async function refresh() {
        try {
            // /system/version (local API) returns whether worker URLs are configured
            const r = await fetch(`${API_BASE}/system/version`);
            const ver = await r.json();
            if (!ver.worker?.host) {
                error = 'No worker configured (AUCTORUM_HOST not set in .env)';
                loading = false;
                return;
            }
            // Fetch worker health via the local API proxying isn't set up;
            // call the worker directly via its known URL pattern.
            // Use the same host the local fleet config knows about.
            const fleetRes = await fetch(`${API_BASE}/fleet`);
            const fleet = await fleetRes.json();
            const workerNode = (fleet.nodes || []).find((n: any) => n.role === 'worker');
            if (!workerNode || !workerNode.alive) {
                error = workerNode
                    ? `Worker ${workerNode.host} unreachable (Tailscale offline?)`
                    : 'No worker node configured in fleet.json';
                loading = false;
                return;
            }
            // Build the worker health URL from the fleet node's host
            const hostUrl = workerNode.host.startsWith('http')
                ? workerNode.host
                : `http://${workerNode.host}:8080`;
            const wRes = await fetch(`${hostUrl}/health`);
            if (!wRes.ok) throw new Error(`worker health → ${wRes.status}`);
            health = await wRes.json();
            error = null;
        } catch (e: any) {
            error = e.message;
        } finally {
            loading = false;
        }
    }

    async function reindexNow() {
        try {
            const r = await fetch(`${API_BASE}/worker/reindex?force=true`, { method: 'POST' });
            const data = await r.json();
            error = data.ran
                ? `re-indexed ${data.indexed} files in ${data.elapsed_s}s`
                : `skipped: ${data.reason}`;
        } catch (e: any) {
            error = e.message;
        }
    }

    onMount(() => {
        refresh();
        const t = setInterval(refresh, 8000);
        return () => clearInterval(t);
    });
</script>

<div class="h-full overflow-y-auto px-6 py-6 max-w-5xl mx-auto">
    <div class="flex items-baseline justify-between mb-5">
        <div>
            <h1 class="text-zinc-200 text-lg tracking-wide mb-1">Worker</h1>
            <p class="text-zinc-500 text-[0.78rem]">
                Auctorum stack — Tailscale-connected worker node hosting
                ChromaDB + Ollama + reranker + vision + health aggregator.
            </p>
        </div>
        <button
            onclick={reindexNow}
            class="mono text-[0.65rem] uppercase tracking-wider px-3 py-1.5 bg-cyan-600/20 border border-cyan-700 text-cyan-200 rounded hover:bg-cyan-600/30">
            re-index now
        </button>
    </div>

    {#if error}
        <Glass>
            <div class="p-4">
                <p class="text-rose-300 text-[0.85rem]">{error}</p>
                <p class="text-zinc-500 text-[0.7rem] mt-2">
                    Standalone HTML viewer always available at
                    <a href="{API_BASE}/worker/dashboard" target="_blank" class="text-cyan-300 hover:underline">/worker/dashboard</a>.
                </p>
            </div>
        </Glass>
    {/if}

    {#if loading}
        <p class="text-zinc-500 text-center mt-12 text-[0.78rem]">probing worker…</p>
    {/if}

    {#if health}
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <!-- Header card -->
            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 mb-3">Host</h2>
                    <div class="flex items-baseline gap-3">
                        <span class="text-zinc-200 text-base">{health.host}</span>
                        <span class="mono text-[0.7rem] {health.ok ? 'text-emerald-300' : 'text-rose-300'}">
                            {health.ok ? '● UP' : '○ DEGRADED'}
                        </span>
                    </div>
                    <p class="mono text-[0.6rem] text-zinc-600 mt-2">
                        last poll: {new Date(health.ts * 1000).toLocaleTimeString('es-MX')}
                    </p>
                </div>
            </Glass>

            <!-- Subsystems card -->
            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 mb-3">Subsystems</h2>
                    <div class="space-y-1.5">
                        {#each health.subsystems.filter(s => ['chroma', 'ollama', 'reranker', 'vision'].includes(s.name)) as s}
                            <div class="flex items-baseline gap-2 text-[0.78rem]">
                                <span class="{s.ok ? 'text-emerald-400' : 'text-rose-400'}">{s.ok ? '●' : '○'}</span>
                                <span class="text-zinc-300 capitalize w-20">{s.name}</span>
                                {#if s.elapsed_ms !== undefined}
                                    <span class="mono text-zinc-500 tabular text-[0.7rem]">{s.elapsed_ms}ms</span>
                                {/if}
                                {#if s.status_code !== undefined}
                                    <span class="mono text-zinc-700 text-[0.65rem]">HTTP {s.status_code}</span>
                                {/if}
                            </div>
                        {/each}
                    </div>
                </div>
            </Glass>

            <!-- GPU card -->
            {#each health.subsystems.filter(s => s.name === 'gpu') as gpu}
                <Glass>
                    <div class="p-4">
                        <h2 class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 mb-3">GPU</h2>
                        {#if gpu.ok}
                            <p class="text-zinc-200 text-[0.85rem] mb-2">{gpu.model}</p>
                            <div class="space-y-1 text-[0.78rem]">
                                <div class="flex justify-between">
                                    <span class="text-zinc-500">VRAM</span>
                                    <span class="mono text-zinc-300 tabular">{gpu.mem_used_mb} / {gpu.mem_total_mb} MB</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-zinc-500">util</span>
                                    <span class="mono text-zinc-300 tabular">{gpu.util_pct}%</span>
                                </div>
                                <div class="w-full h-1 bg-zinc-800 rounded overflow-hidden mt-2">
                                    <div class="h-full bg-cyan-500/60 transition-all duration-300"
                                         style="width: {gpu.mem_used_pct}%"></div>
                                </div>
                            </div>
                        {:else}
                            <p class="text-rose-300 text-[0.78rem]">{gpu.error}</p>
                        {/if}
                    </div>
                </Glass>
            {/each}

            <!-- Hardware card (disk + load) -->
            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 mb-3">Hardware</h2>
                    <div class="space-y-2 text-[0.78rem]">
                        {#each health.subsystems.filter(s => s.name === 'load') as load}
                            <div class="flex justify-between">
                                <span class="text-zinc-500">CPU</span>
                                <span class="mono text-zinc-300 tabular">{load.cpu_pct}%</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-zinc-500">RAM</span>
                                <span class="mono text-zinc-300 tabular">{load.ram_used_gb} / {load.ram_total_gb} GB ({load.ram_pct}%)</span>
                            </div>
                        {/each}
                        {#each health.subsystems.filter(s => s.name === 'disk') as disk}
                            <div class="flex justify-between">
                                <span class="text-zinc-500">Disk free</span>
                                <span class="mono text-zinc-300 tabular">{disk.free_gb} GB ({(100 - (disk.percent || 0)).toFixed(1)}% free)</span>
                            </div>
                        {/each}
                    </div>
                </div>
            </Glass>
        </div>

        <p class="text-center mt-6 mono text-[0.6rem] text-zinc-700">
            Standalone HTML viewer at
            <a href="{API_BASE}/worker/dashboard" target="_blank" class="text-zinc-500 hover:text-cyan-300">/worker/dashboard</a>
        </p>
    {/if}
</div>
