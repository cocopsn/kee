<script lang="ts">
    import { onMount } from 'svelte';
    import { api, type ConversationRow, type MessageRow } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    let convs = $state<ConversationRow[]>([]);
    let loading = $state(true);
    let activeId = $state<string | null>(null);
    let activeMessages = $state<MessageRow[]>([]);
    let activeLoading = $state(false);
    let sourceFilter = $state<string>('all');
    let textFilter = $state('');
    let summarizing = $state<Record<string, boolean>>({});

    async function refresh() {
        try { convs = (await api.conversations(80)).rows; } catch {}
        loading = false;
    }
    async function open(id: string) {
        activeId = id;
        activeLoading = true;
        try {
            const r = await api.conversationDetail(id);
            activeMessages = r.messages;
        } catch (e) {
            activeMessages = [{ role: 'error', content: (e as Error).message }];
        }
        activeLoading = false;
    }
    async function summarize(id: string) {
        summarizing[id] = true;
        try {
            await api.summarizeOne(id);
            await refresh();
        } catch {}
        summarizing[id] = false;
    }

    onMount(refresh);

    let sources = $derived([...new Set(convs.map(c => c.source))].sort());
    let filtered = $derived(convs.filter(c => {
        if (sourceFilter !== 'all' && c.source !== sourceFilter) return false;
        if (textFilter && !(c.summary ?? '').toLowerCase().includes(textFilter.toLowerCase())
            && !c.id.includes(textFilter)) return false;
        return true;
    }));
    let withSummary = $derived(convs.filter(c => c.summary).length);

    function fmtTs(s: string): string {
        try {
            const d = new Date(s);
            const today = new Date();
            return d.toDateString() === today.toDateString()
                ? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
                : d.toLocaleString('es-MX', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
        } catch { return s; }
    }
    function sourceTone(s: string): string {
        return ({telegram:'cyan', voice:'amber', api:'violet', terminal:'zinc'} as any)[s] ?? 'zinc';
    }
</script>

<div class="h-full flex flex-col">
    <div class="px-6 py-4 hairline-b">
        <div class="flex items-end gap-8">
            <div>
                <span class="eyebrow">past</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Conversations</h1>
                <p class="text-sm text-zinc-500 mt-1">Toda conversación con Kee, cualquier surface.</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="total" value={convs.length} />
                <Stat label="summarized" value={withSummary} accent="cyan" />
                <Stat label="filtered" value={filtered.length} accent="gold" />
            </div>
        </div>
        <div class="flex items-center gap-2 mt-3 flex-wrap">
            <span class="mono text-[0.6rem] uppercase tracking-wider text-zinc-600 mr-2">source</span>
            <button onclick={() => (sourceFilter = 'all')}
                class="px-3 py-1 text-[0.7rem] rounded-full
                    {sourceFilter === 'all' ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">all</button>
            {#each sources as s (s)}
                {@const tone = sourceTone(s)}
                <button onclick={() => (sourceFilter = s)}
                    class="px-3 py-1 text-[0.7rem] mono rounded-full
                        {sourceFilter === s ? 'bg-white/[0.04] text-zinc-100 hairline' : `text-${tone}-300 hover:text-${tone}-200`}">{s}</button>
            {/each}
            <input bind:value={textFilter} placeholder="search summary or id…"
                class="ml-3 mono text-[0.7rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none placeholder:text-zinc-700 w-60"/>
        </div>
    </div>
    <div class="flex-1 grid grid-cols-12 min-h-0">
        <!-- List -->
        <div class="col-span-5 hairline border-r overflow-y-auto p-4 space-y-2" style="border-right: 1px solid var(--color-hairline);">
            {#if loading}
                {#each Array(5) as _, i (i)}
                    <div class="h-10 skeleton mb-1"></div>
                {/each}
            {/if}
            {#each filtered as c (c.id)}
                {@const tone = sourceTone(c.source)}
                <article class="rounded-xl px-3 py-2.5 hairline lift hover:border-cyan-400/30 transition-colors group
                    {activeId === c.id ? 'bg-cyan-400/05 border-cyan-400/40' : ''}">
                    <button onclick={() => open(c.id)} class="w-full text-left">
                        <div class="flex items-baseline gap-2 mb-1">
                            <span class="chip mono text-[0.55rem]
                                {tone === 'cyan' ? 'bg-cyan-400/10 text-cyan-200'
                                    : tone === 'amber' ? 'bg-amber-300/10 text-amber-200'
                                    : tone === 'violet' ? 'bg-violet-400/10 text-violet-200'
                                    : 'bg-zinc-700/30 text-zinc-300'}">{c.source}</span>
                            <span class="mono text-[0.6rem] text-zinc-600 tabular">{fmtTs(c.last_active)}</span>
                            <span class="mono text-[0.55rem] text-zinc-700 tabular ml-auto">#{c.id.slice(0, 6)}</span>
                        </div>
                        {#if c.summary}
                            <p class="text-[0.78rem] text-zinc-300 leading-snug line-clamp-2">{c.summary}</p>
                        {:else}
                            <p class="text-[0.7rem] text-zinc-500 italic">Sin resumen aún. <button onclick={(e) => { e.stopPropagation(); summarize(c.id); }} class="text-cyan-400 hover:text-cyan-200">summarize ↻</button></p>
                        {/if}
                    </button>
                </article>
            {/each}
        </div>
        <!-- Detail viewer -->
        <div class="col-span-7 overflow-y-auto p-5 space-y-3">
            {#if !activeId}
                <div class="flex h-full items-center justify-center text-zinc-500 text-sm">Selecciona una conversación</div>
            {:else if activeLoading}
                <div class="h-32 skeleton"></div>
            {:else}
                {#each activeMessages as m, i (i)}
                    {#if m.role === 'system'}
                        <details class="text-[0.7rem] text-zinc-600">
                            <summary class="mono uppercase tracking-wider cursor-pointer hover:text-zinc-400">system block ({m.content.length} chars)</summary>
                            <pre class="mt-2 mono text-[0.65rem] text-zinc-500 whitespace-pre-wrap">{m.content.slice(0, 2000)}{m.content.length > 2000 ? '…' : ''}</pre>
                        </details>
                    {:else if m.role === 'user'}
                        <div class="flex gap-2">
                            <span class="chip bg-amber-300/10 text-amber-200 text-[0.6rem] flex-shrink-0">user</span>
                            <p class="text-[0.85rem] text-zinc-200 whitespace-pre-wrap">{m.content}</p>
                        </div>
                    {:else if m.role === 'assistant'}
                        <div class="flex gap-2">
                            <span class="chip bg-cyan-400/10 text-cyan-200 text-[0.6rem] flex-shrink-0">kee</span>
                            <p class="text-[0.85rem] text-zinc-300 whitespace-pre-wrap">{m.content}</p>
                        </div>
                    {:else if m.tool_name}
                        <div class="flex gap-2 text-[0.7rem]">
                            <span class="chip bg-violet-400/10 text-violet-200 text-[0.55rem] flex-shrink-0">tool</span>
                            <span class="mono text-zinc-400">{m.tool_name}</span>
                        </div>
                    {/if}
                {/each}
            {/if}
        </div>
    </div>
</div>
