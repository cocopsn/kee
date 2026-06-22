<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api, type AuditRow, type AnomalyRow } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    let rows = $state<AuditRow[]>([]);
    let anomalies = $state<AnomalyRow[]>([]);
    let loading = $state(true);
    let actionFilter = $state<string>('all');
    let toolFilter = $state<string>('');
    let onlyFails = $state(false);
    let limit = $state(200);
    let timer: any;

    async function refresh() {
        try { rows = (await api.audit(limit)).rows; } catch {}
        try { anomalies = (await api.anomalies(50)).rows; } catch {}
        loading = false;
    }

    let view = $state<'audit' | 'anomalies' | 'identity'>('audit');
    let identityHistory = $state<{ rows: any[] }>({ rows: [] });
    let identityDiff = $state<{ sha: string; diff: string } | null>(null);

    async function refreshIdentity() {
        try {
            const r = await fetch(`${import.meta.env.VITE_KEE_API ?? 'http://127.0.0.1:7330'}/identity/history?limit=30`);
            identityHistory = await r.json();
        } catch {}
    }
    async function showDiff(sha: string) {
        try {
            const r = await fetch(`${import.meta.env.VITE_KEE_API ?? 'http://127.0.0.1:7330'}/identity/diff/${sha}`);
            identityDiff = await r.json();
        } catch (e) {
            identityDiff = { sha, diff: `Error: ${(e as Error).message}` };
        }
    }

    let actions = $derived([...new Set(rows.map(r => r.action))].sort());
    let filtered = $derived(rows.filter(r => {
        if (actionFilter !== 'all' && r.action !== actionFilter) return false;
        if (onlyFails && r.success) return false;
        if (toolFilter && !r.tool_name.toLowerCase().includes(toolFilter.toLowerCase())) return false;
        return true;
    }));
    let totalCalls = $derived(rows.filter(r => r.action === 'tool_call' || r.action === 'llm_call').length);
    let totalFails = $derived(rows.filter(r => !r.success).length);
    let totalAnomalies = $derived(anomalies.length);

    onMount(() => { refresh(); refreshIdentity(); timer = setInterval(refresh, 5000); });
    onDestroy(() => clearInterval(timer));

    function fmtTs(s: string): string {
        try { return new Date(s).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
        catch { return s; }
    }
    function actionTone(a: string): string {
        if (a === 'llm_call') return 'cyan';
        if (a === 'tool_call') return 'amber';
        if (a === 'response') return 'violet';
        if (a === 'heartbeat') return 'zinc';
        if (a === 'self_healing') return 'fuchsia';
        return 'zinc';
    }
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">trace</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Audit log</h1>
                <p class="text-sm text-zinc-500 mt-1">Cada acción que Kee ejecutó. Útil para debugging + accountability.</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="rows" value={rows.length} />
                <Stat label="calls" value={totalCalls} accent="cyan" />
                <Stat label="failures" value={totalFails} accent={totalFails > 0 ? 'gold' : 'plain'} />
                <Stat label="anomalies" value={totalAnomalies} accent={totalAnomalies > 0 ? 'gold' : 'plain'} />
            </div>
        </header>

        <!-- View tabs -->
        <div class="mb-3 flex items-center gap-1 hairline-b pb-2">
            {#each [{id: 'audit', label: 'Audit log'}, {id: 'anomalies', label: 'Anomalies'}, {id: 'identity', label: 'Identity history'}] as v (v.id)}
                <button onclick={() => (view = v.id as any)}
                    class="px-4 py-1.5 text-[0.75rem] tracking-wide rounded-full transition-all
                        {view === v.id ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">
                    {v.label}
                </button>
            {/each}
        </div>

        <!-- Filter bar (only for audit view) -->
        {#if view === 'audit'}
        <div class="mb-5 flex items-center gap-2 hairline-b pb-3 flex-wrap">
            <span class="mono text-[0.6rem] uppercase tracking-wider text-zinc-600 mr-2">action</span>
            <button onclick={() => (actionFilter = 'all')}
                class="px-3 py-1 text-[0.7rem] rounded-full
                    {actionFilter === 'all' ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">all</button>
            {#each actions as a (a)}
                <button onclick={() => (actionFilter = a)}
                    class="px-3 py-1 text-[0.7rem] mono rounded-full
                        {actionFilter === a ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">{a}</button>
            {/each}
            <input bind:value={toolFilter} placeholder="filter tool…"
                class="ml-4 mono text-[0.7rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none w-44 placeholder:text-zinc-700"/>
            <label class="flex items-center gap-1.5 text-[0.7rem] text-zinc-400 ml-2 cursor-pointer">
                <input type="checkbox" bind:checked={onlyFails} class="accent-fuchsia-300"/>
                <span>only fails</span>
            </label>
            <span class="ml-auto mono text-[0.65rem] text-zinc-600 tabular">{filtered.length} / {rows.length}</span>
        </div>
        {/if}

        {#if view === 'audit'}
        <div class="grid gap-4 lg:grid-cols-4">
            <!-- Audit rows -->
            <div class="lg:col-span-3">
                <Glass eyebrow="rows" title="Recent" padded={false}>
                    {#if loading}
                        {#each Array(6) as _, i (i)}
                            <div class="px-5 py-2 hairline-b"><div class="h-4 w-2/3 skeleton"></div></div>
                        {/each}
                    {/if}
                    {#each filtered.slice(0, 200) as r (r.id)}
                        {@const tone = actionTone(r.action)}
                        <div class="px-5 py-1.5 hairline-b last:border-b-0 hover:bg-white/[0.015] transition-colors">
                            <div class="flex items-center gap-3 text-[0.72rem]">
                                <span class="mono text-[0.6rem] text-zinc-600 tabular w-14 flex-shrink-0">{fmtTs(r.timestamp)}</span>
                                <span class="chip mono text-[0.6rem]
                                    {tone === 'cyan' ? 'bg-cyan-400/10 text-cyan-200'
                                        : tone === 'amber' ? 'bg-amber-300/10 text-amber-200'
                                        : tone === 'violet' ? 'bg-violet-400/10 text-violet-200'
                                        : tone === 'fuchsia' ? 'bg-fuchsia-300/15 text-fuchsia-200'
                                        : 'bg-zinc-700/30 text-zinc-400'}">{r.action}</span>
                                <span class="mono text-zinc-300 truncate flex-1">{r.tool_name}</span>
                                {#if r.risk_level >= 2}
                                    <span class="chip mono text-[0.6rem] bg-amber-300/10 text-amber-200">r{r.risk_level}</span>
                                {/if}
                                {#if !r.success}
                                    <span class="chip mono text-[0.6rem] bg-fuchsia-300/15 text-fuchsia-200">fail</span>
                                {/if}
                                <span class="mono text-[0.6rem] text-zinc-600 tabular w-12 text-right">#{r.id}</span>
                            </div>
                            {#if r.error}
                                <p class="mono text-[0.65rem] text-fuchsia-200/80 mt-1 ml-20 truncate">{r.error}</p>
                            {/if}
                        </div>
                    {/each}
                </Glass>
            </div>

            <!-- Anomalies sidebar -->
            <div class="lg:col-span-1">
                <Glass eyebrow="anomalies" title="Detected">
                    {#if anomalies.length === 0}
                        <p class="text-[0.78rem] text-zinc-500">Sin anomalías recientes ✓</p>
                    {:else}
                        <ul class="space-y-2">
                            {#each anomalies.slice(0, 12) as a (a.id)}
                                <li class="px-3 py-2 hairline rounded-lg">
                                    <div class="flex items-baseline justify-between mb-1">
                                        <span class="mono text-[0.7rem] text-fuchsia-200">{a.kind}</span>
                                        <span class="mono text-[0.6rem] text-zinc-600 tabular">{fmtTs(a.timestamp)}</span>
                                    </div>
                                    <div class="mono text-[0.65rem] text-zinc-400 truncate">{a.tool_name}</div>
                                    {#if a.detail}
                                        <p class="text-[0.65rem] text-zinc-500 mt-1 line-clamp-2">{a.detail}</p>
                                    {/if}
                                </li>
                            {/each}
                        </ul>
                    {/if}
                </Glass>
            </div>
        </div>
        {/if}

        {#if view === 'anomalies'}
            <Glass eyebrow="anomalies" title="Detected anomalies" padded={false}>
                {#if anomalies.length === 0}
                    <p class="px-5 py-12 text-center text-[0.78rem] text-zinc-500">Sin anomalías. ✓</p>
                {/if}
                {#each anomalies as a (a.id)}
                    <div class="px-5 py-3 hairline-b last:border-b-0 hover:bg-white/[0.015] transition-colors">
                        <div class="flex items-baseline gap-3 mb-1">
                            <span class="mono text-[0.6rem] text-zinc-600 tabular w-14">{fmtTs(a.timestamp)}</span>
                            <span class="chip mono text-[0.6rem] bg-fuchsia-300/15 text-fuchsia-200">{a.kind}</span>
                            <span class="mono text-[0.78rem] text-zinc-300 truncate flex-1">{a.tool_name}</span>
                            {#if a.severity}<span class="chip mono text-[0.6rem] bg-amber-300/10 text-amber-200">sev{a.severity}</span>{/if}
                            <span class="mono text-[0.6rem] text-zinc-700 tabular">#{a.id}</span>
                        </div>
                        {#if a.detail}
                            <p class="text-[0.78rem] text-zinc-400 ml-20 whitespace-pre-wrap">{a.detail}</p>
                        {/if}
                    </div>
                {/each}
            </Glass>
        {/if}

        {#if view === 'identity'}
            <div class="grid gap-4 lg:grid-cols-{identityDiff ? '2' : '1'}">
                <Glass eyebrow="git log" title="Identity changes (soul/identity/router/goals)" padded={false}>
                    {#if identityHistory.rows.length === 0}
                        <p class="px-5 py-12 text-center text-[0.78rem] text-zinc-500">Sin commits a los archivos de identidad. (¿Repo no inicializado?)</p>
                    {/if}
                    {#each identityHistory.rows as r (r.sha)}
                        <button onclick={() => showDiff(r.sha)}
                            class="block w-full text-left px-5 py-3 hairline-b last:border-b-0 hover:bg-white/[0.015] transition-colors
                                {identityDiff?.sha === r.sha ? 'bg-cyan-400/05 border-l-2 border-l-cyan-400/50' : ''}">
                            <div class="flex items-baseline gap-3 mb-1">
                                <span class="mono text-[0.65rem] text-cyan-200 tabular">{r.sha}</span>
                                <span class="mono text-[0.6rem] text-zinc-600 tabular">{r.date.slice(0, 19)}</span>
                                <span class="mono text-[0.6rem] text-zinc-500">{r.author}</span>
                            </div>
                            <p class="text-[0.85rem] text-zinc-200 truncate">{r.subject}</p>
                            {#if r.files?.length}
                                <p class="mono text-[0.6rem] text-zinc-600 mt-1 truncate">{r.files.join(' · ')}</p>
                            {/if}
                        </button>
                    {/each}
                </Glass>
                {#if identityDiff}
                    <Glass eyebrow="diff" title={identityDiff.sha}>
                        {#snippet action()}
                            <button onclick={() => (identityDiff = null)}
                                class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-200">close</button>
                        {/snippet}
                        <pre class="mono text-[0.7rem] text-zinc-300 leading-snug whitespace-pre overflow-auto max-h-[60vh] bg-white/[0.02] hairline p-3 rounded-lg">{identityDiff.diff || '(empty)'}</pre>
                    </Glass>
                {/if}
            </div>
        {/if}
    </div>
</div>
