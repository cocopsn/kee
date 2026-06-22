<script lang="ts">
    import { onMount } from 'svelte';
    import { API_BASE } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';

    type Hit = {
        snippet: string;
        similarity: number | null;
        metadata: {
            kind: string;
            ref: string | number | null;
            ts?: string;
            project?: string;
            source?: string;
            window_title?: string;
            executed?: boolean;
            urgency?: number;
            reinforced?: number;
        };
    };

    let q = $state('');
    let kindsFilter = $state<string[]>([]);
    let n = $state(8);
    let loading = $state(false);
    let error = $state<string | null>(null);
    let hits = $state<Hit[]>([]);
    let totalCount = $state(0);
    let elapsedMs = $state(0);

    const KIND_OPTIONS = [
        'conversation', 'dispatch', 'plan', 'focus',
        'learning', 'notification', 'perception',
    ];

    async function search() {
        if (!q.trim()) return;
        loading = true;
        error = null;
        const t0 = performance.now();
        try {
            const params = new URLSearchParams({ q, n: String(n) });
            if (kindsFilter.length) params.set('kinds', kindsFilter.join(','));
            const res = await fetch(`${API_BASE}/episodic/query?${params}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (!data.ok) {
                error = data.reason || 'unknown error';
                hits = [];
            } else {
                hits = data.hits || [];
                totalCount = data.count || 0;
            }
        } catch (e: any) {
            error = e.message;
            hits = [];
        } finally {
            elapsedMs = Math.round(performance.now() - t0);
            loading = false;
        }
    }

    function toggleKind(k: string) {
        if (kindsFilter.includes(k)) kindsFilter = kindsFilter.filter(x => x !== k);
        else kindsFilter = [...kindsFilter, k];
    }

    async function reindex() {
        loading = true;
        try {
            const res = await fetch(`${API_BASE}/episodic/reindex?window_days=7`, { method: 'POST' });
            const data = await res.json();
            error = data.offline
                ? 'worker offline'
                : `re-indexed ${data.indexed} events across ${Object.keys(data.by_source || {}).length} sources`;
        } catch (e: any) {
            error = e.message;
        } finally {
            loading = false;
        }
    }

    function fmtTs(ts: string | undefined): string {
        if (!ts) return '';
        try {
            return new Date(ts).toLocaleString('es-MX', {
                month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit',
            });
        } catch {
            return ts;
        }
    }

    function kindColor(k: string): string {
        return ({
            conversation: 'cyan',
            dispatch: 'amber',
            plan: 'fuchsia',
            focus: 'emerald',
            learning: 'violet',
            notification: 'rose',
            perception: 'sky',
        } as Record<string, string>)[k] || 'zinc';
    }

    onMount(() => {
        // Auto-search a sample query so the page isn't empty on first load
        // (only if user hasn't typed anything yet)
    });
</script>

<div class="h-full overflow-y-auto px-6 py-6 max-w-5xl mx-auto">
    <div class="mb-5">
        <h1 class="text-zinc-200 text-lg tracking-wide mb-1">Episodic memory</h1>
        <p class="text-zinc-500 text-[0.78rem]">
            Búsqueda semántica unificada sobre conversaciones, dispatches, planes,
            focus sessions, learnings, notificaciones y perception events.
        </p>
    </div>

    <Glass>
        <div class="p-4">
            <div class="flex gap-2 items-center mb-3">
                <input
                    type="text"
                    bind:value={q}
                    onkeydown={(e) => e.key === 'Enter' && search()}
                    placeholder="qué buscas — e.g. 'auctorum stripe', 'focus de ayer'"
                    class="flex-1 bg-zinc-950/60 border border-zinc-800 rounded px-3 py-2 text-[0.85rem] text-zinc-200 placeholder-zinc-600 focus:border-cyan-700 focus:outline-none"
                />
                <button
                    onclick={search}
                    disabled={loading || !q.trim()}
                    class="px-4 py-2 bg-cyan-600/20 border border-cyan-700 text-cyan-200 text-[0.78rem] rounded hover:bg-cyan-600/30 disabled:opacity-40 disabled:cursor-not-allowed">
                    {loading ? '…' : 'query'}
                </button>
                <button
                    onclick={reindex}
                    disabled={loading}
                    class="px-3 py-2 bg-zinc-800/40 border border-zinc-700 text-zinc-400 text-[0.7rem] rounded hover:bg-zinc-800/60">
                    re-index
                </button>
            </div>

            <div class="flex gap-1.5 flex-wrap items-center">
                <span class="mono text-[0.6rem] text-zinc-600 mr-1">kind:</span>
                {#each KIND_OPTIONS as k}
                    {@const color = kindColor(k)}
                    {@const active = kindsFilter.includes(k)}
                    <button
                        onclick={() => toggleKind(k)}
                        class="mono text-[0.6rem] px-2 py-0.5 rounded border tracking-wider uppercase
                            {active ? `bg-${color}-500/20 border-${color}-700 text-${color}-200`
                                    : 'border-zinc-800 text-zinc-600 hover:text-zinc-300'}">
                        {k}
                    </button>
                {/each}
            </div>
        </div>
    </Glass>

    {#if error}
        <p class="text-rose-300 text-[0.78rem] mt-3 px-2">{error}</p>
    {/if}

    {#if hits.length > 0}
        <div class="mt-4 mb-3 flex items-baseline justify-between">
            <span class="mono text-[0.65rem] text-zinc-500 tracking-wider uppercase">
                {totalCount} hits — {elapsedMs}ms round-trip
            </span>
        </div>
        <div class="space-y-2.5">
            {#each hits as h, i}
                {@const m = h.metadata}
                {@const color = kindColor(m.kind)}
                <Glass>
                    <div class="p-4">
                        <div class="flex items-center gap-2 mb-2">
                            <span class="mono text-[0.55rem] uppercase tracking-wider px-2 py-0.5 rounded bg-{color}-500/15 text-{color}-200">
                                {m.kind}
                            </span>
                            {#if m.ref}
                                <span class="mono text-[0.6rem] text-zinc-600">#{m.ref}</span>
                            {/if}
                            {#if m.project}
                                <span class="chip text-[0.6rem] bg-amber-500/10 text-amber-200">{m.project}</span>
                            {/if}
                            {#if m.executed === true}
                                <span class="chip text-[0.6rem] bg-emerald-500/10 text-emerald-200">executed</span>
                            {:else if m.executed === false}
                                <span class="chip text-[0.6rem] bg-zinc-700/40 text-zinc-400">pending</span>
                            {/if}
                            {#if h.similarity !== null}
                                <span class="ml-auto mono text-[0.6rem] tabular text-zinc-500">sim={h.similarity?.toFixed(3)}</span>
                            {/if}
                            {#if m.ts}
                                <span class="mono text-[0.6rem] text-zinc-600">{fmtTs(m.ts)}</span>
                            {/if}
                        </div>
                        <p class="text-[0.85rem] text-zinc-300 leading-relaxed">{h.snippet}</p>
                    </div>
                </Glass>
            {/each}
        </div>
    {:else if !loading && q && !error}
        <p class="text-zinc-500 text-[0.78rem] mt-6 text-center">
            Sin resultados para "{q}". Prueba palabras más generales o el botón <em>re-index</em>.
        </p>
    {/if}
</div>
