<script lang="ts">
    import { onMount } from 'svelte';
    import { API_BASE } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';

    function todayIso(): string {
        return new Date().toISOString().slice(0, 10);
    }

    let dateInput = $state(todayIso());
    let markdown = $state('');
    let counts = $state<Record<string, number>>({});
    let loading = $state(false);
    let error = $state<string | null>(null);
    let elapsedMs = $state(0);

    async function load(d: string = dateInput) {
        loading = true;
        error = null;
        const t0 = performance.now();
        try {
            const res = await fetch(`${API_BASE}/narrate/${encodeURIComponent(d)}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (!data.ok) {
                error = data.error || 'unknown error';
                markdown = '';
                counts = {};
            } else {
                markdown = data.markdown || '';
                counts = data.counts || {};
            }
        } catch (e: any) {
            error = e.message;
            markdown = '';
            counts = {};
        } finally {
            elapsedMs = Math.round(performance.now() - t0);
            loading = false;
        }
    }

    function shiftDay(delta: number) {
        const d = new Date(dateInput);
        d.setDate(d.getDate() + delta);
        dateInput = d.toISOString().slice(0, 10);
        load();
    }

    onMount(() => load('today'));
</script>

<div class="h-full overflow-y-auto px-6 py-6 max-w-4xl mx-auto">
    <div class="mb-5">
        <h1 class="text-zinc-200 text-lg tracking-wide mb-1">Diary</h1>
        <p class="text-zinc-500 text-[0.78rem]">
            Línea de tiempo cronológica de cualquier día — commits, dispatches,
            planes, focus, notificaciones, perception, conversaciones.
            Determinístico, sin LLM.
        </p>
    </div>

    <Glass>
        <div class="p-4 flex gap-2 items-center flex-wrap">
            <button
                onclick={() => shiftDay(-1)}
                class="mono text-[0.7rem] px-3 py-1.5 bg-zinc-800/40 border border-zinc-700 text-zinc-400 rounded hover:text-zinc-200">
                ← prev
            </button>
            <input
                type="date"
                bind:value={dateInput}
                onchange={() => load()}
                class="bg-zinc-950/60 border border-zinc-800 rounded px-3 py-1.5 mono text-[0.78rem] text-zinc-200"
            />
            <button
                onclick={() => shiftDay(1)}
                class="mono text-[0.7rem] px-3 py-1.5 bg-zinc-800/40 border border-zinc-700 text-zinc-400 rounded hover:text-zinc-200">
                next →
            </button>
            <button
                onclick={() => { dateInput = todayIso(); load(); }}
                class="mono text-[0.7rem] px-3 py-1.5 bg-cyan-600/20 border border-cyan-700 text-cyan-200 rounded hover:bg-cyan-600/30">
                today
            </button>
            <span class="ml-auto mono text-[0.6rem] text-zinc-600">
                {loading ? 'loading…' : `${elapsedMs}ms`}
            </span>
        </div>
    </Glass>

    {#if error}
        <p class="text-rose-300 text-[0.78rem] mt-3 px-2">{error}</p>
    {/if}

    {#if Object.keys(counts).length > 0}
        <div class="mt-4 flex gap-2 flex-wrap">
            {#each Object.entries(counts) as [k, v]}
                {#if v > 0}
                    <span class="mono text-[0.65rem] px-2.5 py-1 rounded bg-zinc-800/40 border border-zinc-700/50 text-zinc-300 tabular">
                        <span class="text-zinc-500">{k}</span>
                        <span class="ml-1.5 text-cyan-200">{v}</span>
                    </span>
                {/if}
            {/each}
        </div>
    {/if}

    {#if markdown}
        <Glass>
            <div class="p-5">
                <pre class="text-[0.78rem] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono">{markdown}</pre>
            </div>
        </Glass>
    {/if}

    {#if !loading && !markdown && !error}
        <p class="text-zinc-600 text-center mt-12 text-[0.78rem]">No hay eventos registrados para {dateInput}.</p>
    {/if}
</div>

<style>
    .glass + .glass { margin-top: 1rem; }
</style>
