<script lang="ts">
    type Tone = 'live' | 'warm' | 'mute' | 'alert';
    let {
        tone = 'live',
        label,
        size = 6,
    }: { tone?: Tone; label?: string; size?: number } = $props();

    const colors: Record<Tone, string> = {
        live: 'bg-cyan-400',
        warm: 'bg-amber-300',
        mute: 'bg-zinc-500',
        alert: 'bg-fuchsia-300',
    };
    const halos: Record<Tone, string> = {
        live: 'bg-cyan-400/30',
        warm: 'bg-amber-300/30',
        mute: 'bg-zinc-500/30',
        alert: 'bg-fuchsia-300/30',
    };
</script>

<span class="inline-flex items-center gap-2">
    <span class="relative inline-flex" style="width:{size}px;height:{size}px;">
        <span class="absolute inset-0 rounded-full {halos[tone]} breathe"></span>
        <span class="relative inline-block rounded-full {colors[tone]}" style="width:{size}px;height:{size}px;"></span>
    </span>
    {#if label}
        <span class="text-[0.6875rem] uppercase tracking-[0.14em] text-zinc-400">{label}</span>
    {/if}
</span>
