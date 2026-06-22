<script lang="ts">
    import { page } from '$app/state';
    import { base } from '$app/paths';
    import { onMount } from 'svelte';
    import '../app.css';
    import Brand from '$lib/components/Brand.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';
    import Toast from '$lib/components/Toast.svelte';
    import { api, openStream } from '$lib/api';

    const tabs = [
        { href: `${base}/`,                label: 'Chat',     kbd: '1' },
        { href: `${base}/conversations`,   label: 'Past',     kbd: '2' },
        { href: `${base}/nervous-system`,  label: 'Nervous',  kbd: '3' },
        { href: `${base}/world`,           label: 'World',    kbd: '4' },
        { href: `${base}/cycle`,           label: 'Cycle',    kbd: '5' },
        { href: `${base}/voice`,           label: 'Voice',    kbd: '6' },
        { href: `${base}/vault`,           label: 'Vault',    kbd: '7' },
        { href: `${base}/tools`,           label: 'Tools',    kbd: '8' },
        { href: `${base}/goals`,           label: 'Goals',    kbd: '9' },
        { href: `${base}/notifications`,   label: 'Inbox',    kbd: '0' },
        // Click-only (no kbd shortcut) — added in the night-sweep:
        { href: `${base}/episodic`,        label: 'Memory',   kbd: '' },
        { href: `${base}/diary`,           label: 'Diary',    kbd: '' },
        { href: `${base}/worker`,          label: 'Worker',   kbd: '' },
    ];

    let { children } = $props();
    let healthy = $state<boolean | null>(null);
    let model = $state<string>('');
    let toolCount = $state<number>(0);
    let uptime = $state<number>(0);
    let now = $state<string>(new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' }));
    let liveSignal = $state(0);
    let costToday = $state<number | null>(null);
    let costNearCap = $state(false);
    let costKill = $state(false);
    let unreadCount = $state(0);
    let notifOpen = $state(false);
    let recentNotifs = $state<any[]>([]);

    function fmtUptime(s: number): string {
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
        return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function isActive(href: string): boolean {
        const path = page.url.pathname;
        const root = `${base}/`;
        if (href === root) return path === root || path === base || path === '/';
        return path.startsWith(href);
    }

    // Detect if we're running inside pywebview (desktop) vs a browser.
    // When inside the desktop, we show extra chrome buttons (back to
    // hologram, minimize, hide) so the user always has a way out.
    const pywebview = typeof window !== 'undefined' ? (window as any).pywebview : null;
    const isDesktop = pywebview !== null && pywebview !== undefined;

    function backToHologram() {
        if (pywebview?.api?.switch_mode) pywebview.api.switch_mode('hologram');
    }
    function minimizeWindow() {
        if (pywebview?.api?.minimize) pywebview.api.minimize();
    }
    function hideWindow() {
        if (pywebview?.api?.hide) pywebview.api.hide();
    }

    async function refreshHealth() {
        try {
            const h = await api.health();
            healthy = h.status === 'ok' || h.status === 'healthy' || true;
            model = h.model;
            toolCount = h.tools;
            uptime = h.uptime_s;
        } catch {
            healthy = false;
        }
    }
    async function refreshCost() {
        try {
            const c = await api.llmCost();
            costToday = c.today.today_usd;
            costNearCap = c.today.near_cap;
            costKill = c.today.kill_active;
        } catch { /* api may not have endpoint yet */ }
    }
    async function refreshNotifs() {
        try {
            const c = await api.notificationsUnreadCount();
            unreadCount = c.count;
            const r = await api.notifications({ limit: 8 });
            recentNotifs = r.rows;
        } catch {}
    }
    async function markAll() {
        await api.notificationsHandleAll();
        await refreshNotifs();
    }

    function onKey(e: KeyboardEvent) {
        // Ignore when typing in inputs/textareas
        const tgt = e.target as HTMLElement;
        if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable)) return;
        // Cmd/Ctrl+0..9 → switch tab (0 = last)
        if ((e.metaKey || e.ctrlKey) && /^[0-9]$/.test(e.key)) {
            // Map: '1'..'9' → 0..8, '0' → 9
            let idx = e.key === '0' ? 9 : parseInt(e.key, 10) - 1;
            if (idx >= 0 && idx < tabs.length) {
                e.preventDefault();
                window.location.href = tabs[idx].href;
                return;
            }
        }
    }

    onMount(() => {
        refreshHealth();
        refreshCost();
        refreshNotifs();
        const ti = setInterval(refreshHealth, 8000);
        const tc = setInterval(refreshCost, 6000);
        const tn_notif = setInterval(refreshNotifs, 5000);
        const tn = setInterval(() => {
            now = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
        }, 30000);
        const close = openStream((e) => {
            liveSignal = (liveSignal + 1) % 1000000;
            // Refresh notifs immediately on inbound notification events
            if ((e as any).type === 'notification_inbound') refreshNotifs();
        });
        window.addEventListener('keydown', onKey);
        return () => {
            clearInterval(ti); clearInterval(tc); clearInterval(tn); clearInterval(tn_notif);
            close();
            window.removeEventListener('keydown', onKey);
        };
    });
