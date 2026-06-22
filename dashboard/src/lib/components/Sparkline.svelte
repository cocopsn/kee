<script lang="ts">
    /**
     * Tiny inline area chart for trends. SVG-based, no deps.
     * Pass a series of numbers; auto-scales Y.
     */
    let {
        values,
        width = 120,
        height = 28,
        color = 'rgb(34, 211, 238)',
        fill = true,
    }: {
        values: number[];
        width?: number;
        height?: number;
        color?: string;
        fill?: boolean;
    } = $props();

    let path = $derived.by(() => {
        if (!values.length) return { d: '', area: '' };
        const n = values.length;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const range = max - min || 1;
        const stepX = width / Math.max(1, n - 1);
        const points = values.map((v, i) => {
            const x = i * stepX;
            const y = height - ((v - min) / range) * (height - 2) - 1;
            return [x, y];
        });
        const d = points.map(([x, y], i) => (i === 0 ? `M ${x.toFixed(1)} ${y.toFixed(1)}` : `L ${x.toFixed(1)} ${y.toFixed(1)}`)).join(' ');
        const area = `${d} L ${width} ${height} L 0 ${height} Z`;
        return { d, area };
    });
</script>

<svg viewBox="0 0 {width} {height}" class="block" style="width: {width}px; height: {height}px;" aria-hidden="true">
    {#if fill && path.area}
        <path d={path.area} fill={color} opacity="0.15" />
    {/if}
    {#if path.d}
        <path d={path.d} fill="none" stroke={color} stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" />
    {/if}
</svg>
