<script lang="ts">
    // The trailing @ on +layout@.svelte tells SvelteKit to break out of
    // the root layout (Brand bar, tabs, neural canvas). The HUD lives in
    // a 360x540 frameless window so any of that chrome would be wrong.
    import '../../app.css';
    let { children } = $props();
</script>

<div class="hud-shell">
    {@render children?.()}
</div>

<style>
    /* Frameless transparent window — the WebView has no titlebar so we
       simulate one inside the HUD card itself. Background drawn here is
       a near-black panel, not full transparent, so OS shadow looks right. */
    :global(html, body, #svelte) {
        background: transparent !important;
        margin: 0;
        padding: 0;
        height: 100%;
        overflow: hidden;
    }
    .hud-shell {
        height: 100vh;
        display: flex;
        flex-direction: column;
        font-family: 'Inter', system-ui, sans-serif;
        color: #f4f4f5;
    }
</style>
