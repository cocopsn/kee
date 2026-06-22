<script lang="ts">
    import type { Snippet } from 'svelte';
    let {
        eyebrow,
        title,
        action,
        padded = true,
        soft = false,
        children,
    }: {
        eyebrow?: string;
        title?: string;
        action?: Snippet;
        padded?: boolean;
        soft?: boolean;
        children: Snippet;
    } = $props();
</script>

<section
    class="rounded-2xl {soft ? 'glass-soft' : 'glass'} flex h-full flex-col overflow-hidden lift"
>
    {#if eyebrow || title || action}
        <header class="flex items-baseline gap-3 px-5 pt-4 pb-3 hairline-b">
            <div class="flex flex-col gap-1">
                {#if eyebrow}<span class="eyebrow">{eyebrow}</span>{/if}
                {#if title}<h2 class="text-sm font-medium text-zinc-100 tracking-tight">{title}</h2>{/if}
            </div>
            {#if action}
                <div class="ml-auto">{@render action()}</div>
            {/if}
        </header>
    {/if}
    <div class="flex-1 min-h-0 overflow-auto {padded ? 'p-5' : ''}">
        {@render children()}
    </div>
</section>
