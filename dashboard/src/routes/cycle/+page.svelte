<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    let state = $state<any>(null);
    let digest = $state<{ date: string; markdown: string } | null>(null);
    let proposals = $state<{ date: string; path: string; bytes: number; applied: boolean }[]>([]);
    let loading = $state(true);
    let running = $state(false);
    let runStatus = $state('');
    let applyingProposal = $state<string | null>(null);
    let timer: any;

    async function refresh() {
        try { state = await api.cycleState(); } catch {}
        try { digest = await api.digestToday(); } catch { digest = null; }
        try { proposals = (await api.cycleProposals()).proposals; } catch {}
        loading = false;
    }
    async function applyProposal(d: string) {
        if (!confirm(`Aplicar proposal ${d}? Editará soul.md irreversiblemente (revertible vía git).`)) return;
        applyingProposal = d;
        try {
            const r = await api.cycleApplyProposal(d);
            runStatus = r.ok
                ? `✓ Identity actualizada (+${r.soul_bytes_added}b, git=${r.git_committed ? '✓' : 'skip'})`
                : `Error aplicando`;
            await refresh();
        } catch (e) {
            runStatus = `Error: ${(e as Error).message}`;
        }
        applyingProposal = null;
        setTimeout(() => (runStatus = ''), 5000);
    }

    async function runNow() {
        running = true;
        runStatus = 'corriendo (free, ollama)…';
        try {
            await api.cycleRun();
            runStatus = '✓ ciclo completado';
            await refresh();
        } catch (e) {
            runStatus = `error: ${(e as Error).message}`;
        }
        running = false;
        setTimeout(() => (runStatus = ''), 4000);
    }

    onMount(() => { refresh(); timer = setInterval(refresh, 30000); });
    onDestroy(() => clearInterval(timer));

    function fmtTs(s: string): string {
        try { return new Date(s).toLocaleString('es-MX', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); }
        catch { return s; }
    }

    let topTools = $derived(() => {
        const breakdown = state?.rolling_stats?.tool_breakdown ?? {};
        return Object.entries(breakdown).sort((a: any, b: any) => b[1] - a[1]).slice(0, 8);
    });
    let axioms = $derived(state?.axioms_recent || []);
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">cognition</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Sleep Cycle</h1>
                <p class="text-sm text-zinc-500 mt-1">Reflection over recent activity → axiomas + digest.</p>
            </div>
            <div class="ml-auto flex items-end gap-10">
                {#if state?.rolling_stats}
                    <Stat label="audit rows" value={state.rolling_stats.audit_rows} accent="cyan" />
                    <Stat label="tool calls" value={state.rolling_stats.tool_calls} />
                    <Stat label="success rate" value={Math.round((state.rolling_stats.tool_success_rate ?? 0) * 100)} unit="%" accent="gold" />
                {/if}
                <button onclick={runNow} disabled={running}
                    class="self-end mono text-[0.7rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-4 py-2 rounded-lg disabled:opacity-30">
                    {running ? '…' : 'run cycle now'}
                </button>
            </div>
        </header>

        {#if runStatus}
            <div class="mb-4 glass rounded-xl px-4 py-2 text-[0.78rem] text-amber-200">{runStatus}</div>
        {/if}

        <div class="grid gap-4 lg:grid-cols-3">
            <!-- Axioms -->
            <div class="lg:col-span-2">
                <Glass eyebrow="learned" title="Axiomas recientes">
                    {#if loading}
                        <div class="h-32 skeleton"></div>
                    {:else if !state?.exists}
                        <p class="text-[0.78rem] text-zinc-500">Sleep cycle nunca corrió. Click "run cycle now".</p>
                    {:else if axioms.length === 0}
                        <p class="text-[0.78rem] text-zinc-500">Sin axiomas extraídos aún.</p>
                    {:else}
                        <ul class="space-y-2">
                            {#each axioms as ax (ax)}
                                <li class="flex gap-3 px-3 py-2.5 hairline rounded-xl">
                                    <span class="text-cyan-400 flex-shrink-0">·</span>
                                    <span class="text-[0.85rem] text-zinc-200 leading-relaxed">{ax}</span>
                                </li>
                            {/each}
                        </ul>
                    {/if}
                </Glass>
            </div>

            <!-- Stats sidebar -->
            <div class="lg:col-span-1 space-y-4">
                <Glass eyebrow="last run" title="Resumen">
                    {#if state?.last_run}
                        <p class="text-[0.78rem] text-zinc-400">{fmtTs(state.last_run)}</p>
                        {#if state?.rolling_stats?.window_hours}
                            <p class="text-[0.7rem] text-zinc-600 mt-1">ventana: últimas {state.rolling_stats.window_hours}h</p>
                        {/if}
                        {#if state?.rolling_stats?.peak_activity_hour !== undefined}
                            <p class="text-[0.7rem] text-zinc-500 mt-2">
                                pico de actividad: <span class="mono text-zinc-300 tabular">{state.rolling_stats.peak_activity_hour}:00</span>
                            </p>
                        {/if}
                        {#if state?.rolling_stats?.anomalies !== undefined}
                            <p class="text-[0.7rem] text-zinc-500">
                                anomalías: <span class="mono {state.rolling_stats.anomalies > 0 ? 'text-fuchsia-200' : 'text-zinc-300'} tabular">{state.rolling_stats.anomalies}</span>
                            </p>
                        {/if}
                    {/if}
                </Glass>

                <Glass eyebrow="tool usage" title="Top tools">
                    {#if topTools().length > 0}
                        <ul class="space-y-1.5 text-[0.78rem]">
                            {#each topTools() as [tool, count] (tool)}
                                <li class="flex justify-between">
                                    <span class="mono text-zinc-300">{tool}</span>
                                    <span class="mono text-amber-200/80 tabular">{count}</span>
                                </li>
                            {/each}
                        </ul>
                    {:else}
                        <p class="text-[0.78rem] text-zinc-500">Sin datos de tool usage.</p>
                    {/if}
                </Glass>
            </div>

            <!-- Identity proposals -->
            <div class="lg:col-span-3">
                <Glass eyebrow="identity evolution" title="Proposals">
                    {#if proposals.length === 0}
                        <p class="text-[0.78rem] text-zinc-500">Sin proposals aún. El Sleep Cycle genera una propuesta de cambio a soul.md cada vez que extrae axiomas.</p>
                    {:else}
                        <ul class="space-y-2">
                            {#each proposals as p (p.date)}
                                <li class="flex items-center justify-between px-3 py-2.5 hairline rounded-xl gap-3">
                                    <div class="flex items-baseline gap-3 min-w-0">
                                        <span class="mono text-[0.85rem] text-zinc-200 tabular flex-shrink-0">{p.date}</span>
                                        {#if p.applied}
                                            <span class="chip text-[0.6rem] bg-cyan-400/10 text-cyan-200">applied ✓</span>
                                        {:else}
                                            <span class="chip text-[0.6rem] bg-amber-300/10 text-amber-200">pending</span>
                                        {/if}
                                        <span class="mono text-[0.65rem] text-zinc-600 tabular truncate">{(p.bytes / 1024).toFixed(1)}KB</span>
                                    </div>
                                    {#if !p.applied}
                                        <button onclick={() => applyProposal(p.date)} disabled={applyingProposal === p.date}
                                            class="mono text-[0.65rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-3 py-1.5 rounded-lg disabled:opacity-30 flex-shrink-0">
                                            {applyingProposal === p.date ? '…' : 'apply →'}
                                        </button>
                                    {/if}
                                </li>
                            {/each}
                        </ul>
                    {/if}
                </Glass>
            </div>

            <!-- Digest -->
            <div class="lg:col-span-3">
                <Glass eyebrow="digest" title="Digest de hoy">
                    {#if digest?.markdown}
                        <pre class="text-[0.85rem] text-zinc-300 leading-relaxed whitespace-pre-wrap">{digest.markdown}</pre>
                    {:else}
                        <p class="text-[0.78rem] text-zinc-500">Sin digest hoy. Click "run cycle now" para generar uno.</p>
                    {/if}
                </Glass>
            </div>
        </div>
    </div>
</div>
