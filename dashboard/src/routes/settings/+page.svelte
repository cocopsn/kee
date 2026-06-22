<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import {
        api,
        type LLMProvidersResponse, type LLMCostResponse, type LLMCallRow,
        type RouterConfigResponse, type KeeCodeStatus,
        type VoicePrefs, type InstalledVoice, type CatalogVoice,
    } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';

    let providers = $state<LLMProvidersResponse | null>(null);
    let cost = $state<LLMCostResponse | null>(null);
    let recent = $state<LLMCallRow[]>([]);
    let routerCfg = $state<RouterConfigResponse | null>(null);
    let keecode = $state<KeeCodeStatus | null>(null);
    let healthModel = $state('');
    let mainModelDraft = $state('');
    let keecodeDraftReady = $state(false);
    let keecodeDraft = $state({
        agent: 'keecode',
        model: '',
        command: 'opencode',
        repo: '',
    });
    let keecodePrompt = $state('Continua desde Kee chat y manten contexto con la conversacion normal.');
    let keecodeWorkdir = $state('D:\\Kee');
    let keecodeLaunching = $state(false);
    let keecodeSyncing = $state(false);
    let loading = $state(true);
    let saving = $state(false);
    let savedFlash = $state('');

    async function refresh() {
        try {
            const h = await api.health();
            healthModel = h.model;
            if (!mainModelDraft) mainModelDraft = h.model;
        } catch {}
        try { providers = await api.llmProviders(); } catch {}
        try { cost = await api.llmCost(); } catch {}
        try { recent = (await api.llmRecent(50)).rows; } catch {}
        try { routerCfg = await api.routerConfig(); } catch {}
        try { await refreshKeeCode(false); } catch {}
        loading = false;
    }

    function hydrateKeeCodeDraft(s: KeeCodeStatus, force = false) {
        if (keecodeDraftReady && !force) return;
        keecodeDraft = {
            agent: s.agent || 'keecode',
            model: s.model || healthModel,
            command: s.opencode_command || 'opencode',
            repo: s.opencode_repo || '',
        };
        if (!keecodeWorkdir) keecodeWorkdir = 'D:\\Kee';
        keecodeDraftReady = true;
    }

    async function refreshKeeCode(hydrate = false) {
        const s = await api.keecodeStatus();
        keecode = s;
        if (hydrate || !keecodeDraftReady) hydrateKeeCodeDraft(s, hydrate);
    }

    let timer: any;
    onMount(() => {
        refresh();
        refreshVoice();
        timer = setInterval(() => { refresh(); }, 5000);
    });
    onDestroy(() => clearInterval(timer));

    let providerTestState = $state<Record<string, { running: boolean; result?: string; ms?: number }>>({});

    // Voice config + catalog state
    let voicePrefs = $state<VoicePrefs | null>(null);
    let installed = $state<InstalledVoice[]>([]);
    let catalog = $state<CatalogVoice[]>([]);
    let voiceTestText = $state('Hola Coco. Soy Kee, tu identidad residente. Probando esta voz.');
    let voiceTestState = $state<{ running: boolean; ms?: number; error?: string }>({ running: false });
    let installing = $state<Record<string, boolean>>({});

    async function refreshVoice() {
        try { voicePrefs = await api.voiceConfig(); } catch {}
        try { installed = (await api.voiceVoices()).voices; } catch {}
        try { catalog = (await api.voiceCatalog()).voices; } catch {}
    }
    async function setVoice(name: string) {
        try {
            const r = await api.voiceConfigSet({ voice: name });
            voicePrefs = r.config;
            savedFlash = `Voz → ${name}. Aplicada al siguiente turno hablado ✓`;
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        setTimeout(() => (savedFlash = ''), 4000);
    }
    async function setSpeed(length_scale: number) {
        try {
            const r = await api.voiceConfigSet({ length_scale });
            voicePrefs = r.config;
        } catch {}
    }
    async function toggleSpeak() {
        if (!voicePrefs) return;
        const r = await api.voiceConfigSet({ speak_responses: !voicePrefs.speak_responses });
        voicePrefs = r.config;
    }
    async function toggleAutoLang() {
        if (!voicePrefs) return;
        const r = await api.voiceConfigSet({ auto_detect_language: !voicePrefs.auto_detect_language });
        voicePrefs = r.config;
    }
    async function setLangVoice(lang: string, voice: string) {
        if (!voicePrefs) return;
        const next = { ...voicePrefs.voice_per_lang };
        if (voice === '__none__') delete next[lang]; else next[lang] = voice;
        const r = await api.voiceConfigSet({ voice_per_lang: next });
        voicePrefs = r.config;
        savedFlash = `Voz [${lang}] → ${voice === '__none__' ? '(default)' : voice}`;
        setTimeout(() => (savedFlash = ''), 3000);
    }
    async function testVoice(stem?: string) {
        voiceTestState = { running: true };
        try {
            const r = await api.voiceSpeak(voiceTestText, stem);
            voiceTestState = { running: false, ms: r.elapsed_ms };
        } catch (e) {
            voiceTestState = { running: false, error: (e as Error).message };
        }
        setTimeout(() => (voiceTestState = { running: false }), 5000);
    }
    async function installVoice(stem: string) {
        installing[stem] = true;
        try {
            const r = await api.voiceInstall([stem]);
            const result = r.results[0];
            if (result.ok) {
                savedFlash = result.already_installed
                    ? `${stem} ya estaba instalada.`
                    : `${stem} instalada (${result.size_mb}MB) ✓`;
            } else {
                savedFlash = `Error instalando ${stem}: ${result.error}`;
            }
            await refreshVoice();
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        installing[stem] = false;
        setTimeout(() => (savedFlash = ''), 5000);
    }
    async function uninstallVoice(stem: string) {
        if (!confirm(`Quitar voz "${stem}"?`)) return;
        try {
            await api.voiceUninstall(stem);
            await refreshVoice();
            savedFlash = `${stem} eliminada.`;
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        setTimeout(() => (savedFlash = ''), 4000);
    }
    async function installAllSpanish() {
        const stems = catalog.filter(v => v.locale.startsWith('es_') && !v.installed).map(v => v.stem);
        if (stems.length === 0) { savedFlash = 'Todas las voces ES ya están instaladas.'; setTimeout(() => (savedFlash = ''), 3000); return; }
        for (const s of stems) installing[s] = true;
        try {
            await api.voiceInstall(stems);
            await refreshVoice();
            savedFlash = `Instaladas ${stems.length} voces ES ✓`;
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        for (const s of stems) installing[s] = false;
        setTimeout(() => (savedFlash = ''), 5000);
    }
    let summarizerRunning = $state(false);
    let routerEditMode = $state<'closed' | 'direct' | 'tier_hints'>('closed');
    let routerDirectDraft = $state<{ match: string; reply: string }[]>([]);
    let routerTierDraft = $state<Record<string, string[]>>({ simple: [], medium: [], heavy: [] });

    async function setPrimary(name: string) {
        saving = true;
        const r = await api.updateSettings({ primary: name });
        saving = false;
        savedFlash = r.chain_rebuilt
            ? `Primary → ${name}. Chain rebuilt en vivo ✓`
            : `Primary → ${name}. (Chain rebuild fallido — dale "Force rebuild")`;
        setTimeout(() => (savedFlash = ''), 4000);
        refresh();
    }
    async function saveMainModel() {
        const model = mainModelDraft.trim();
        if (!model) return;
        saving = true;
        try {
            const r = await api.updateSettings({ model });
            savedFlash = r.chain_rebuilt
                ? `Modelo principal -> ${model}. Chain rebuilt en vivo`
                : `Modelo principal -> ${model}. Reinicia o usa Force rebuild si no cambia`;
            await refresh();
        } catch (e) {
            savedFlash = `Model error: ${(e as Error).message}`;
        }
        saving = false;
        setTimeout(() => (savedFlash = ''), 5000);
    }
    async function saveKeeCodeConfig() {
        saving = true;
        try {
            const model = keecodeDraft.model.trim() || mainModelDraft.trim();
            const r = await api.updateSettings({
                code_agent: keecodeDraft.agent,
                code_agent_model: model,
                opencode_command: keecodeDraft.command.trim(),
                opencode_repo: keecodeDraft.repo.trim(),
            });
            savedFlash = `KeeCode config guardada: ${r.changed.join(', ') || 'sin cambios'}`;
            await refreshKeeCode(true);
        } catch (e) {
            savedFlash = `KeeCode config error: ${(e as Error).message}`;
        }
        saving = false;
        setTimeout(() => (savedFlash = ''), 5000);
    }
    async function syncKeeCodeContext() {
        keecodeSyncing = true;
        try {
            const r = await api.keecodeContext({ notes: keecodePrompt, session_id: 'dashboard' });
            keecode = r;
            savedFlash = `Contexto KeeCode sincronizado -> ${r.context_path}`;
        } catch (e) {
            savedFlash = `Context sync error: ${(e as Error).message}`;
        }
        keecodeSyncing = false;
        setTimeout(() => (savedFlash = ''), 5000);
    }
    async function launchKeeCode() {
        keecodeLaunching = true;
        try {
            const r = await api.keecodeLaunch({
                prompt: keecodePrompt,
                workdir: keecodeWorkdir,
                model: keecodeDraft.model.trim() || mainModelDraft.trim(),
            });
            savedFlash = r.ok
                ? `KeeCode abierto en terminal nueva (pid ${r.pid})`
                : `KeeCode no pudo abrir: ${r.error || 'error desconocido'}`;
            await refreshKeeCode(false);
        } catch (e) {
            savedFlash = `KeeCode launch error: ${(e as Error).message}`;
        }
        keecodeLaunching = false;
        setTimeout(() => (savedFlash = ''), 6000);
    }
    async function setCap(v: number) {
        saving = true;
        await api.updateSettings({ daily_cap_usd: v });
        saving = false;
        savedFlash = `Cap → $${v}/día.`;
        setTimeout(() => (savedFlash = ''), 4000);
        refresh();
    }
    async function forceRebuild() {
        saving = true;
        try {
            const r = await api.rebuildAgent();
            savedFlash = `Chain rebuilt: [${r.chain_providers.join(' → ')}] | primary=${r.primary}`;
        } catch (e) {
            savedFlash = `Rebuild error: ${(e as Error).message}`;
        }
        saving = false;
        setTimeout(() => (savedFlash = ''), 5000);
        refresh();
    }
    async function testProvider(name: string) {
        providerTestState[name] = { running: true };
        try {
            const r = await api.testProvider(name);
            providerTestState[name] = {
                running: false,
                result: r.healthy ? 'OK' : 'FAIL',
                ms: r.latency_ms,
            };
        } catch (e) {
            providerTestState[name] = { running: false, result: 'ERR' };
        }
        setTimeout(() => {
            const cur = providerTestState[name];
            if (cur && !cur.running) providerTestState[name] = { running: false };
        }, 5000);
    }
    async function summarizeNow() {
        summarizerRunning = true;
        try {
            // Pull recent and pick the most recent unsummarized one
            const summaries = (await api.recentSummaries(10)).rows;
            const latest = summaries[0];
            if (latest) {
                await api.summarizeOne(latest.id);
                savedFlash = `Resumen actualizado: ${latest.id.slice(0, 8)}…`;
            } else {
                savedFlash = 'Sin conversaciones para resumir';
            }
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        summarizerRunning = false;
        setTimeout(() => (savedFlash = ''), 4000);
    }

    function openRouterEdit(mode: 'direct' | 'tier_hints') {
        if (!routerCfg) return;
        if (mode === 'direct') {
            routerDirectDraft = routerCfg.direct_rules.map(r => ({ match: r.pattern, reply: r.reply }));
        } else {
            routerTierDraft = {
                simple: [...(routerCfg.tier_hints.simple || [])],
                medium: [...(routerCfg.tier_hints.medium || [])],
                heavy: [...(routerCfg.tier_hints.heavy || [])],
            };
        }
        routerEditMode = mode;
    }
    async function saveRouterConfig() {
        try {
            if (routerEditMode === 'direct') {
                await api.putRouterConfig({ direct_rules: routerDirectDraft.filter(r => r.match && r.reply) });
            } else if (routerEditMode === 'tier_hints') {
                const cleaned: Record<string, string[]> = {};
                for (const tier of ['simple', 'medium', 'heavy']) {
                    cleaned[tier] = (routerTierDraft[tier] || []).filter(p => p.trim());
                }
                await api.putRouterConfig({ tier_hints: cleaned });
            }
            savedFlash = 'router.md guardado ✓ (siguiente turno lo aplica)';
            routerEditMode = 'closed';
            refresh();
        } catch (e) {
            savedFlash = `Error: ${(e as Error).message}`;
        }
        setTimeout(() => (savedFlash = ''), 4000);
    }

    function tierTone(t: string): string {
        return ({direct:'text-cyan-200', simple:'text-zinc-300', medium:'text-violet-200', heavy:'text-fuchsia-200'} as any)[t] ?? 'text-zinc-400';
    }
    function fmtTs(s: string): string {
        try { return new Date(s).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
        catch { return s; }
    }
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">control plane</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Settings</h1>
                <p class="text-sm text-zinc-500 mt-1">Provider chain, cost limits, router rules.</p>
            </div>
            {#if cost}
                <div class="ml-auto flex gap-10">
                    <Stat label="hoy" value={Number((cost.today.today_usd ?? 0).toFixed(4))} unit="usd" accent={cost.today.near_cap ? 'gold' : 'cyan'} />
                    <Stat label="cap" value={Number(cost.today.cap_usd.toFixed(2))} unit="usd" />
                    <Stat label="% cap" value={Math.round(cost.today.pct_of_cap)} unit="%" accent={cost.today.kill_active ? 'gold' : 'plain'}/>
                </div>
            {/if}
        </header>

        {#if savedFlash}
            <div class="mb-4 glass rounded-xl px-4 py-2 text-[0.78rem] text-amber-200">{savedFlash}</div>
        {/if}

        <div class="grid gap-4 lg:grid-cols-3">
            <!-- Provider chain -->
            <div class="lg:col-span-2">
                <Glass eyebrow="chain" title="LLM providers" >
                    {#snippet action()}
                        <button
                            onclick={forceRebuild}
                            disabled={saving}
                            class="mono text-[0.6rem] uppercase tracking-[0.2em] text-zinc-500 hover:text-cyan-200 transition-colors disabled:opacity-30"
                            title="Force-rebuild chain from current .env"
                        >force rebuild</button>
                    {/snippet}
                    {#if !providers || loading}
                        <div class="h-24 skeleton"></div>
                    {:else}
                        <ul class="space-y-3">
                            {#each providers.providers as p (p.name)}
                                {@const tst = providerTestState[p.name]}
                                <li class="flex items-center gap-4 px-3 py-3 rounded-xl hairline lift">
                                    <PulseDot tone={p.healthy ? 'live' : 'alert'} size={7}/>
                                    <div class="flex-1 min-w-0">
                                        <div class="flex items-baseline gap-3">
                                            <span class="mono text-[0.95rem] text-zinc-100 tracking-tight">{p.name}</span>
                                            {#if p.is_primary}
                                                <span class="chip bg-cyan-400/15 text-cyan-200 text-[0.6rem]">primary</span>
                                            {/if}
                                            {#if tst?.result}
                                                <span class="chip text-[0.6rem]
                                                    {tst.result === 'OK'
                                                        ? 'bg-cyan-400/15 text-cyan-200'
                                                        : 'bg-fuchsia-300/15 text-fuchsia-200'}">
                                                    {tst.result}{tst.ms != null ? ` ${tst.ms}ms` : ''}
                                                </span>
                                            {/if}
                                        </div>
                                        <div class="mono text-[0.7rem] text-zinc-500 tabular truncate">{p.model}</div>
                                    </div>
                                    <div class="text-right mono text-[0.7rem] text-zinc-500 tabular">
                                        <div class="text-zinc-400">${p.cost_in_per_mtok.toFixed(2)} / ${p.cost_out_per_mtok.toFixed(2)}</div>
                                        <div class="text-[0.6rem] text-zinc-600">in / out per Mtok</div>
                                    </div>
                                    <button
                                        onclick={() => testProvider(p.name)}
                                        disabled={tst?.running}
                                        class="mono text-[0.6rem] uppercase tracking-[0.18em] px-2.5 py-1.5 rounded-lg text-zinc-500 hover:text-amber-200 hairline hover:border-amber-300/30 transition-colors disabled:opacity-40"
                                        title="Probe with 1 token"
                                    >
                                        {tst?.running ? '…' : 'test'}
                                    </button>
                                    <button
                                        onclick={() => setPrimary(p.name)}
                                        disabled={p.is_primary || saving}
                                        class="mono text-[0.65rem] uppercase tracking-[0.18em] px-3 py-1.5 rounded-lg
                                            {p.is_primary
                                                ? 'text-zinc-700 cursor-not-allowed'
                                                : 'text-zinc-400 hover:text-cyan-200 hairline hover:border-cyan-400/30'}"
                                    >
                                        {p.is_primary ? '·' : 'set primary'}
                                    </button>
                                </li>
                            {/each}
                        </ul>
                    {/if}
                </Glass>
            </div>

            <!-- Kill switch + cap config -->
            <div class="lg:col-span-1 space-y-4">
                <Glass eyebrow="kill switch" title="Daily cap">
                    {#if cost}
                        <div class="space-y-3">
                            <div>
                                <div class="flex justify-between text-[0.78rem] mb-2">
                                    <span class="text-zinc-400">Gastado hoy</span>
                                    <span class="mono tabular {cost.today.kill_active ? 'text-fuchsia-200' : cost.today.near_cap ? 'text-amber-200' : 'text-zinc-200'}">
                                        ${cost.today.today_usd.toFixed(4)}
                                    </span>
                                </div>
                                <div class="relative h-2 rounded-full bg-white/[0.04] overflow-hidden">
                                    <div
                                        class="absolute left-0 top-0 h-full rounded-full transition-all duration-700
                                            {cost.today.kill_active ? 'bg-fuchsia-400' : cost.today.near_cap ? 'bg-amber-300' : 'bg-cyan-400'}"
                                        style="width: {Math.min(100, cost.today.pct_of_cap)}%;"
                                    ></div>
                                </div>
                            </div>
                            {#if cost.today.kill_active}
                                <div class="text-[0.78rem] text-fuchsia-200">⚠ Kill switch ACTIVO. Todas las queries pagas → Ollama hasta medianoche.</div>
                            {:else if cost.today.near_cap}
                                <div class="text-[0.78rem] text-amber-200">≥80% del cap. Cuidado.</div>
                            {/if}
                            <div class="flex gap-2">
                                {#each [1, 2, 5, 10, 20] as v (v)}
                                    <button
                                        onclick={() => setCap(v)}
                                        class="flex-1 mono text-[0.7rem] py-2 rounded-lg hairline hover:border-cyan-400/30 hover:text-cyan-200 transition-colors
                                            {cost.today.cap_usd === v ? 'bg-cyan-400/10 text-cyan-200 border-cyan-400/40' : 'text-zinc-400'}"
                                    >${v}</button>
                                {/each}
                            </div>
                        </div>
                    {/if}
                </Glass>

                <Glass eyebrow="today" title="Por provider">
                    {#if cost && Object.keys(cost.by_provider).length > 0}
                        <ul class="space-y-2 text-[0.78rem]">
                            {#each Object.entries(cost.by_provider) as [name, info] (name)}
                                <li class="flex justify-between">
                                    <span class="mono text-zinc-300">{name}</span>
                                    <span class="mono tabular text-zinc-500">
                                        <span class="text-amber-200/80">${info.cost_usd.toFixed(4)}</span>
                                        <span class="text-zinc-700">·</span> {info.calls}
                                    </span>
                                </li>
                            {/each}
                        </ul>
                    {:else}
                        <p class="text-[0.78rem] text-zinc-500">Sin actividad pagada hoy.</p>
                    {/if}
                </Glass>
            </div>

            <!-- KeeCode / OpenCode bridge -->
            <div class="lg:col-span-3">
                <Glass eyebrow="keecode" title="KeeCode / OpenCode">
                    {#snippet action()}
                        <div class="flex items-center gap-3">
                            <button onclick={() => refreshKeeCode(true)}
                                class="mono text-[0.6rem] uppercase tracking-[0.18em] text-zinc-500 hover:text-cyan-200">
                                refresh
                            </button>
                            <button onclick={syncKeeCodeContext} disabled={keecodeSyncing}
                                class="mono text-[0.6rem] uppercase tracking-[0.18em] text-zinc-500 hover:text-amber-200 disabled:opacity-30">
                                {keecodeSyncing ? '...' : 'sync context'}
                            </button>
                            <button onclick={launchKeeCode} disabled={keecodeLaunching || !keecode?.ok}
                                class="mono text-[0.6rem] uppercase tracking-[0.18em] text-cyan-200 bg-cyan-400/10 hairline border-cyan-400/30 hover:bg-cyan-400/20 px-3 py-1.5 rounded-lg disabled:opacity-30">
                                {keecodeLaunching ? 'opening...' : 'open terminal'}
                            </button>
                        </div>
                    {/snippet}

                    <div class="grid gap-5 lg:grid-cols-3">
                        <div class="space-y-3">
                            <div class="flex items-center gap-3">
                                <PulseDot tone={keecode?.ok ? 'live' : 'alert'} size={7}/>
                                <div>
                                    <div class="mono text-[0.85rem] text-zinc-100">
                                        {keecode?.ok ? 'ready' : 'needs setup'}
                                    </div>
                                    <div class="mono text-[0.62rem] text-zinc-600">
                                        source: {keecode?.opencode_command_source || 'unknown'}
                                    </div>
                                </div>
                            </div>
                            <div class="space-y-1.5 text-[0.7rem]">
                                <div class="flex justify-between gap-3">
                                    <span class="text-zinc-500">command</span>
                                    <span class="mono text-zinc-300 truncate">{keecode?.opencode_command_resolved || keecodeDraft.command}</span>
                                </div>
                                <div class="flex justify-between gap-3">
                                    <span class="text-zinc-500">repo clone</span>
                                    <span class="mono {keecode?.opencode_repo_exists ? 'text-cyan-200' : 'text-fuchsia-200'} truncate">
                                        {keecode?.opencode_repo_exists ? 'found' : 'missing'}
                                    </span>
                                </div>
                                <div class="flex justify-between gap-3">
                                    <span class="text-zinc-500">config</span>
                                    <span class="mono {keecode?.config_exists ? 'text-cyan-200' : 'text-zinc-500'} truncate">
                                        {keecode?.config_exists ? 'written' : 'pending'}
                                    </span>
                                </div>
                                <div class="flex justify-between gap-3">
                                    <span class="text-zinc-500">context</span>
                                    <span class="mono {keecode?.context_exists ? 'text-cyan-200' : 'text-zinc-500'} truncate">
                                        {keecode?.context_exists ? 'synced' : 'pending'}
                                    </span>
                                </div>
                            </div>
                            {#if keecode?.hint}
                                <p class="text-[0.7rem] text-amber-200">{keecode.hint}</p>
                            {/if}
                        </div>

                        <div class="space-y-3">
                            <div>
                                <div class="eyebrow mb-2">main chat model</div>
                                <div class="flex gap-2">
                                    <input bind:value={mainModelDraft}
                                        class="flex-1 mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300"/>
                                    <button onclick={saveMainModel} disabled={saving}
                                        class="mono text-[0.62rem] uppercase tracking-wider text-cyan-200 hairline border-cyan-400/30 rounded-lg px-3 disabled:opacity-30">
                                        apply
                                    </button>
                                </div>
                                <div class="mono text-[0.58rem] text-zinc-600 mt-1 truncate">live: {healthModel || 'unknown'}</div>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">code agent</div>
                                <div class="grid grid-cols-3 gap-2">
                                    {#each ['keecode', 'opencode', 'claude_code'] as agent (agent)}
                                        <button onclick={() => (keecodeDraft.agent = agent)}
                                            class="mono text-[0.62rem] uppercase tracking-wider py-2 rounded-lg hairline transition-colors
                                                {keecodeDraft.agent === agent
                                                    ? 'bg-cyan-400/10 text-cyan-200 border-cyan-400/40'
                                                    : 'text-zinc-500 hover:text-zinc-200'}">
                                            {agent === 'claude_code' ? 'claude' : agent}
                                        </button>
                                    {/each}
                                </div>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">keecode model</div>
                                <input bind:value={keecodeDraft.model}
                                    placeholder={mainModelDraft}
                                    class="w-full mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300"/>
                                <div class="mono text-[0.58rem] text-zinc-600 mt-1 truncate">{keecode?.model_id || 'ollama/model'}</div>
                            </div>
                        </div>

                        <div class="space-y-3">
                            <div>
                                <div class="eyebrow mb-2">opencode command</div>
                                <input bind:value={keecodeDraft.command}
                                    class="w-full mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300"/>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">desktop clone</div>
                                <input bind:value={keecodeDraft.repo}
                                    class="w-full mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300"/>
                            </div>
                            <div>
                                <div class="eyebrow mb-2">terminal workdir</div>
                                <input bind:value={keecodeWorkdir}
                                    class="w-full mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300"/>
                            </div>
                            <textarea bind:value={keecodePrompt} rows="3"
                                class="w-full mono text-[0.68rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-amber-300/40 focus:outline-none resize-none text-zinc-300"></textarea>
                            <button onclick={saveKeeCodeConfig} disabled={saving}
                                class="w-full mono text-[0.65rem] uppercase tracking-[0.18em] text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-4 py-2 rounded-lg disabled:opacity-30">
                                save keecode config
                            </button>
                        </div>
                    </div>
                </Glass>
            </div>

            <!-- Recent LLM calls -->
            <div class="lg:col-span-2">
                <Glass eyebrow="trace" title="Recent LLM calls" padded={false}>
                    {#if recent.length === 0}
                        <p class="px-5 py-6 text-[0.78rem] text-zinc-500">Sin llamadas registradas todavía.</p>
                    {/if}
                    {#each recent as r (r.id)}
                        <div class="px-5 py-1.5 hairline-b last:border-b-0 hover:bg-white/[0.02] transition-colors">
                            <div class="flex items-center gap-3 text-[0.72rem]">
                                <span class="mono text-[0.6rem] text-zinc-600 tabular w-14 flex-shrink-0">{fmtTs(r.timestamp)}</span>
                                <span class="chip bg-cyan-400/10 text-cyan-200 text-[0.6rem]">{r.provider}</span>
                                <span class="mono text-zinc-400 truncate flex-1 text-[0.65rem]">{r.model}</span>
                                <span class="mono text-[0.65rem] {tierTone(r.tier)} w-12 flex-shrink-0">{r.tier}</span>
                                <span class="mono text-[0.65rem] text-zinc-500 tabular w-14 text-right flex-shrink-0">{r.latency_ms ?? '?'}ms</span>
                                <span class="mono text-[0.65rem] text-amber-200/80 tabular w-16 text-right flex-shrink-0">${(r.cost_usd ?? 0).toFixed(4)}</span>
                            </div>
                        </div>
                    {/each}
                </Glass>
            </div>

            <!-- Router config (editable) -->
            <div class="lg:col-span-1">
                <Glass eyebrow="router" title="router.md rules">
                    {#if routerCfg}
                        <div class="flex justify-between items-baseline mb-3">
                            <p class="text-[0.7rem] text-zinc-500">Cambios live al siguiente turno.</p>
                            <button onclick={summarizeNow} disabled={summarizerRunning}
                                class="mono text-[0.6rem] uppercase tracking-[0.2em] text-zinc-500 hover:text-amber-200 transition-colors disabled:opacity-30"
                            >{summarizerRunning ? '…' : 'summarize last conv'}</button>
                        </div>
                        <div class="mb-3">
                            <div class="flex justify-between items-baseline mb-1">
                                <div class="eyebrow">direct ({routerCfg.direct_rules.length})</div>
                                <button onclick={() => openRouterEdit('direct')}
                                    class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">edit</button>
                            </div>
                            <ul class="space-y-1 text-[0.7rem]">
                                {#each routerCfg.direct_rules.slice(0, 6) as r (r.pattern)}
                                    <li class="mono text-zinc-400 truncate">→ {r.reply}</li>
                                {/each}
                            </ul>
                        </div>
                        <div>
                            <div class="flex justify-between items-baseline mb-1">
                                <div class="eyebrow">tier hints</div>
                                <button onclick={() => openRouterEdit('tier_hints')}
                                    class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">edit</button>
                            </div>
                            <ul class="space-y-0.5 text-[0.7rem] mono">
                                {#each Object.entries(routerCfg.tier_hints) as [tier, pats] (tier)}
                                    <li class="flex gap-2">
                                        <span class="{tierTone(tier)} w-14 flex-shrink-0">{tier}</span>
                                        <span class="text-zinc-500 tabular text-[0.65rem]">{pats.length} patrones</span>
                                    </li>
                                {/each}
                            </ul>
                        </div>
                    {/if}
                </Glass>
            </div>

            <!-- Voice configuration -->
            <div class="lg:col-span-3">
                <Glass eyebrow="speech" title="Voice — Piper TTS">
                    {#snippet action()}
                        <div class="flex items-center gap-3">
                            <button onclick={installAllSpanish}
                                class="mono text-[0.6rem] uppercase tracking-[0.18em] text-zinc-500 hover:text-cyan-200">
                                install all ES
                            </button>
                            {#if voicePrefs}
                                <button onclick={toggleSpeak}
                                    class="mono text-[0.6rem] uppercase tracking-[0.18em] {voicePrefs.speak_responses ? 'text-cyan-200' : 'text-zinc-600'}">
                                    speak: {voicePrefs.speak_responses ? 'on' : 'off'}
                                </button>
                            {/if}
                        </div>
                    {/snippet}

                    {#if !voicePrefs}
                        <div class="h-24 skeleton"></div>
                    {:else}
                        <div class="grid gap-5 md:grid-cols-3">
                            <!-- Active voice + speed -->
                            <div class="md:col-span-1 space-y-4">
                                <div>
                                    <div class="eyebrow mb-2">active</div>
                                    <div class="mono text-[0.95rem] text-cyan-200 truncate">{voicePrefs.voice}</div>
                                    <div class="mono text-[0.6rem] text-zinc-600 mt-1">
                                        {installed.length} instaladas · {catalog.length} en catálogo
                                    </div>
                                </div>
                                <div>
                                    <div class="flex justify-between text-[0.7rem] mb-2">
                                        <span class="text-zinc-400">Velocidad</span>
                                        <span class="mono tabular text-zinc-300">{voicePrefs.length_scale.toFixed(2)}×</span>
                                    </div>
                                    <input type="range" min="0.6" max="1.6" step="0.05"
                                        value={voicePrefs.length_scale}
                                        onchange={(e) => setSpeed(Number((e.target as HTMLInputElement).value))}
                                        class="w-full accent-cyan-400"/>
                                    <div class="flex justify-between text-[0.55rem] text-zinc-600 mono mt-1">
                                        <span>rápido</span><span>normal</span><span>lento</span>
                                    </div>
                                </div>
                                <div>
                                    <div class="flex items-baseline justify-between mb-2">
                                        <div class="eyebrow">multi-lang</div>
                                        <button onclick={toggleAutoLang}
                                            class="mono text-[0.55rem] uppercase tracking-wider {voicePrefs.auto_detect_language ? 'text-cyan-200' : 'text-zinc-600'}">
                                            auto-detect: {voicePrefs.auto_detect_language ? 'on' : 'off'}
                                        </button>
                                    </div>
                                    <div class="space-y-1.5 mb-3">
                                        {#each ['es','en','pt','fr','de'] as lang (lang)}
                                            <div class="flex items-center gap-2">
                                                <span class="mono text-[0.6rem] text-zinc-500 uppercase w-6">{lang}</span>
                                                <select value={voicePrefs.voice_per_lang[lang] || '__none__'}
                                                    onchange={(e) => setLangVoice(lang, (e.target as HTMLSelectElement).value)}
                                                    class="flex-1 mono text-[0.65rem] px-2 py-1 rounded-md bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none text-zinc-300">
                                                    <option value="__none__">— (use default)</option>
                                                    {#each installed.filter(v => !voicePrefs?.auto_detect_language || v.name.toLowerCase().startsWith(lang + '_')) as v (v.name)}
                                                        <option value={v.name}>{v.name}</option>
                                                    {/each}
                                                </select>
                                            </div>
                                        {/each}
                                    </div>
                                </div>

                                <div>
                                    <div class="eyebrow mb-2">test</div>
                                    <textarea bind:value={voiceTestText} rows="2"
                                        class="w-full mono text-[0.7rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-amber-300/40 focus:outline-none resize-none"></textarea>
                                    <div class="flex items-center gap-3 mt-2">
                                        <button onclick={() => testVoice()} disabled={voiceTestState.running}
                                            class="mono text-[0.7rem] uppercase tracking-wider text-amber-200 bg-amber-300/10 hairline border-amber-300/30 hover:bg-amber-300/20 px-4 py-1.5 rounded-lg disabled:opacity-30">
                                            {voiceTestState.running ? '…' : '▷ play'}
                                        </button>
                                        {#if voiceTestState.ms !== undefined}
                                            <span class="mono text-[0.65rem] text-zinc-500 tabular">{voiceTestState.ms}ms</span>
                                        {/if}
                                        {#if voiceTestState.error}
                                            <span class="text-[0.65rem] text-fuchsia-200 truncate">{voiceTestState.error}</span>
                                        {/if}
                                    </div>
                                </div>
                            </div>

                            <!-- Installed voices list -->
                            <div class="md:col-span-1">
                                <div class="eyebrow mb-2">installed</div>
                                {#if installed.length === 0}
                                    <p class="text-[0.7rem] text-zinc-500">Ninguna voz instalada.</p>
                                {:else}
                                    <ul class="space-y-1.5">
                                        {#each installed as v (v.name)}
                                            <li class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg hairline lift
                                                {voicePrefs.voice === v.name ? 'bg-cyan-400/05 border-cyan-400/40' : ''}">
                                                <PulseDot tone={voicePrefs.voice === v.name ? 'live' : 'mute'} size={5}/>
                                                <div class="flex-1 min-w-0">
                                                    <div class="mono text-[0.72rem] text-zinc-200 truncate">{v.name}</div>
                                                    <div class="mono text-[0.55rem] text-zinc-600">{v.language} · {v.size_mb}MB · {v.sample_rate ?? '?'}Hz</div>
                                                </div>
                                                <button onclick={() => testVoice(v.name)}
                                                    class="mono text-[0.55rem] uppercase tracking-wider text-zinc-500 hover:text-amber-200">play</button>
                                                {#if voicePrefs.voice !== v.name}
                                                    <button onclick={() => setVoice(v.name)}
                                                        class="mono text-[0.55rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">use</button>
                                                {/if}
                                                <button onclick={() => uninstallVoice(v.name)}
                                                    class="text-zinc-600 hover:text-fuchsia-200 px-1">×</button>
                                            </li>
                                        {/each}
                                    </ul>
                                {/if}
                            </div>

                            <!-- Catalog -->
                            <div class="md:col-span-1">
                                <div class="eyebrow mb-2">catalog</div>
                                {#if catalog.length === 0}
                                    <p class="text-[0.7rem] text-zinc-500">Catálogo vacío.</p>
                                {:else}
                                    <ul class="space-y-1.5 max-h-80 overflow-y-auto pr-1">
                                        {#each catalog as v (v.stem)}
                                            <li class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg hairline">
                                                <div class="flex-1 min-w-0">
                                                    <div class="mono text-[0.7rem] text-zinc-300 truncate">{v.stem}</div>
                                                    <div class="mono text-[0.55rem] text-zinc-600 truncate">{v.description} · {v.approx_mb}MB</div>
                                                </div>
                                                {#if v.installed}
                                                    <span class="chip bg-cyan-400/10 text-cyan-200 text-[0.55rem]">✓</span>
                                                {:else}
                                                    <button onclick={() => installVoice(v.stem)} disabled={installing[v.stem]}
                                                        class="mono text-[0.55rem] uppercase tracking-wider text-zinc-500 hover:text-amber-200 disabled:opacity-30">
                                                        {installing[v.stem] ? '…' : 'install'}
                                                    </button>
                                                {/if}
                                            </li>
                                        {/each}
                                    </ul>
                                {/if}
                            </div>
                        </div>
                    {/if}
                </Glass>
            </div>
        </div>
    </div>

    <!-- Router edit modal -->
    {#if routerEditMode !== 'closed'}
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onclick={() => (routerEditMode = 'closed')} role="presentation">
            <div class="glass rounded-2xl p-6 max-w-2xl w-full mx-6 max-h-[80vh] overflow-y-auto" onclick={(e) => e.stopPropagation()} role="presentation">
                <header class="flex justify-between items-baseline mb-4">
                    <div>
                        <span class="eyebrow">router.md</span>
                        <h3 class="text-lg font-light text-zinc-100">
                            {routerEditMode === 'direct' ? 'Direct answers' : 'Tier hints'}
                        </h3>
                    </div>
                    <button onclick={() => (routerEditMode = 'closed')} class="text-zinc-500 hover:text-zinc-200 text-xl leading-none">×</button>
                </header>

                {#if routerEditMode === 'direct'}
                    <p class="text-[0.78rem] text-zinc-500 mb-4">Regex (case-insensitive) → reply template. Variables: <code class="mono text-amber-200/70">{`{time} {date} {day} {user}`}</code></p>
                    <div class="space-y-2 mb-4">
                        {#each routerDirectDraft as r, i (i)}
                            <div class="flex gap-2 items-start">
                                <input bind:value={r.match} placeholder="^(hola|hey)\s*[!.?]?$"
                                    class="flex-1 mono text-[0.78rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none"/>
                                <input bind:value={r.reply} placeholder="Hola Coco."
                                    class="flex-1 text-[0.78rem] px-3 py-2 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none"/>
                                <button onclick={() => routerDirectDraft = routerDirectDraft.filter((_, j) => j !== i)}
                                    class="text-zinc-500 hover:text-fuchsia-200 px-2">×</button>
                            </div>
                        {/each}
                    </div>
                    <button onclick={() => (routerDirectDraft = [...routerDirectDraft, { match: '', reply: '' }])}
                        class="mono text-[0.7rem] uppercase tracking-wider text-zinc-400 hover:text-cyan-200 hairline px-3 py-1.5 rounded-lg mb-4">+ regla</button>
                {:else}
                    <p class="text-[0.78rem] text-zinc-500 mb-4">Patterns que fuerzan tier (heavy beats medium beats simple).</p>
                    {#each ['simple', 'medium', 'heavy'] as tier (tier)}
                        <div class="mb-4">
                            <div class="flex justify-between items-baseline mb-2">
                                <span class="mono text-[0.8rem] {tierTone(tier)} uppercase tracking-wider">{tier}</span>
                                <button onclick={() => (routerTierDraft[tier] = [...(routerTierDraft[tier] || []), ''])}
                                    class="mono text-[0.6rem] uppercase tracking-wider text-zinc-500 hover:text-cyan-200">+ pattern</button>
                            </div>
                            <div class="space-y-1.5">
                                {#each routerTierDraft[tier] || [] as p, i (tier + i)}
                                    <div class="flex gap-2">
                                        <input bind:value={routerTierDraft[tier][i]} placeholder="haz un plan"
                                            class="flex-1 mono text-[0.75rem] px-3 py-1.5 rounded-lg bg-white/[0.02] hairline focus:border-cyan-400/40 focus:outline-none"/>
                                        <button onclick={() => routerTierDraft[tier] = routerTierDraft[tier].filter((_, j) => j !== i)}
                                            class="text-zinc-500 hover:text-fuchsia-200 px-2">×</button>
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/each}
                {/if}

                <div class="flex justify-end gap-2 hairline-t pt-4">
                    <button onclick={() => (routerEditMode = 'closed')}
                        class="mono text-[0.7rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-200 px-4 py-2">cancelar</button>
                    <button onclick={saveRouterConfig}
                        class="mono text-[0.7rem] uppercase tracking-wider text-cyan-200 bg-cyan-400/10 hairline border-cyan-400/30 hover:bg-cyan-400/20 px-4 py-2 rounded-lg">guardar</button>
                </div>
            </div>
        </div>
    {/if}
</div>
