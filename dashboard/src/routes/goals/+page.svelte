<script lang="ts">
    import { onMount } from 'svelte';
    import { api, type GoalRow, type EconomySummary } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    let goals = $state<GoalRow[]>([]);
    let economy = $state<EconomySummary | null>(null);
    let loading = $state(true);
    let windowDays = $state<number | null>(null); // null = lifetime
    let editorOpen = $state(false);
    let goalsMd = $state('');
    let savingGoals = $state(false);
    let saveStatus = $state('');

    async function refresh() {
        try { goals = (await api.goals()).goals; } catch {}
        try { economy = await api.economySummary(windowDays ?? undefined); } catch {}
        loading = false;
    }
    async function openEditor() {
        try {
            goalsMd = (await api.goalsRaw()).markdown;
            editorOpen = true;
        } catch (e) {
            saveStatus = `Error cargando: ${(e as Error).message}`;
        }
    }
    async function saveGoals() {
        savingGoals = true;
        try {
            await api.putGoalsRaw(goalsMd);
            saveStatus = 'guardado ✓';
            await refresh();
            setTimeout(() => { editorOpen = false; saveStatus = ''; }, 800);
        } catch (e) {
            saveStatus = `error: ${(e as Error).message}`;
        }
        savingGoals = false;
    }

    onMount(async () => { await refresh(); });

    async function setWindow(d: number | null) {
        windowDays = d;
        await refresh();
    }

    function deadlineTone(days: number | null): string {
        if (days === null) return 'text-zinc-500';
        if (days < 0)  return 'text-fuchsia-200';
        if (days <= 7) return 'text-amber-200';
        return 'text-zinc-400';
    }

    function progressColor(p: number | null): string {
        if (p === null) return 'from-zinc-500/60 to-zinc-400/60';
        if (p >= 80) return 'from-cyan-400/80 to-cyan-300/60';
        if (p >= 40) return 'from-amber-300/70 to-amber-200/50';
        return 'from-fuchsia-400/60 to-fuchsia-300/40';
    }

    let topTool = $derived(economy?.by_tool[0]);
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">trajectory</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Goals &amp; economy</h1>
                <p class="text-sm text-zinc-500 mt-1">Lo que importa, lo que cuesta.</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="active goals" value={goals.length} />
                {#if economy}
                    <Stat label="spent" value={Number(economy.total_spent_usd.toFixed(4))} unit="usd" accent="gold" />
                    <Stat label="paid calls" value={economy.total_calls} accent="cyan" />
                {/if}
            </div>
        </header>
        <!-- Window selector + refresh -->
        <div class="mb-5 flex items-center gap-1 hairline-b pb-3">
            {#each [{ d: 1, label: 'today' }, { d: 7, label: '7d' }, { d: 30, label: '30d' }, { d: null, label: 'lifetime' }] as opt (opt.label)}
                <button
                    onclick={() => setWindow(opt.d)}
                    class="px-4 py-1.5 text-[0.78rem] tracking-wide rounded-full transition-all
                        {windowDays === opt.d
                            ? 'bg-white/[0.04] text-zinc-100 hairline'
                            : 'text-zinc-500 hover:text-zinc-300'}">
                    {opt.label}
                </button>
            {/each}
            <button onclick={openEditor}
                class="ml-auto mono text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 px-3 py-1.5 hairline rounded-lg"
                title="Edit goals.md">edit goals.md</button>
            <button onclick={refresh}
                class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 px-3 py-1.5 ml-1"
                title="Refresh">↻</button>
        </div>

        <div class="grid gap-4 lg:grid-cols-5">
            <!-- Goals — wider column -->
            <div class="lg:col-span-3 space-y-3">
                {#if loading}
                    {#each Array(3) as _, i (i)}
                        <div class="glass rounded-2xl p-5 space-y-3">
                            <div class="h-5 w-1/2 skeleton"></div>
                            <div class="h-2 w-full skeleton"></div>
                        </div>
                    {/each}
                {/if}
                {#each goals as g (g.title)}
                    <article class="glass rounded-2xl p-5 lift">
                        <header class="flex items-baseline justify-between gap-4 mb-3">
                            <h3 class="text-[0.95rem] font-medium text-zinc-100 tracking-tight">{g.title}</h3>
                            <span class="mono text-[0.7rem] tabular {deadlineTone(g.days_left)} flex-shrink-0">
                                {g.deadline ?? '—'}
                                {#if g.days_left !== null}
                                    <span class="text-zinc-700">·</span> {g.days_left}d
                                {/if}
                            </span>
                        </header>

                        {#if g.progress_pct !== null}
                            <div class="mb-1 h-1 w-full overflow-hidden rounded-full bg-white/[0.04]">
                                <div
                                    class="h-full rounded-full bg-gradient-to-r {progressColor(g.progress_pct)}"
                                    style="width: {g.progress_pct}%; transition: width 1s cubic-bezier(0.2, 0.8, 0.2, 1);"
                                ></div>
                            </div>
                            <div class="flex items-baseline justify-between mb-2">
                                <span class="mono text-[0.65rem] text-zinc-600 tabular">{g.progress_pct}%</span>
                                {#if g.project}
                                    <span class="mono text-[0.6rem] text-zinc-600 uppercase tracking-wider">{g.project}</span>
                                {/if}
                            </div>
                        {/if}

                        {#if g.notes?.length}
                            <ul class="mt-2 space-y-1">
                                {#each g.notes as n (n)}
                                    <li class="text-[0.78rem] text-zinc-400 flex gap-2">
                                        <span class="text-zinc-700">·</span>
                                        <span>{n}</span>
                                    </li>
                                {/each}
                            </ul>
                        {/if}
                    </article>
                {/each}
                {#if !loading && goals.length === 0}
                    <div class="glass rounded-2xl p-12 text-center">
                        <p class="text-lg font-light text-zinc-300 tracking-tight">No hay goals activos.</p>
                        <p class="text-xs text-zinc-600 mt-2">Edita <span class="mono text-zinc-500">vault/config/goals.md</span></p>
                    </div>
                {/if}
            </div>

            <!-- Economy column -->
            <div class="lg:col-span-2 space-y-4">
                <Glass eyebrow="ledger" title="Por herramienta">
                    {#if economy?.by_tool.length}
                        <ul class="space-y-2.5">
                            {#each economy.by_tool.slice(0, 8) as b (b.tool)}
                                {@const pct = topTool ? (b.spent_usd / topTool.spent_usd) * 100 : 0}
                                <li>
                                    <div class="flex justify-between text-[0.78rem] mb-1">
                                        <span class="mono text-zinc-300">{b.tool}</span>
                                        <span class="mono text-amber-200/80 tabular">${b.spent_usd.toFixed(4)}</span>
                                    </div>
                                    <div class="flex items-center gap-2">
                                        <div class="flex-1 h-px bg-white/[0.04]">
                                            <div class="h-px bg-amber-300/40" style="width: {pct}%;"></div>
                                        </div>
                                        <span class="mono text-[0.6rem] text-zinc-600 tabular w-8 text-right">{b.calls}</span>
                                    </div>
                                </li>
                            {/each}
                        </ul>
                    {:else}
                        <p class="text-[0.78rem] text-zinc-500">Sin actividad económica registrada.</p>
                    {/if}
                </Glass>

                <Glass eyebrow="models" title="Por modelo">
                    {#if economy?.by_model.length}
                        <ul class="space-y-2 text-[0.78rem]">
                            {#each economy.by_model as b (b.model)}
                                <li class="flex justify-between">
                                    <span class="mono text-zinc-300">{b.model}</span>
                                    <span class="mono text-zinc-500 tabular">
                                        <span class="text-amber-200/80">${b.spent_usd.toFixed(4)}</span>
                                        <span class="text-zinc-700">·</span> {b.calls}
                                    </span>
                                </li>
                            {/each}
                        </ul>
                    {:else}
                        <p class="text-[0.78rem] text-zinc-500">Sin uso de modelos pagos.</p>
                    {/if}
                </Glass>
            </div>
        </div>
    </div>

    <!-- goals.md editor -->
    {#if editorOpen}
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm" onclick={() => (editorOpen = false)} role="presentation">
            <div class="glass rounded-2xl max-w-3xl w-full mx-6 max-h-[85vh] flex flex-col" onclick={(e) => e.stopPropagation()} role="presentation">
                <header class="flex items-baseline justify-between px-6 pt-5 pb-3 hairline-b">
                    <div>
                        <span class="eyebrow">vault/config</span>
                        <h3 class="mono text-lg text-cyan-200 mt-0.5">goals.md</h3>
                    </div>
                    <div class="flex items-center gap-3">
                        {#if saveStatus}
                            <span class="text-[0.7rem] text-amber-200">{saveStatus}</span>
                        {/if}
                        <button onclick={() => (editorOpen = false)} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
                    </div>
                </header>
                <div class="flex-1 overflow-auto p-4">
                    <textarea bind:value={goalsMd}
                        class="w-full h-full mono text-[0.78rem] text-zinc-200 leading-relaxed bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none p-3 rounded-lg resize-none"
                        style="min-height: 500px;"
                        spellcheck="false"></textarea>
                </div>
                <div class="flex justify-end gap-2 hairline-t px-6 py-3">
                    <button onclick={() => (editorOpen = false)}
                        class="mono text-[0.7rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-200 px-4 py-2">cancelar</button>
                    <button onclick={saveGoals} disabled={savingGoals}
                        class="mono text-[0.7rem] uppercase tracking-wider text-cyan-200 bg-cyan-400/10 hairline border-cyan-400/30 hover:bg-cyan-400/20 px-4 py-2 rounded-lg disabled:opacity-30">
                        {savingGoals ? 'guardando…' : 'guardar'}
                    </button>
                </div>
            </div>
        </div>
    {/if}
</div>
