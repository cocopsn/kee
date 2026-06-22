<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';
    import PulseDot from '$lib/components/PulseDot.svelte';

    let state = $state<any>(null);
    let loading = $state(true);
    let timer: any;

    async function refresh() {
        try { state = await api.voiceState(); } catch {}
        loading = false;
    }

    onMount(() => { refresh(); timer = setInterval(refresh, 5000); });
    onDestroy(() => clearInterval(timer));

    function fmtBytes(n: number): string {
        if (n < 1024) return `${n}B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
        return `${(n / (1024 * 1024)).toFixed(2)}MB`;
    }
</script>

<div class="h-full overflow-y-auto px-6 py-5">
    <div class="mx-auto max-w-7xl">
        <header class="mb-6 flex items-end gap-8">
            <div>
                <span class="eyebrow">audio i/o</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">Voice</h1>
                <p class="text-sm text-zinc-500 mt-1">Wake-word + STT + TTS pipeline.</p>
            </div>
        </header>

        <div class="grid gap-4 lg:grid-cols-3">
            <!-- Wake-word -->
            <Glass eyebrow="wake-word" title="kee.onnx">
                {#if loading}
                    <div class="h-24 skeleton"></div>
                {:else if state?.wake_word.exists}
                    <div class="flex items-baseline gap-3 mb-2">
                        <PulseDot tone="live" />
                        <span class="mono text-[0.85rem] text-cyan-200">READY</span>
                        <span class="mono text-[0.65rem] text-zinc-500 tabular ml-auto">{fmtBytes(state.wake_word.bytes)}</span>
                    </div>
                    <p class="text-[0.7rem] text-zinc-600 mono break-all">{state.wake_word.path}</p>
                {:else}
                    <div class="flex items-baseline gap-3 mb-2">
                        <PulseDot tone="warm" />
                        <span class="mono text-[0.85rem] text-amber-200">TRAINING</span>
                    </div>
                    <p class="text-[0.7rem] text-zinc-500 mt-2">
                        Sin .onnx aún. Fallback: <span class="mono text-amber-200/70">{state?.wake_word.fallback}</span>
                    </p>
                {/if}
            </Glass>

            <!-- TTS -->
            <Glass eyebrow="text-to-speech" title="Piper">
                {#if loading}
                    <div class="h-24 skeleton"></div>
                {:else if state?.tts.exists}
                    <div class="flex items-baseline gap-3 mb-2">
                        <PulseDot tone="live" />
                        <span class="mono text-[0.85rem] text-cyan-200">READY</span>
                        <span class="mono text-[0.65rem] text-zinc-500 tabular ml-auto">{fmtBytes(state.tts.bytes)}</span>
                    </div>
                    <p class="text-[0.7rem] text-zinc-400 mono">{state.tts.voice}</p>
                    <p class="text-[0.65rem] text-zinc-600 mono break-all mt-1">{state.tts.path}</p>
                {:else}
                    <div class="flex items-baseline gap-3 mb-2">
                        <PulseDot tone="alert" />
                        <span class="mono text-[0.85rem] text-fuchsia-200">MISSING</span>
                    </div>
                {/if}
            </Glass>

            <!-- STT -->
            <Glass eyebrow="speech-to-text" title="Whisper">
                {#if state}
                    <p class="text-[0.78rem] text-zinc-300">{state.stt.model}</p>
                    <p class="text-[0.7rem] text-zinc-500 mt-2">Languages: <span class="mono">{state.stt.languages.join(', ')}</span></p>
                    <p class="text-[0.65rem] text-zinc-600 mt-3">CT2 format (CTranslate2), no .onnx — descarga lazy en primer voice run.</p>
                {/if}
            </Glass>

            {#if state?.training}
                <div class="lg:col-span-3">
                    <Glass eyebrow="training" title="Wake-word training log (live)">
                        <div class="flex items-baseline gap-3 mb-3">
                            <Stat label="recorded samples" value={state.training.samples_recorded} accent={state.training.samples_recorded > 0 ? 'cyan' : 'plain'} />
                        </div>
                        <pre class="mono text-[0.65rem] text-zinc-400 leading-snug whitespace-pre overflow-auto bg-white/[0.02] p-3 rounded-lg" style="max-height: 50vh;">{state.training.log_tail || '(no log)'}</pre>
                    </Glass>
                </div>
            {/if}
        </div>
    </div>
</div>