</script>

<!-- Desktop-only chrome strip: back to hologram + minimize + hide.
     Always visible, fixed in the top-right corner so user never gets
     stuck in a dashboard sub-page with no way back to the orb. -->
{#if isDesktop}
    <div class="desktop-chrome">
        <button onclick={backToHologram} title="Volver al holograma" class="dchrome-btn dchrome-back">
            ← orb
        </button>
        <button onclick={minimizeWindow} title="Minimizar" class="dchrome-btn">_</button>
        <button onclick={hideWindow} title="Ocultar" class="dchrome-btn dchrome-x">×</button>
    </div>
{/if}

<!-- Body bg moved here from app.html so the hologram route (which uses
     +page@.svelte and skips this layout entirely) renders fully transparent.
     bg-[#08090b] is the default dashboard background. -->
<div class="relative z-10 flex h-screen w-screen flex-col bg-[#08090b]">
    <header class="flex items-center gap-3 md:gap-6 lg:gap-8 px-4 md:px-7 py-3 md:py-3.5 hairline-b overflow-x-auto" style="background: linear-gradient(180deg, rgb(8 9 11 / 0.9), rgb(8 9 11 / 0.4)); backdrop-filter: blur(24px);">
        <a href="{base}/" class="flex items-center gap-3 group flex-shrink-0">
            <span class="text-cyan-400 transition-transform duration-500 group-hover:rotate-180">
                <Brand size={20} glow={healthy === true} />
            </span>
            <div class="hidden md:flex flex-col leading-none">
                <span class="font-medium text-zinc-100 tracking-[0.18em] text-[0.7rem]">KEE</span>
                <span class="text-[0.6rem] text-amber-200/40 tracking-[0.22em] uppercase">sovereign</span>
            </div>
        </a>

        <nav class="flex gap-0.5 overflow-x-auto flex-shrink min-w-0">
            {#each tabs as tab (tab.href)}
                <a
                    href={tab.href}
                    class="group relative flex items-center gap-1 md:gap-2 px-2.5 md:px-4 py-2 text-[0.7rem] md:text-[0.78rem] tracking-wide transition-colors duration-300 whitespace-nowrap
                        {isActive(tab.href)
                            ? 'text-cyan-300'
                            : 'text-zinc-500 hover:text-zinc-200'}"
                >
                    <span>{tab.label}</span>
                    <span class="hidden lg:inline mono text-[0.55rem] text-zinc-600 group-hover:text-zinc-400">⌘{tab.kbd}</span>
                    {#if isActive(tab.href)}
                        <span class="absolute inset-x-3 -bottom-px h-px bg-gradient-to-r from-transparent via-cyan-400 to-transparent"></span>
                    {/if}
                </a>
            {/each}
        </nav>

        <div class="ml-auto flex items-center gap-3 md:gap-6 flex-shrink-0">
            <div class="hidden md:flex items-center gap-2.5">
                {#if healthy === true}
                    <PulseDot tone="live" />
                    <span class="mono text-[0.7rem] text-zinc-400">{model}</span>
                {:else if healthy === false}
                    <PulseDot tone="alert" label="offline" />
                {:else}
                    <PulseDot tone="mute" label="…" />
                {/if}
            </div>
            <div class="hidden lg:flex items-center gap-4 mono text-[0.7rem] text-zinc-500">
                <span class="flex items-center gap-1.5">
                    <span class="text-zinc-700">tools</span>
                    <span class="text-zinc-300 tabular">{toolCount}</span>
                </span>
                <span class="flex items-center gap-1.5">
                    <span class="text-zinc-700">uptime</span>
                    <span class="text-zinc-300 tabular">{fmtUptime(uptime)}</span>
                </span>
                {#if costToday !== null}
                    <a href="{base}/cost" class="flex items-center gap-1.5 hover:text-zinc-200 transition-colors" title="Cost today — click for breakdown">
                        <span class="text-zinc-700">$</span>
                        <span
                            class="tabular {costKill ? 'text-fuchsia-200' : costNearCap ? 'text-amber-200' : 'text-zinc-300'}"
                        >{costToday.toFixed(4)}</span>
                    </a>
                {/if}
                <!-- Health pulse icon -->
                <a href="{base}/health" class="text-zinc-400 hover:text-amber-200 transition-colors" title="Health">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                    </svg>
                </a>
                <!-- Settings gear -->
                <a href="{base}/settings" class="text-zinc-400 hover:text-cyan-200 transition-colors" title="Settings">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="3"/>
                        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                    </svg>
                </a>
                <!-- Notification bell -->
                <div class="relative">
                    <button onclick={() => (notifOpen = !notifOpen)}
                        class="relative flex items-center justify-center text-zinc-400 hover:text-cyan-200 transition-colors"
                        title="Notificaciones">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
                            <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
                        </svg>
                        {#if unreadCount > 0}
                            <span class="absolute -top-1.5 -right-1.5 mono text-[0.55rem] tabular bg-fuchsia-400/90 text-zinc-950 rounded-full px-1 leading-tight"
                                style="min-width: 14px; text-align: center;">{unreadCount}</span>
                        {/if}
                    </button>
                    {#if notifOpen}
                        <div class="absolute right-0 top-full mt-2 w-96 z-50 glass rounded-xl overflow-hidden"
                            style="background: linear-gradient(180deg, rgb(8 9 11 / 0.96), rgb(8 9 11 / 0.88)); backdrop-filter: blur(28px);">
                            <header class="flex items-baseline justify-between px-4 py-3 hairline-b">
                                <span class="eyebrow">inbox</span>
                                <div class="flex items-baseline gap-3">
                                    <a href="{base}/notifications" onclick={() => (notifOpen = false)}
                                        class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">ver todas →</a>
                                    <button onclick={markAll}
                                        class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">mark all</button>
                                </div>
                            </header>
                            <div class="max-h-96 overflow-y-auto">
                                {#if recentNotifs.length === 0}
                                    <p class="px-4 py-6 text-[0.78rem] text-zinc-500 text-center">Sin notificaciones</p>
                                {/if}
                                {#each recentNotifs as n (n.id)}
                                    {@const tone = n.urgency >= 2 ? 'fuchsia' : n.urgency === 0 ? 'zinc' : 'cyan'}
                                    <div class="px-4 py-2.5 hairline-b last:border-b-0 hover:bg-white/[0.02]">
                                        <div class="flex items-baseline gap-2 mb-0.5">
                                            <span class="chip text-[0.55rem] bg-{tone}-400/10 text-{tone}-200">{n.source}</span>
                                            {#if n.direction === 'outbound'}
                                                <span class="chip text-[0.55rem] bg-zinc-700/40 text-zinc-400">out</span>
                                            {/if}
                                            {#if !n.handled && n.direction === 'inbound'}
                                                <span class="h-1.5 w-1.5 rounded-full bg-cyan-400/80"></span>
                                            {/if}
                                            <span class="ml-auto mono text-[0.6rem] text-zinc-600 tabular">
                                                {new Date(n.timestamp).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })}
                                            </span>
                                        </div>
                                        {#if n.title}
                                            <p class="text-[0.78rem] text-zinc-200 truncate">{n.title}</p>
                                        {/if}
                                        <p class="text-[0.7rem] text-zinc-500 line-clamp-2">{n.body}</p>
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/if}
                </div>
                <span class="text-zinc-300 tabular">{now}</span>
            </div>
        </div>
    </header>

    <Toast />
    <main class="flex-1 overflow-hidden">
        {@render children()}
    </main>
</div>

<style>
    /* Desktop-only chrome strip: always-visible exit/minimize/back-to-orb
       buttons in the top-right corner of any dashboard page. Higher z-index
       than the dashboard nav so it stays on top regardless of scroll. */
    .desktop-chrome {
        position: fixed;
        top: 6px;
        right: 6px;
        display: flex;
        gap: 4px;
        z-index: 9999;
        background: rgba(0, 0, 0, 0.6);
        padding: 4px;
        border-radius: 6px;
        backdrop-filter: blur(8px);
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .dchrome-btn {
        height: 24px;
        min-width: 28px;
        padding: 0 8px;
        background: transparent;
        border: none;
        color: rgba(244, 244, 245, 0.85);
        font-size: 11px;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
        line-height: 1;
        cursor: pointer;
        transition: all 0.12s;
        border-radius: 3px;
    }
    .dchrome-btn:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }
    .dchrome-back { color: #67e8f9; }
    .dchrome-back:hover { background: rgba(34, 211, 238, 0.2); }
    .dchrome-x:hover { background: rgba(232, 121, 249, 0.4); color: #fff; }
</style>
