<script lang="ts">
    import { onMount } from 'svelte';
    import { API_BASE, api } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';

    type CostRow = {
        ts: string;
        provider: string;
        model_name: string;
        tier?: string;
        cost_usd: number;
        tokens_in?: number;
        tokens_out?: number;
        latency_ms?: number;
    };

    let cost = $state<any>(null);
    let recent = $state<CostRow[]>([]);
    let loading = $state(true);
    let error = $state<string | null>(null);

    async function refresh() {
        try {
            cost = await api.llmCost();
            // Pull recent rows via /economy if available
            const r = await fetch(`${API_BASE}/audit?action=tool_call&limit=50`);
            if (r.ok) {
                const data = await r.json();
                recent = (data.rows || [])
                    .filter((x: any) => x.cost_usd && x.cost_usd > 0)
                    .slice(0, 30);
            }
        } catch (e: any) {
            error = e.message;
        } finally {
            loading = false;
        }
    }

    onMount(() => {
        refresh();
        const t = setInterval(refresh, 8000);
        return () => clearInterval(t);
    });

    function fmtUsd(n: number | null | undefined): string {
        if (n === null || n === undefined) return '—';
        return `$${n.toFixed(4)}`;
    }
</script>

<div class="h-full overflow-y-auto px-6 py-6 max-w-5xl mx-auto">
    <div class="mb-5">
        <h1 class="text-zinc-200 text-lg tracking-wide mb-1">Cost</h1>
        <p class="text-zinc-500 text-[0.78rem]">
            LLM spend tracker — daily total, kill-switch state, last 30
            paid calls. Local Ollama + remote Ollama don't show ($0).
        </p>
    </div>

    {#if error}
        <p class="text-rose-300 text-[0.85rem] mb-4">{error}</p>
    {/if}

    {#if cost}
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 mb-2">Today</h2>
                    <p class="text-zinc-100 text-3xl tabular tracking-tight">
                        {fmtUsd(cost.today?.today_usd)}
                    </p>
                    <p class="mono text-[0.65rem] text-zinc-500 mt-1.5">
                        cap = {fmtUsd(cost.today?.cap_usd)} ({(cost.today?.cap_pct || 0).toFixed(0)}%)
                    </p>
                </div>
            </Glass>

            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 mb-2">Status</h2>
                    {#if cost.today?.kill_active}
                        <p class="text-fuchsia-300 text-base">⚠ kill switch active</p>
                        <p class="mono text-[0.65rem] text-zinc-500 mt-1.5">forcing ollama until midnight</p>
                    {:else if cost.today?.near_cap}
                        <p class="text-amber-300 text-base">○ near cap</p>
                        <p class="mono text-[0.65rem] text-zinc-500 mt-1.5">{(100 - (cost.today?.cap_pct || 0)).toFixed(0)}% remaining</p>
                    {:else}
                        <p class="text-emerald-300 text-base">● healthy</p>
                        <p class="mono text-[0.65rem] text-zinc-500 mt-1.5">well below cap</p>
                    {/if}
                </div>
            </Glass>

            <Glass>
                <div class="p-4">
                    <h2 class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 mb-2">7-day trend</h2>
                    <p class="text-zinc-100 text-2xl tabular tracking-tight">
                        {fmtUsd(cost.week?.total_usd)}
                    </p>
                    <p class="mono text-[0.65rem] text-zinc-500 mt-1.5">
                        avg/day {fmtUsd((cost.week?.total_usd || 0) / 7)}
                    </p>
                </div>
            </Glass>
        </div>
    {/if}

    {#if recent.length > 0}
        <Glass>
            <div class="p-4">
                <h2 class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 mb-3">
                    Recent paid calls ({recent.length})
                </h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-[0.78rem]">
                        <thead>
                            <tr class="text-zinc-600 mono text-[0.6rem] uppercase tracking-wider">
                                <th class="text-left py-1 px-2">when</th>
                                <th class="text-left py-1 px-2">provider</th>
                                <th class="text-left py-1 px-2">model</th>
                                <th class="text-right py-1 px-2">cost</th>
                                <th class="text-right py-1 px-2">in/out</th>
                                <th class="text-right py-1 px-2">ms</th>
                            </tr>
                        </thead>
                        <tbody>
                            {#each recent as r}
                                <tr class="border-t border-zinc-900 hover:bg-white/[0.02]">
                                    <td class="py-1.5 px-2 mono text-zinc-500 tabular">
                                        {new Date(r.ts).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}
                                    </td>
                                    <td class="py-1.5 px-2 text-zinc-300">{r.provider || '—'}</td>
                                    <td class="py-1.5 px-2 text-zinc-400 mono text-[0.7rem]">{r.model_name || '—'}</td>
                                    <td class="py-1.5 px-2 text-right tabular text-cyan-200">{fmtUsd(r.cost_usd)}</td>
                                    <td class="py-1.5 px-2 text-right mono text-zinc-500 tabular text-[0.7rem]">
                                        {r.tokens_in || 0}/{r.tokens_out || 0}
                                    </td>
                                    <td class="py-1.5 px-2 text-right mono text-zinc-600 tabular text-[0.7rem]">
                                        {r.latency_ms || ''}
                                    </td>
                                </tr>
                            {/each}
                        </tbody>
                    </table>
                </div>
            </div>
        </Glass>
    {:else if !loading}
        <p class="text-zinc-600 text-center mt-12 text-[0.78rem]">
            No paid LLM calls yet.
        </p>
    {/if}
</div>
