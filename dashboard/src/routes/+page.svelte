<script lang="ts">
    import { onMount } from 'svelte';
    import { api, type ChatResponse, type AttachmentItem, type ConversationSummaryRow } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';
    import NeuralCanvas from '$lib/components/NeuralCanvas.svelte';

    type Msg = { role: 'user' | 'assistant' | 'error'; content: string; ts: string };

    let messages = $state<Msg[]>([]);
    let input = $state('');
    let busy = $state(false);
    let pulse = $state(0);
    let scrollRef: HTMLDivElement | undefined = $state();
    let textareaRef: HTMLTextAreaElement | undefined = $state();
    let fileInputRef: HTMLInputElement | undefined = $state();
    let attachments = $state<AttachmentItem[]>([]);
    let attachStatus = $state('');
    let history = $state<ConversationSummaryRow[]>([]);
    let sidebarOpen = $state(false);
    let detailConv = $state<{ id: string; messages: { role: string; content: string }[] } | null>(null);
    let detailLoading = $state(false);

    async function refreshHistory() {
        try { history = (await api.recentSummaries(15)).rows; } catch {}
    }
    async function openConvDetail(id: string) {
        detailLoading = true;
        detailConv = { id, messages: [] };
        try {
            const r = await api.conversationDetail(id);
            detailConv = { id: r.id, messages: r.messages };
        } catch (e) {
            detailConv = { id, messages: [{ role: 'error', content: (e as Error).message }] };
        }
        detailLoading = false;
    }

    async function refreshAttachments() {
        try {
            const r = await api.listAttachments();
            attachments = r.items;
        } catch {}
    }

    async function onPickFile(e: Event) {
        const input = e.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        attachStatus = `Subiendo ${file.name}…`;
        try {
            const r = await api.attachFile(file);
            attachStatus = `${file.name} (${(r.bytes / 1024).toFixed(1)} KB) anclado`;
            await refreshAttachments();
            setTimeout(() => (attachStatus = ''), 3000);
        } catch (err) {
            attachStatus = `Error: ${(err as Error).message}`;
        }
        input.value = ''; // allow re-picking same file
    }

    async function removeAttachment(name: string) {
        await api.deleteAttachment(name);
        await refreshAttachments();
    }

    function fmtBytes(n: number): string {
        if (n < 1024) return `${n}B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
        return `${(n / (1024 * 1024)).toFixed(2)}MB`;
    }

    let streamCtrl: AbortController | null = null;
    let cmdHelpOpen = $state(false);

    type SlashCmd = { name: string; args?: string; help: string; run: (arg?: string) => Promise<string> | string };
    const slashCmds: SlashCmd[] = [
        {
            name: '/help', help: 'Show available commands',
            run: () => slashCmds.map(c => `${c.name}${c.args ? ' ' + c.args : ''} — ${c.help}`).join('\n'),
        },
        {
            name: '/reset', help: 'Drop the active session and start fresh',
            run: async () => { await api.resetChat(); messages = []; await loadActiveConversation(true); return 'Session reset.'; },
        },
        {
            name: '/clear', help: 'Hide messages locally (keeps server history)',
            run: () => { messages = []; return ''; },
        },
        {
            name: '/cost', help: 'Show today\'s LLM spend',
            run: async () => {
                const c = await api.llmCost();
                const t = c.today;
                return `**Hoy**: \$${t.today_usd.toFixed(4)} / \$${t.cap_usd} (${t.pct_of_cap}% del cap)\n` +
                    Object.entries(c.by_provider).map(([n, info]: any) =>
                        `  ${n}: \$${info.cost_usd.toFixed(4)} · ${info.calls} calls`).join('\n');
            },
        },
        {
            name: '/providers', help: 'Show LLM chain status',
            run: async () => {
                const r = await api.llmProviders();
                return `**Primary**: ${r.primary}\n` +
                    r.providers.map(p =>
                        `  ${p.is_primary ? '★' : '·'} ${p.name} (${p.model}) — ${p.healthy ? '✓' : '✗'}`
                    ).join('\n');
            },
        },
        {
            name: '/proposals', help: 'List identity evolution proposals',
            run: async () => {
                const r = await api.cycleProposals();
                if (!r.proposals.length) return 'No proposals yet. Run /sleep to generate one.';
                return r.proposals.map(p => `  ${p.applied ? '✅' : '🟡'} ${p.date} — ${(p.bytes/1024).toFixed(1)}KB`).join('\n');
            },
        },
        {
            name: '/sleep', help: 'Trigger Sleep Cycle now (free, ollama)',
            run: async () => { await api.cycleRun(); return 'Sleep Cycle running. Check /cycle page in ~30s.'; },
        },
        {
            name: '/inbox', help: 'Show unread notifications count',
            run: async () => {
                const c = await api.notificationsUnreadCount();
                return `${c.count} sin leer en inbox.`;
            },
        },
        {
            name: '/tools', help: 'Show registered tool count',
            run: async () => {
                const t = await api.tools();
                return `${t.count} herramientas registradas. Ver /tools page para detalle.`;
            },
        },
        {
            name: '/now', help: 'Spotify currently playing',
            run: async () => {
                const r = await fetch(`${import.meta.env.VITE_KEE_API ?? 'http://127.0.0.1:7330'}/spotify/now_playing`).then(r => r.json());
                if (r.status === 'ok' && r.is_playing) return `🎵 ${r.track} — ${r.artist}`;
                if (r.status === 'ok') return 'Sin música activa.';
                return r.detail?.error ?? 'Spotify no auth\'d. Setup: SPOTIFY_CLIENT_ID + run `python -m kee.main spotify-auth`.';
            },
        },
    ];

    async function send() {
        const text = input.trim();
        if (!text || busy) return;
        // Handle slash commands locally
        if (text.startsWith('/')) {
            const [cmd, ...rest] = text.split(/\s+/);
            const c = slashCmds.find(c => c.name === cmd);
            if (c) {
                input = '';
                autosize();
                messages.push({ role: 'user', content: text, ts: new Date().toISOString() });
                try {
                    const reply = await c.run(rest.join(' '));
                    if (reply) messages.push({ role: 'assistant', content: reply, ts: new Date().toISOString() });
                } catch (e) {
                    messages.push({ role: 'error', content: `${(e as Error).message}`, ts: new Date().toISOString() });
                }
                scrollSoon();
                return;
            }
            // Unknown slash → show help inline
            messages.push({ role: 'user', content: text, ts: new Date().toISOString() });
            messages.push({ role: 'assistant', content: `Comando desconocido. /help para ver opciones.`, ts: new Date().toISOString() });
            input = '';
            autosize();
            scrollSoon();
            return;
        }
        input = '';
        autosize();
        messages.push({ role: 'user', content: text, ts: new Date().toISOString() });
        // Pre-create the assistant slot — streaming fills it in
        const assistantTs = new Date().toISOString();
        messages.push({ role: 'assistant', content: '', ts: assistantTs });
        const assistantIdx = messages.length - 1;
        busy = true;
        pulse++;
        scrollSoon();

        streamCtrl = api.chatStream(text, {
            onDelta: (chunk) => {
                messages[assistantIdx].content += chunk;
                scrollSoon();
            },
            onReplace: (clean) => {
                messages[assistantIdx].content = clean;
                scrollSoon();
            },
            onDone: () => {
                pulse++;
                busy = false;
                scrollSoon();
                refreshHistory();
                // Re-sync from DB so any server-side post-processing
                // (strip offers, etc.) is reflected in the visible bubble.
                setTimeout(() => loadActiveConversation(true), 200);
            },
            onError: (err) => {
                messages[assistantIdx] = { role: 'error', content: err, ts: assistantTs };
                busy = false;
                scrollSoon();
            },
        });
    }

    function stopStream() {
        streamCtrl?.abort();
        busy = false;
    }

    async function reset() {
        await api.resetChat();
        messages = [];
        await refreshAttachments();
    }

    function scrollSoon() {
        requestAnimationFrame(() => {
            if (scrollRef) scrollRef.scrollTop = scrollRef.scrollHeight;
        });
    }

    function onKey(e: KeyboardEvent) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            send();
        }
    }

    function autosize() {
        if (!textareaRef) return;
        textareaRef.style.height = 'auto';
        textareaRef.style.height = Math.min(textareaRef.scrollHeight, 240) + 'px';
    }

    function timeOf(ts: string): string {
        return new Date(ts).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    }

    async function loadActiveConversation(force = false) {
        // Don't clobber an in-flight stream — the assistant slot is being
        // filled chunk-by-chunk in memory; the DB only has the user msg yet.
        if (busy && !force) return;
        try {
            const r = await api.chatActive();
            const filtered = r.messages.filter(m => m.role === 'user' || m.role === 'assistant');
            // Only swap in if the server count changed OR we have nothing yet.
            // Avoids visual flicker on identical polls.
            if (filtered.length !== messages.length || force) {
                messages = filtered.map(m => ({
                    role: m.role as 'user' | 'assistant',
                    content: m.content,
                    ts: new Date().toISOString(),
                }));
                scrollSoon();
            }
        } catch {}
    }

    onMount(() => {
        textareaRef?.focus();
        refreshAttachments();
        refreshHistory();
        loadActiveConversation();   // ← restore prior turns
        // Poll active conversation every 5s so a task running on the server
        // (e.g. user closed the tab mid-stream) shows up when they return.
        const tActive = setInterval(loadActiveConversation, 5000);
        // Refresh history every 30s so newly-summarized convs appear
        const t = setInterval(refreshHistory, 30000);
        return () => { clearInterval(t); clearInterval(tActive); };
    });

    function fmtTs2(s: string): string {
        try {
            const d = new Date(s);
            const today = new Date();
            const sameDay = d.toDateString() === today.toDateString();
            return sameDay
                ? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
                : d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short' });
        } catch { return s; }
    }
</script>

<div class="relative h-full px-6 py-5">
    <!-- Reactive neural backdrop — wakes up when Kee is producing,
         stays as gentle ambient when idle. -->
    <div
        class="pointer-events-none absolute inset-0 transition-opacity duration-700"
        style="opacity: {busy ? 0.65 : 0.20};"
    >
        <NeuralCanvas pulseTrigger={pulse} agentState={busy ? 'thinking' : 'idle'} density={0.7} />
    </div>

    <!-- History sidebar toggle -->
    <button
        onclick={() => (sidebarOpen = !sidebarOpen)}
        class="absolute top-5 left-6 z-20 glass rounded-lg px-3 py-2 mono text-[0.6rem] uppercase tracking-[0.2em] text-zinc-400 hover:text-cyan-200 transition-colors flex items-center gap-2"
        title="Historial de conversaciones"
    >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
        <span>past · {history.length}</span>
    </button>

    <!-- History sidebar (slide-in) -->
    {#if sidebarOpen}
        <div class="absolute top-0 left-0 bottom-0 w-80 z-30 glass overflow-y-auto" style="background: linear-gradient(180deg, rgb(8 9 11 / 0.95), rgb(8 9 11 / 0.85)); backdrop-filter: blur(28px);">
            <header class="sticky top-0 hairline-b px-5 py-4 flex items-baseline justify-between"
                style="background: linear-gradient(180deg, rgb(8 9 11 / 0.98), rgb(8 9 11 / 0.85));">
                <div>
                    <span class="eyebrow">memory</span>
                    <h2 class="text-sm font-light text-zinc-100 tracking-tight mt-0.5">Conversaciones</h2>
                </div>
                <button onclick={() => (sidebarOpen = false)} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
            </header>
            <div class="p-3 space-y-1.5">
                {#if history.length === 0}
                    <p class="text-[0.78rem] text-zinc-500 px-3 py-4 text-center">Aún no hay resúmenes. El auto-summarizer corre cada 10 min sobre conversaciones inactivas.</p>
                {/if}
                {#each history as h (h.id)}
                    <button onclick={() => openConvDetail(h.id)}
                        class="w-full text-left rounded-xl px-3 py-2.5 hairline lift cursor-pointer hover:border-cyan-400/30 transition-colors">
                        <div class="flex items-baseline justify-between mb-1">
                            <span class="chip text-[0.55rem]
                                {h.source === 'telegram' ? 'bg-cyan-400/10 text-cyan-200'
                                    : h.source === 'voice' ? 'bg-amber-300/10 text-amber-200'
                                    : h.source === 'api' ? 'bg-violet-400/10 text-violet-200'
                                    : 'bg-zinc-700/30 text-zinc-300'}">{h.source}</span>
                            <span class="mono text-[0.6rem] text-zinc-600 tabular">{fmtTs2(h.last_active)}</span>
                        </div>
                        <p class="text-[0.78rem] text-zinc-300 leading-snug line-clamp-3">{h.summary}</p>
                    </button>
                {/each}
            </div>
        </div>
    {/if}

    <div class="relative mx-auto flex h-full max-w-[1600px] flex-col gap-4">
        <!-- Conversation surface -->
        <div bind:this={scrollRef} class="flex-1 overflow-y-auto pr-2">
            {#if messages.length === 0}
                <div class="flex h-full flex-col items-center justify-center text-center">
                    <div class="text-3xl font-light text-zinc-200 tracking-tight mb-2">Buenas, Coco.</div>
                    <p class="text-sm text-zinc-500 max-w-md">
                        Escribe lo que necesites. <span class="mono text-[0.65rem] text-zinc-600">Enter</span> manda,
                        <span class="mono text-[0.65rem] text-zinc-600">Shift+Enter</span> línea nueva.
                    </p>
                </div>
            {/if}

            {#each messages as msg, i (msg.ts + msg.role + i)}
                <article class="mb-5 flex gap-3 {msg.role === 'user' ? 'flex-row-reverse' : ''}">
                    <!-- Author chip -->
                    <div class="flex flex-shrink-0 flex-col items-center gap-1.5 pt-1">
                        {#if msg.role === 'user'}
                            <div class="h-7 w-7 rounded-full bg-gradient-to-br from-amber-200/30 to-amber-400/10 hairline flex items-center justify-center">
                                <span class="mono text-[0.65rem] text-amber-200 tracking-widest">C</span>
                            </div>
                        {:else if msg.role === 'error'}
                            <div class="h-7 w-7 rounded-full bg-fuchsia-500/20 hairline flex items-center justify-center">
                                <span class="text-[0.7rem] text-fuchsia-300">!</span>
                            </div>
                        {:else}
                            <div class="h-7 w-7 rounded-full bg-cyan-400/15 hairline flex items-center justify-center">
                                <span class="mono text-[0.6rem] text-cyan-300 tracking-widest">K</span>
                            </div>
                        {/if}
                        <span class="mono text-[0.55rem] text-zinc-600 tabular">{timeOf(msg.ts)}</span>
                    </div>

                    <!-- Bubble -->
                    <div
                        class="max-w-[92%] rounded-2xl px-4 py-3 text-[0.92rem] leading-relaxed
                        {msg.role === 'user'
                            ? 'glass border border-amber-200/10'
                            : msg.role === 'error'
                                ? 'glass border border-fuchsia-300/30 text-fuchsia-200'
                                : 'glass'}"
                    >
                        <div class="whitespace-pre-wrap">{msg.content}</div>
                    </div>
                </article>
            {/each}

            {#if busy}
                <article class="mb-5 flex gap-3">
                    <div class="h-7 w-7 rounded-full bg-cyan-400/15 hairline flex items-center justify-center">
                        <span class="mono text-[0.6rem] text-cyan-300 tracking-widest breathe">K</span>
                    </div>
                    <div class="glass max-w-[92%] rounded-2xl px-4 py-3">
                        <div class="flex items-center gap-2">
                            <span class="h-1.5 w-1.5 rounded-full bg-cyan-400/80 breathe" style="animation-delay:0s"></span>
                            <span class="h-1.5 w-1.5 rounded-full bg-cyan-400/60 breathe" style="animation-delay:0.2s"></span>
                            <span class="h-1.5 w-1.5 rounded-full bg-cyan-400/40 breathe" style="animation-delay:0.4s"></span>
                            <span class="ml-1 mono text-[0.65rem] text-zinc-500 uppercase tracking-[0.2em]">pensando</span>
                        </div>
                    </div>
                </article>
            {/if}
        </div>

        <!-- Attachment chips -->
        {#if attachments.length > 0 || attachStatus}
            <div class="flex items-center gap-2 flex-wrap px-2">
                {#each attachments as a (a.path)}
                    <span class="chip bg-amber-300/10 text-amber-200 text-[0.65rem] flex items-center gap-1.5">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 3v5h5M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-5z"/></svg>
                        <span class="mono">{a.name}</span>
                        <span class="text-amber-200/50">{fmtBytes(a.bytes)}</span>
                        <button onclick={() => removeAttachment(a.name)} class="text-amber-200/50 hover:text-fuchsia-200" title="Quitar">×</button>
                    </span>
                {/each}
                {#if attachStatus}
                    <span class="text-[0.65rem] text-zinc-400 italic">{attachStatus}</span>
                {/if}
            </div>
        {/if}

        <!-- Floating composer -->
        <div class="glass rounded-2xl p-3 flex items-end gap-3">
            <input
                bind:this={fileInputRef}
                type="file"
                onchange={onPickFile}
                class="hidden"
            />
            <button
                onclick={() => { input = '/help'; send(); }}
                class="self-center h-8 px-2.5 mono text-[0.6rem] uppercase tracking-[0.2em] rounded-lg hairline hover:border-amber-300/30 hover:text-amber-200 text-zinc-500 transition-colors"
                title="Mostrar comandos disponibles"
            >
                /help
            </button>
            <button
                onclick={() => fileInputRef?.click()}
                class="self-center h-8 w-8 rounded-lg hairline hover:border-cyan-400/40 hover:text-cyan-200 text-zinc-500 transition-colors flex items-center justify-center"
                title="Adjuntar archivo"
            >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                </svg>
            </button>
            <textarea
                bind:this={textareaRef}
                bind:value={input}
                oninput={autosize}
                onkeydown={onKey}
                placeholder="hablale a kee…"
                rows="1"
                class="flex-1 resize-none bg-transparent px-2 py-1.5 text-[0.92rem] leading-relaxed text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
                style="max-height: 240px;"
            ></textarea>
            <button
                onclick={reset}
                class="self-center mono text-[0.6rem] uppercase tracking-[0.2em] text-zinc-500 hover:text-zinc-300 transition-colors"
                title="Limpiar conversación"
            >
                reset
            </button>
            <button
                onclick={send}
                disabled={busy || !input.trim()}
                class="group relative flex items-center gap-2 overflow-hidden rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-[0.78rem] text-cyan-200 transition-all
                       hover:bg-cyan-400/20 hover:border-cyan-400/50
                       disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-cyan-400/10"
            >
                <span class="relative z-10">Enviar</span>
                <span class="mono text-[0.6rem] text-cyan-300/60 relative z-10">↵</span>
            </button>
        </div>
    </div>

    <!-- Conversation detail modal -->
    {#if detailConv}
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm" onclick={() => (detailConv = null)} role="presentation">
            <div class="glass rounded-2xl max-w-3xl w-full mx-6 max-h-[85vh] flex flex-col" onclick={(e) => e.stopPropagation()} role="presentation">
                <header class="flex items-baseline justify-between px-6 pt-5 pb-3 hairline-b">
                    <div>
                        <span class="eyebrow">conversation</span>
                        <h3 class="mono text-sm text-cyan-200 mt-1 tabular">{detailConv.id.slice(0, 8)}…</h3>
                    </div>
                    <button onclick={() => (detailConv = null)} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
                </header>
                <div class="flex-1 overflow-auto p-5 space-y-3">
                    {#if detailLoading}
                        <div class="h-32 skeleton"></div>
                    {/if}
                    {#each detailConv.messages as m, i (i)}
                        {#if m.role === 'system'}
                            <details class="text-[0.7rem] text-zinc-600">
                                <summary class="mono uppercase tracking-wider cursor-pointer hover:text-zinc-400">system block ({m.content.length} chars)</summary>
                                <pre class="mt-2 mono text-[0.65rem] text-zinc-500 whitespace-pre-wrap">{m.content.slice(0, 1000)}{m.content.length > 1000 ? '…' : ''}</pre>
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
                        {/if}
                    {/each}
                </div>
            </div>
        </div>
    {/if}
</div>
