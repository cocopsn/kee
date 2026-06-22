<script lang="ts">
    let { size = 18, glow = false }: { size?: number; glow?: boolean } = $props();
</script>

<svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    class:drop-shadow={glow}
    style={glow ? 'filter: drop-shadow(0 0 6px rgb(34 211 238 / 0.6));' : ''}
>
    <!-- Custom Kee glyph: a stylised K formed by an arc + two strokes,
         echoing a sound wave / nervous-system trace. -->
    <path d="M5 4v16" />
    <path d="M5 12c5-4 9-4 14-8" opacity="0.55" />
    <path d="M5 12c5 4 9 4 14 8" opacity="0.85" />
    <circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" />
</svg>
