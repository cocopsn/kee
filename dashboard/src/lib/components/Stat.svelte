<script lang="ts">
    import { onMount } from 'svelte';
    let {
        label,
        value,
        unit = '',
        loading = false,
        accent = 'plain',
    }: {
        label: string;
        value: number | string | null;
        unit?: string;
        loading?: boolean;
        accent?: 'plain' | 'cyan' | 'gold';
    } = $props();

    // Spring-easing from previous to next numeric value
    let display = $state<string>(typeof value === 'number' ? '0' : (value ?? ''));
    let raf = 0;
    function tween(from: number, to: number, ms = 700) {
        const start = performance.now();
        cancelAnimationFrame(raf);
        const step = (now: number) => {
            const t = Math.min(1, (now - start) / ms);
            // ease-out-quart for that "luxury settle" feel
            const e = 1 - Math.pow(1 - t, 4);
            const v = from + (to - from) * e;
            display = formatNum(v, to);
            if (t < 1) raf = requestAnimationFrame(step);
        };
        raf = requestAnimationFrame(step);
    }
    function formatNum(v: number, target: number): string {
        if (Number.isInteger(target)) return Math.round(v).toLocaleString('en-US');
        return v.toFixed(2);
    }
    onMount(() => {
        if (typeof value === 'number') tween(0, value);
    });
    let prev = typeof value === 'number' ? value : 0;
    $effect(() => {
        if (typeof value === 'number') {
            tween(prev, value);
            prev = value;
        } else {
            display = value ?? '';
        }
    });

    const accentClass = {
        plain: 'text-zinc-100',
        cyan: 'text-cyan-300',
        gold: 'text-amber-200',
    }[accent];
</script>

<div class="flex flex-col gap-1.5">
    <span class="eyebrow">{label}</span>
    {#if loading}
        <div class="h-7 w-24 skeleton"></div>
    {:else}
        <div class="flex items-baseline gap-1.5">
            <span class="mono text-2xl font-medium {accentClass}" style="letter-spacing:-0.02em;">
                {display}
            </span>
            {#if unit}<span class="mono text-xs text-zinc-500">{unit}</span>{/if}
        </div>
    {/if}
</div>
