<script lang="ts">
    import { onMount } from 'svelte';
    import { api, type ToolInfo, type AutonomySummary } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    let tools = $state<ToolInfo[]>([]);
    let autonomy = $state<AutonomySummary | null>(null);
    let filter = $state<'all' | 'builtin' | 'created' | 'failing'>('all');
    let loading = $state(true);
    let viewerTool = $state<string | null>(null);
    let viewerSource = $state<{ source: string; path: string; lines: number; bytes: number } | null>(null);
    let viewerLoading = $state(false);
    let execTool = $state<{ name: string; schema: any } | null>(null);
    let execArgs = $state('{}');
    let execResult = $state<any>(null);
    let execRunning = $state(false);

    onMount(async () => {
        try { tools = (await api.tools()).tools; } catch {}
        try { autonomy = await api.autonomy(50); } catch {}
        loading = false;
    });

    async function openSource(name: string) {
        viewerTool = name;
        viewerLoading = true;
        viewerSource = null;
        try {
            viewerSource = await api.toolSource(name);
        } catch (e) {
            viewerSource = { source: `Error: ${(e as Error).message}`, path: '?', lines: 0, bytes: 0 };
        }
        viewerLoading = false;
    }

    function copySource() {
        if (!viewerSource?.source) return;
        navigator.clipboard.writeText(viewerSource.source);
    }

    function openExec(tool: ToolInfo) {
        execTool = { name: tool.name, schema: tool.parameters_schema };
        execArgs = '{}';
        execResult = null;
    }
    async function runExec() {
        if (!execTool) return;
        execRunning = true;
        execResult = null;
        try {
            const args = execArgs.trim() ? JSON.parse(execArgs) : {};
            execResult = await api.toolExecute(execTool.name, args);
        } catch (e) {
            execResult = { ok: false, error: (e as Error).message };
        }
        execRunning = false;
    }

    function trustForTool(name: string) {
        return autonomy?.tools.find((t) => t.tool_name === name) ?? null;
    }

    function riskMeta(r: number): { label: string; color: string; ring: string } {
        const m = [
            { label: 'read',     color: 'text-zinc-300',     ring: 'ring-zinc-500/20' },
            { label: 'local',    color: 'text-cyan-200',     ring: 'ring-cyan-400/30' },
            { label: 'system',   color: 'text-amber-200',    ring: 'ring-amber-300/30' },
            { label: 'external', color: 'text-fuchsia-200',  ring: 'ring-fuchsia-300/30' },
        ];
        return m[r] ?? m[0];
    }

    let filtered = $derived(tools.filter((t) => {
        if (filter === 'all') return true;
        if (filter === 'builtin') return !t.source?.startsWith('vault');
        if (filter === 'created') return t.source?.startsWith('vault');
        if (filter === 'failing') {
            const tr = trustForTool(t.name);
            return tr && tr.success_rate !== null && tr.success_rate < 0.8;
        }
        return true;
    }));

    let totalCalls = $derived(autonomy?.tools.reduce((s, t) => s + t.samples, 0) ?? 0);
    let avgSuccess = $derived(() => {
        if (!autonomy) return 0;
        const wt = autonomy.tools.filter((t) => t.success_rate !== null);
        if (!wt.length) return 0;
        return wt.reduce((s, t) => s + (t.success_rate ?? 0), 0) / wt.length;
    });
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <!-- Hero -->
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">capabilities</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Tool registry</h1>
                <p class="text-sm text-zinc-500 mt-1 max-w-md">
                    Las herramientas que Kee puede invocar. Riesgo y autonomía se calculan a partir del audit log.
                </p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="active"   value={tools.length}/>
                <Stat label="calls"    value={totalCalls} accent="cyan"/>
                <Stat label="avg ok"   value={Math.round(avgSuccess() * 100)} unit="%" accent={avgSuccess() > 0.85 ? 'gold' : 'plain'}/>
            </div>
        </header>

        <!-- Filter strip -->
        <div class="mb-5 flex items-center gap-1 hairline-b pb-3">
            {#each ['all', 'builtin', 'created', 'failing'] as f (f)}
                <button
                    onclick={() => (filter = f as any)}
                    class="px-4 py-1.5 text-[0.78rem] tracking-wide rounded-full transition-all
                        {filter === f
                            ? 'bg-white/[0.04] text-zinc-100 hairline'
                            : 'text-zinc-500 hover:text-zinc-300'}"
                >
                    {f}
                </button>
            {/each}
            <span class="ml-auto mono text-[0.7rem] text-zinc-600 tabular">{filtered.length} de {tools.length}</span>
        </div>

        <!-- Cards -->
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {#if loading}
                {#each Array(6) as _, i (i)}
                    <div class="glass rounded-2xl p-5 space-y-3">
                        <div class="h-4 w-1/3 skeleton"></div>
                        <div class="h-3 w-full skeleton"></div>
                        <div class="h-3 w-2/3 skeleton"></div>
                    </div>
                {/each}
            {/if}
            {#each filtered as t (t.name)}
                {@const trust = trustForTool(t.name)}
                {@const rm = riskMeta(t.risk_level)}
                {@const ok = trust ? Math.round((trust.success_rate ?? 0) * 100) : null}
                <article class="glass rounded-2xl p-5 lift">
                    <header class="flex items-center justify-between mb-2.5">
                        <h3 class="mono text-[0.95rem] font-medium text-zinc-100 tracking-tight">{t.name}</h3>
                        <span class="chip {rm.color} ring-1 {rm.ring}">{rm.label}</span>
                    </header>
                    <p class="text-[0.78rem] leading-relaxed text-zinc-400 line-clamp-3">{t.description}</p>

                    {#if trust && trust.samples > 0}
                        <div class="mt-4 flex items-center gap-3 hairline-t pt-3">
                            <!-- Tiny success arc -->
                            <div class="relative h-9 w-9 flex-shrink-0">
                                <svg viewBox="0 0 36 36" class="h-9 w-9 -rotate-90">
                                    <circle cx="18" cy="18" r="14" fill="none" stroke="rgb(255 255 255 / 0.06)" stroke-width="2.5"/>
                                    <circle
                                        cx="18" cy="18" r="14" fill="none"
                                        stroke={ok && ok > 90 ? '#22d3ee' : ok && ok > 70 ? '#fcd34d' : '#f0abfc'}
                                        stroke-width="2.5"
                                        stroke-linecap="round"
                                        stroke-dasharray={`${(ok ?? 0) * 88 / 100} 88`}
                                        style="transition: stroke-dasharray 0.7s cubic-bezier(0.2, 0.8, 0.2, 1);"
                                    />
                                </svg>
                                <span class="absolute inset-0 flex items-center justify-center mono text-[0.6rem] tabular text-zinc-300">{ok}</span>
                            </div>
                            <div class="flex-1 grid grid-cols-2 gap-y-0.5 text-[0.7rem]">
                                <span class="text-zinc-600">calls</span><span class="mono text-zinc-300 tabular text-right">{trust.samples}</span>
                                {#if trust.recent_corrections > 0}
                                    <span class="text-amber-300/70">corrections</span><span class="mono text-amber-200 tabular text-right">{trust.recent_corrections}</span>
                                {/if}
                            </div>
                        </div>
                    {/if}
                    <div class="mt-3 flex items-baseline justify-between gap-2">
                        <div class="mono text-[0.6rem] text-zinc-600 tracking-wider uppercase">{t.source}</div>
                        <div class="flex items-center gap-3">
                            {#if t.risk_level === 0}
                                <button onclick={() => openExec(t)}
                                    class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-amber-200">
                                    try →
                                </button>
                            {/if}
                            <button onclick={() => openSource(t.name)}
                                class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">
                                source →
                            </button>
                        </div>
                    </div>
                </article>
            {/each}
        </div>

        {#if !loading && filtered.length === 0}
            <p class="text-center text-sm text-zinc-500 py-12">No hay herramientas que cumplan ese filtro.</p>
        {/if}
    </div>

    <!-- Source viewer modal -->
    {#if execTool}
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm" onclick={() => (execTool = null)} role="presentation">
            <div class="glass rounded-2xl max-w-2xl w-full mx-6 max-h-[85vh] flex flex-col" onclick={(e) => e.stopPropagation()} role="presentation">
                <header class="flex items-baseline justify-between px-6 pt-5 pb-3 hairline-b">
                    <div>
                        <span class="eyebrow">execute</span>
                        <h3 class="mono text-lg text-amber-200 mt-0.5">{execTool.name}</h3>
                    </div>
                    <button onclick={() => (execTool = null)} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
                </header>
                <div class="flex-1 overflow-auto p-5 space-y-4">
                    <div>
                        <div class="eyebrow mb-1">parameters_schema</div>
                        <pre class="mono text-[0.7rem] text-zinc-500 bg-white/[0.02] hairline p-3 rounded-lg whitespace-pre-wrap">{JSON.stringify(execTool.schema ?? {}, null, 2)}</pre>
                    </div>
                    <div>
                        <div class="eyebrow mb-1">arguments (json)</div>
                        <textarea bind:value={execArgs} rows="6"
                            class="w-full mono text-[0.78rem] text-zinc-200 bg-white/[0.02] hairline focus:border-amber-300/40 focus:outline-none p-3 rounded-lg resize-none"
                            spellcheck="false"></textarea>
                    </div>
                    {#if execResult}
                        <div>
                            <div class="eyebrow mb-1">
                                {execResult.ok ? '✓ result' : '✗ error'}
                                {#if execResult.elapsed_ms != null}
                                    · <span class="mono text-zinc-600 tabular">{execResult.elapsed_ms}ms</span>
                                {/if}
                            </div>
                            <pre class="mono text-[0.75rem] text-zinc-300 bg-white/[0.02] hairline p-3 rounded-lg whitespace-pre-wrap">{JSON.stringify(execResult.ok ? execResult.result : execResult.error, null, 2)}</pre>
                        </div>
                    {/if}
                </div>
                <div class="flex justify-end gap-2 hairline-t px-6 py-3">
                    <button onclick={() => (execTool = null)}
                        class="mono text-[0.7rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-200 px-4 py-2">cerrar</button>
                    <button onclick={runExec} disabled={execRunning}
                        class="mono text-[0.7rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-4 py-2 rounded-lg disabled:opacity-30">
                        {execRunning ? '…' : 'execute'}
                    </button>
                </div>
            </div>
        </div>
    {/if}

    {#if viewerTool}
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm" onclick={() => (viewerTool = null)} role="presentation">
            <div class="glass rounded-2xl max-w-4xl w-full mx-6 max-h-[85vh] flex flex-col" onclick={(e) => e.stopPropagation()} role="presentation">
                <header class="flex items-baseline justify-between px-6 pt-5 pb-3 hairline-b">
                    <div>
                        <span class="eyebrow">source</span>
                        <h3 class="mono text-lg text-cyan-200 mt-0.5">{viewerTool}</h3>
                        {#if viewerSource}
                            <p class="mono text-[0.65rem] text-zinc-600 tabular mt-1">{viewerSource.path} · {viewerSource.lines}L · {(viewerSource.bytes / 1024).toFixed(1)}KB</p>
                        {/if}
                    </div>
                    <div class="flex items-center gap-3">
                        <button onclick={copySource} disabled={!viewerSource}
                            class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 disabled:opacity-30">copy</button>
                        <button onclick={() => (viewerTool = null)} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
                    </div>
                </header>
                <div class="flex-1 overflow-auto p-4">
                    {#if viewerLoading}
                        <div class="h-32 skeleton"></div>
                    {:else if viewerSource}
                        <pre class="mono text-[0.78rem] text-zinc-300 leading-relaxed whitespace-pre overflow-x-auto"
                            style="tab-size: 4;">{viewerSource.source}</pre>
                    {/if}
                </div>
            </div>
        </div>
    {/if}
</div>
