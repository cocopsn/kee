<script lang="ts" context="module">
    import { writable } from 'svelte/store';

    type Toast = { id: number; tone: 'info' | 'success' | 'warn' | 'error'; text: string };

    export const toasts = writable<Toast[]>([]);
    let nextId = 1;

    export function toast(text: string, tone: Toast['tone'] = 'info', durationMs = 3500): void {
        const id = nextId++;
        toasts.update((arr) => [...arr, { id, tone, text }]);
        if (durationMs > 0) {
            setTimeout(() => {
                toasts.update((arr) => arr.filter((t) => t.id !== id));
            }, durationMs);
        }
    }
</script>

<script lang="ts">
    let items: Toast[] = $state([]);
    toasts.subscribe((v) => (items = v));

    function dismiss(id: number) {
        toasts.update((arr) => arr.filter((t) => t.id !== id));
    }

    function toneColor(t: Toast['tone']): string {
        return ({
            info:    'border-cyan-400/30 bg-cyan-400/10 text-cyan-100',
            success: 'border-cyan-400/30 bg-cyan-400/15 text-cyan-100',
            warn:    'border-amber-300/30 bg-amber-300/15 text-amber-100',
            error:   'border-fuchsia-400/30 bg-fuchsia-400/15 text-fuchsia-100',
        } as Record<string, string>)[t];
    }
</script>

<div class="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm pointer-events-none">
    {#each items as t (t.id)}
        <div class="hairline rounded-xl px-4 py-2.5 text-[0.78rem] backdrop-blur-xl pointer-events-auto cursor-pointer transition-opacity {toneColor(t.tone)}"
            onclick={() => dismiss(t.id)} role="presentation">
            {t.text}
        </div>
    {/each}
</div>
