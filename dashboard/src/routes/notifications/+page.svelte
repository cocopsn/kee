<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api, openStream, type NotificationRow } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';

    let rows = $state<NotificationRow[]>([]);
    let loading = $state(true);
    let directionFilter = $state<'all' | 'inbound' | 'outbound'>('all');
    let sourceFilter = $state<string>('all');
    let handledFilter = $state<'all' | 'unread' | 'handled'>('all');
    let limit = $state(100);
    let unsub: (() => void) | null = null;

    // Inbound test composer
    let testSource = $state('webhook');
    let testTitle = $state('');
    let testBody = $state('');
    let testUrgency = $state(1);
    let testStatus = $state('');

    async function refresh() {
        const opts: any = { limit };
        if (directionFilter !== 'all') opts.direction = directionFilter;
        if (sourceFilter !== 'all') opts.source = sourceFilter;
        if (handledFilter === 'unread') opts.handled = false;
        if (handledFilter === 'handled') opts.handled = true;
        try { rows = (await api.notifications(opts)).rows; } catch {}
        loading = false;
    }

    let sources = $derived([...new Set(rows.map(r => r.source))].sort());
    let inboundCount = $derived(rows.filter(r => r.direction === 'inbound').length);
    let outboundCount = $derived(rows.filter(r => r.direction === 'outbound').length);
    let unreadCount = $derived(rows.filter(r => r.direction === 'inbound' && !r.handled).length);
    let criticalCount = $derived(rows.filter(r => r.urgency >= 2 && !r.handled).length);

    onMount(() => {
        refresh();
        const t = setInterval(refresh, 5000);
        unsub = openStream((e) => {
            if ((e as any).type === 'notification_inbound') refresh();
        });
        return () => clearInterval(t);
    });
    onDestroy(() => unsub?.());

    async function markOne(id: number) {
        await api.notificationsMarkHandled(id);
        await refresh();
    }
    async function markAll() {
        await api.notificationsHandleAll();
        await refresh();
    }
    async function sendTest() {
        if (!testBody.trim()) return;
        testStatus = 'enviando…';
        try {
            await api.notificationsInbound({
                source: testSource.trim() || 'webhook',
                title: testTitle.trim() || undefined,
                body: testBody.trim(),
                urgency: testUrgency,
            });
            testStatus = 'inyectado ✓';
            testBody = '';
            testTitle = '';
            await refresh();
        } catch (e) {
            testStatus = `error: ${(e as Error).message}`;
        }
        setTimeout(() => (testStatus = ''), 3000);
    }

    function urgencyTone(u: number): string {
        return u >= 2 ? 'fuchsia' : u === 0 ? 'zinc' : 'cyan';
    }
    function urgencyLabel(u: number): string {
        return u >= 2 ? 'critical' : u === 0 ? 'low' : 'normal';
    }
    function fmtTs(s: string): string {
        try {
            const d = new Date(s);
            const today = new Date();
            const sameDay = d.toDateString() === today.toDateString();
            return sameDay
                ? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                : d.toLocaleString('es-MX', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
        } catch { return s; }
    }
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">inbox</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Notifications</h1>
                <p class="text-sm text-zinc-500 mt-1">Inbound (de fuera) + outbound (Kee → tú).</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="inbound" value={inboundCount} />
                <Stat label="outbound" value={outboundCount} accent="cyan" />
                <Stat label="unread" value={unreadCount} accent={unreadCount > 0 ? 'gold' : 'plain'} />
                <Stat label="critical" value={criticalCount} accent={criticalCount > 0 ? 'gold' : 'plain'} />
            </div>
        </header>

        <!-- Filter strip -->
        <div class="mb-5 flex items-center gap-1 hairline-b pb-3 flex-wrap">
            <span class="mono text-[0.6rem] uppercase tracking-wider text-zinc-600 mr-2">direction</span>
            {#each ['all', 'inbound', 'outbound'] as d (d)}
                <button onclick={() => { directionFilter = d as any; refresh(); }}
                    class="px-3 py-1 text-[0.7rem] tracking-wide rounded-full transition-all
                        {directionFilter === d
                            ? 'bg-white/[0.04] text-zinc-100 hairline'
                            : 'text-zinc-500 hover:text-zinc-300'}">{d}</button>
            {/each}

            <span class="mono text-[0.6rem] uppercase tracking-wider text-zinc-600 mx-2 ml-6">read</span>
            {#each ['all', 'unread', 'handled'] as h (h)}
                <button onclick={() => { handledFilter = h as any; refresh(); }}
                    class="px-3 py-1 text-[0.7rem] tracking-wide rounded-full transition-all
                        {handledFilter === h
                            ? 'bg-white/[0.04] text-zinc-100 hairline'
                            : 'text-zinc-500 hover:text-zinc-300'}">{h}</button>
            {/each}

            {#if sources.length > 0}
                <span class="mono text-[0.6rem] uppercase tracking-wider text-zinc-600 mx-2 ml-6">source</span>
                <button onclick={() => { sourceFilter = 'all'; refresh(); }}
                    class="px-3 py-1 text-[0.7rem] rounded-full
                        {sourceFilter === 'all' ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">all</button>
                {#each sources as s (s)}
                    <button onclick={() => { sourceFilter = s; refresh(); }}
                        class="px-3 py-1 text-[0.7rem] mono rounded-full
                            {sourceFilter === s ? 'bg-white/[0.04] text-zinc-100 hairline' : 'text-zinc-500 hover:text-zinc-300'}">{s}</button>
                {/each}
            {/if}

            <button onclick={markAll}
                class="ml-auto mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 px-3 py-1.5 hairline rounded-lg">mark all</button>
            <button onclick={refresh}
                class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 px-3 py-1.5 ml-1">↻</button>
        </div>

        <div class="grid gap-4 lg:grid-cols-4">
            <!-- Notifications list -->
            <div class="lg:col-span-3">
                <Glass eyebrow="signal" title="Recent ({rows.length})" padded={false}>
                    {#if loading}
                        {#each Array(4) as _, i (i)}
                            <div class="px-5 py-3 hairline-b">
                                <div class="h-4 w-2/3 skeleton mb-2"></div>
                                <div class="h-3 w-full skeleton"></div>
                            </div>
                        {/each}
                    {/if}
                    {#if !loading && rows.length === 0}
                        <p class="px-5 py-12 text-center text-[0.78rem] text-zinc-500">Sin notificaciones que coincidan.</p>
                    {/if}
                    {#each rows as n (n.id)}
                        {@const tone = urgencyTone(n.urgency)}
                        <article class="px-5 py-3 hairline-b last:border-b-0 hover:bg-white/[0.015] transition-colors group">
                            <div class="flex items-baseline gap-3 mb-1.5">
                                <PulseDot tone={n.urgency >= 2 ? 'alert' : n.handled ? 'mute' : 'live'} size={6}/>
                                <span class="chip text-[0.6rem]
                                    {n.direction === 'inbound' ? 'bg-cyan-400/10 text-cyan-200' : 'bg-zinc-700/40 text-zinc-400'}">
                                    {n.direction === 'inbound' ? '← in' : '→ out'}
                                </span>
                                <span class="chip mono text-[0.6rem]
                                    {tone === 'fuchsia' ? 'bg-fuchsia-400/10 text-fuchsia-200'
                                        : tone === 'zinc' ? 'bg-zinc-700/40 text-zinc-400'
                                        : 'bg-amber-300/10 text-amber-200'}">{n.source}</span>
                                {#if n.urgency >= 2}
                                    <span class="chip text-[0.6rem] bg-fuchsia-400/15 text-fuchsia-200">{urgencyLabel(n.urgency)}</span>
                                {/if}
                                <span class="ml-auto mono text-[0.65rem] text-zinc-600 tabular">{fmtTs(n.timestamp)}</span>
                                {#if n.direction === 'inbound' && !n.handled}
                                    <button onclick={() => markOne(n.id)}
                                        class="opacity-0 group-hover:opacity-100 mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200 transition-opacity">
                                        mark read
                                    </button>
                                {/if}
                            </div>
                            {#if n.title}
                                <h3 class="text-[0.92rem] text-zinc-100 mb-0.5">{n.title}</h3>
                            {/if}
                            <p class="text-[0.85rem] text-zinc-400 leading-relaxed whitespace-pre-wrap">{n.body}</p>
                        </article>
                    {/each}
                </Glass>
            </div>

            <!-- Inject test inbound + integration help -->
            <div class="lg:col-span-1 space-y-4">
                <Glass eyebrow="inject" title="Push test">
                    <div class="space-y-2">
                        <div>
                            <label class="eyebrow block mb-1">source</label>
                            <input bind:value={testSource}
                                class="w-full mono text-[0.78rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none"
                                placeholder="whatsapp"/>
                        </div>
                        <div>
                            <label class="eyebrow block mb-1">title (opt)</label>
                            <input bind:value={testTitle}
                                class="w-full text-[0.78rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none"
                                placeholder="Maria"/>
                        </div>
                        <div>
                            <label class="eyebrow block mb-1">body</label>
                            <textarea bind:value={testBody} rows="3"
                                class="w-full text-[0.78rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none resize-none"
                                placeholder="el mensaje…"></textarea>
                        </div>
                        <div>
                            <label class="eyebrow block mb-1">urgency</label>
                            <div class="flex gap-2">
                                {#each [{ v: 0, l: 'low' }, { v: 1, l: 'normal' }, { v: 2, l: 'critical' }] as opt (opt.v)}
                                    <button onclick={() => (testUrgency = opt.v)}
                                        class="flex-1 mono text-[0.65rem] py-1.5 rounded-lg uppercase tracking-wider transition-colors
                                            {testUrgency === opt.v
                                                ? (opt.v === 2 ? 'bg-fuchsia-400/15 text-fuchsia-200 hairline border-fuchsia-400/40'
                                                    : opt.v === 0 ? 'bg-zinc-700/40 text-zinc-300'
                                                    : 'bg-cyan-400/10 text-cyan-200 hairline border-cyan-400/40')
                                                : 'text-zinc-500 hairline hover:text-zinc-300'}">
                                        {opt.l}
                                    </button>
                                {/each}
                            </div>
                        </div>
                        <button onclick={sendTest} disabled={!testBody.trim()}
                            class="w-full mono text-[0.7rem] uppercase tracking-wider py-2 rounded-lg bg-cyan-400/10 text-cyan-200 hairline border-cyan-400/30 hover:bg-cyan-400/20 disabled:opacity-30 transition-colors">
                            inject
                        </button>
                        {#if testStatus}
                            <p class="text-[0.7rem] text-zinc-400 italic text-center">{testStatus}</p>
                        {/if}
                    </div>
                </Glass>

                <Glass eyebrow="integrate" title="Cómo conectar">
                    <p class="text-[0.7rem] text-zinc-500 mb-3">Cualquier source puede pushear:</p>
                    <pre class="mono text-[0.65rem] text-zinc-400 bg-white/[0.02] p-3 rounded-lg overflow-x-auto whitespace-pre">POST /notifications/inbound
{"{"}
  "source": "whatsapp",
  "title": "Maria",
  "body": "...",
  "urgency": 1
{"}"}</pre>
                    <p class="text-[0.65rem] text-zinc-600 mt-3">
                        Sources sugeridos: <span class="mono text-zinc-400">whatsapp · slack · gmail · system · webhook · ifttt · zapier</span>
                    </p>
                </Glass>
            </div>
        </div>
    </div>
</div>
