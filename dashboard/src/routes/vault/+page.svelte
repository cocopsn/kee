<script lang="ts">
    import { onMount } from 'svelte';
    import { api, type VaultItem } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';
    import { toast } from '$lib/components/Toast.svelte';

    let items = $state<VaultItem[]>([]);
    let loading = $state(true);
    let activePath = $state<string | null>(null);
    let activeContent = $state<{ content: string; lines: number; bytes: number } | null>(null);
    let pathFilter = $state('');
    let editMode = $state(false);
    let editBuffer = $state('');
    let saving = $state(false);
    let saveStatus = $state('');

    async function refresh() {
        try { items = (await api.vaultList()).items; } catch {}
        loading = false;
    }
    async function open(path: string) {
        activePath = path;
        activeContent = null;
        editMode = false;
        try {
            activeContent = await api.vaultRead(path);
        } catch (e) {
            activeContent = { content: `Error: ${(e as Error).message}`, lines: 0, bytes: 0 };
        }
    }
    function startEdit() {
        if (!activeContent) return;
        editBuffer = activeContent.content;
        editMode = true;
    }
    async function saveEdit() {
        if (!activePath) return;
        saving = true;
        try {
            await api.vaultWrite(activePath, editBuffer);
            toast(`Guardado: ${activePath}`, 'success');
            // Refresh content + list (mtime changed)
            activeContent = await api.vaultRead(activePath);
            await refresh();
            editMode = false;
        } catch (e) {
            toast(`Error: ${(e as Error).message}`, 'error', 5000);
        }
        saving = false;
    }

    onMount(refresh);

    let filtered = $derived(pathFilter
        ? items.filter(i => i.path.toLowerCase().includes(pathFilter.toLowerCase()))
        : items);

    let totalBytes = $derived(items.reduce((s, i) => s + i.bytes, 0));

    function fmtTs(t: number): string {
        try {
            const d = new Date(t * 1000);
            const today = new Date();
            return d.toDateString() === today.toDateString()
                ? d.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
                : d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short' });
        } catch { return ''; }
    }
    function fmtBytes(n: number): string {
        if (n < 1024) return `${n}B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
        return `${(n / (1024 * 1024)).toFixed(2)}MB`;
    }

    // Top-level folders
    let folders = $derived.by(() => {
        const m = new Map<string, number>();
        for (const it of items) {
            const top = it.path.split('/')[0] || '_root';
            m.set(top, (m.get(top) || 0) + 1);
        }
        return [...m.entries()].sort((a, b) => b[1] - a[1]);
    });
</script>

<div class="h-full flex flex-col">
    <div class="px-6 py-4 hairline-b">
        <div class="flex items-end gap-8">
            <div>
                <span class="eyebrow">memory</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Vault</h1>
                <p class="text-sm text-zinc-500 mt-1">Read-only viewer over Coco's Obsidian vault.</p>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="files" value={items.length} />
                <Stat label="total" value={Number((totalBytes / 1024).toFixed(1))} unit="kb" accent="cyan" />
                {#if activeContent}
                    <Stat label="open lines" value={activeContent.lines} accent="gold"/>
                {/if}
            </div>
        </div>
    </div>

    <div class="flex-1 grid grid-cols-12 min-h-0">
        <!-- File list -->
        <div class="col-span-4 hairline border-r overflow-y-auto p-4 space-y-2" style="border-right: 1px solid var(--color-hairline);">
            <input bind:value={pathFilter} placeholder="filter path…"
                class="w-full mono text-[0.7rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/30 focus:outline-none placeholder:text-zinc-700 mb-3"/>
            <div class="flex gap-1 mb-3 flex-wrap">
                {#each folders as [name, n] (name)}
                    <button onclick={() => (pathFilter = name + '/')}
                        class="mono text-[0.6rem] uppercase tracking-wider px-2 py-1 rounded bg-white/[0.02] hairline hover:border-cyan-400/30 text-zinc-400 hover:text-cyan-200">
                        {name} <span class="text-zinc-600 tabular">{n}</span>
                    </button>
                {/each}
            </div>
            {#if loading}
                {#each Array(6) as _, i (i)}
                    <div class="h-4 w-3/4 skeleton mb-1"></div>
                {/each}
            {/if}
            {#each filtered as it (it.path)}
                <button onclick={() => open(it.path)}
                    class="w-full text-left px-3 py-1.5 rounded-lg hairline lift hover:border-cyan-400/30 transition-colors
                        {activePath === it.path ? 'bg-cyan-400/05 border-cyan-400/40' : ''}">
                    <div class="flex items-baseline justify-between mb-0.5">
                        <span class="mono text-[0.75rem] text-zinc-200 truncate">{it.path}</span>
                        <span class="mono text-[0.6rem] text-zinc-600 tabular ml-2 flex-shrink-0">{fmtBytes(it.bytes)}</span>
                    </div>
                    <span class="mono text-[0.6rem] text-zinc-600">{fmtTs(it.mtime)}</span>
                </button>
            {/each}
        </div>
        <!-- Content viewer / editor -->
        <div class="col-span-8 overflow-y-auto p-6 flex flex-col">
            {#if !activePath}
                <div class="flex flex-1 items-center justify-center text-zinc-500 text-sm">
                    Selecciona un archivo
                </div>
            {:else if !activeContent}
                <div class="h-32 skeleton"></div>
            {:else}
                <div class="mb-3 flex items-baseline justify-between">
                    <div class="mono text-[0.65rem] text-zinc-600 tabular">{activePath} · {activeContent.bytes}b · {activeContent.lines} lines</div>
                    <div class="flex items-center gap-3">
                        {#if saveStatus}
                            <span class="text-[0.65rem] text-amber-200">{saveStatus}</span>
                        {/if}
                        {#if !editMode}
                            <button onclick={startEdit}
                                class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-amber-200">edit</button>
                        {:else}
                            <button onclick={() => (editMode = false)}
                                class="mono text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-200">cancel</button>
                            <button onclick={saveEdit} disabled={saving}
                                class="mono text-[0.65rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-3 py-1 rounded disabled:opacity-30">
                                {saving ? '…' : 'save'}
                            </button>
                        {/if}
                    </div>
                </div>
                {#if editMode}
                    <textarea bind:value={editBuffer}
                        class="flex-1 w-full mono text-[0.78rem] text-zinc-200 leading-relaxed bg-white/[0.02] hairline focus:border-amber-300/40 focus:outline-none p-4 rounded-lg resize-none"
                        spellcheck="false"></textarea>
                {:else}
                    <pre class="text-[0.85rem] text-zinc-300 leading-relaxed whitespace-pre-wrap flex-1">{activeContent.content}</pre>
                {/if}
            {/if}
        </div>
    </div>
</div>
