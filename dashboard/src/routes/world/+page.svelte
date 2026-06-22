<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { api, type EntityRow, type WorldEdgeRow, type ImpactRow } from '$lib/api';
    import Glass from '$lib/components/Glass.svelte';
    import Stat from '$lib/components/Stat.svelte';

    type Node = EntityRow & { x: number; y: number; vx: number; vy: number; r: number };

    let entities = $state<EntityRow[]>([]);
    let edges = $state<WorldEdgeRow[]>([]);
    let nodes = $state<Node[]>([]);
    let canvasEl: HTMLCanvasElement | undefined = $state();
    let containerEl: HTMLDivElement | undefined = $state();
    let ctx: CanvasRenderingContext2D | null = null;
    let raf = 0;
    let W = 800, H = 600, dpr = 1;
    let hoverId: string | null = $state(null);
    let selectedId: string | null = $state(null);
    let impact = $state<ImpactRow | null>(null);
    let mouseX = -9999, mouseY = -9999;

    // Color by entity type
    function typeColor(type: string): [number, number, number] {
        return ({
            project: [34, 211, 238],     // cyan
            system:  [167, 139, 250],    // violet
            service: [252, 211, 77],     // amber
            metric:  [240, 171, 252],    // fuchsia
            person:  [125, 211, 252],    // sky
            entity:  [156, 163, 175],    // zinc
        } as Record<string, [number, number, number]>)[type] ?? [156, 163, 175];
    }

    // Edge color by relation
    function relationColor(rel: string): string {
        return ({
            depends_on: 'rgba(56,189,248,0.45)',
            affects:    'rgba(252,211,77,0.45)',
            generates:  'rgba(167,139,250,0.55)',
            blocks:     'rgba(251,113,133,0.55)',
            owns:       'rgba(125,211,252,0.45)',
            uses:       'rgba(156,163,175,0.40)',
        } as Record<string, string>)[rel] ?? 'rgba(255,255,255,0.20)';
    }

    async function load() {
        try { entities = (await api.worldEntities()).entities; } catch {}
        try { edges = (await api.worldRelations()).edges; } catch {}
        seedLayout();
    }

    function seedLayout() {
        const cx = W / 2, cy = H / 2;
        const radius = Math.min(W, H) * 0.32;
        nodes = entities.map((e, i) => {
            const a = (i / Math.max(1, entities.length)) * Math.PI * 2;
            return {
                ...e,
                x: cx + Math.cos(a) * radius,
                y: cy + Math.sin(a) * radius,
                vx: 0, vy: 0,
                r: 8 + (e.criticality || 1) * 1.4,
            };
        });
    }

    function tick() {
        if (!ctx) { raf = requestAnimationFrame(tick); return; }
        const cx = W / 2, cy = H / 2;
        // Force simulation: spring on edges, repulsion node↔node, gravity to centre
        const nodeMap = new Map(nodes.map(n => [n.id, n]));
        for (const e of edges) {
            const a = nodeMap.get(e.source);
            const b = nodeMap.get(e.target);
            if (!a || !b) continue;
            const dx = b.x - a.x, dy = b.y - a.y;
            const d = Math.hypot(dx, dy) || 1;
            const target = 100 + (1 - e.weight) * 80;
            const f = (d - target) * 0.0015 * e.weight;
            const fx = (dx / d) * f, fy = (dy / d) * f;
            a.vx += fx; a.vy += fy;
            b.vx -= fx; b.vy -= fy;
        }
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const a = nodes[i], b = nodes[j];
                const dx = b.x - a.x, dy = b.y - a.y;
                const d2 = dx * dx + dy * dy + 1;
                const f = 5500 / d2;
                const inv = 1 / Math.sqrt(d2);
                const fx = dx * inv * f * 0.001, fy = dy * inv * f * 0.001;
                a.vx -= fx; a.vy -= fy;
                b.vx += fx; b.vy += fy;
            }
        }
        for (const n of nodes) {
            n.vx += (cx - n.x) * 0.0008;
            n.vy += (cy - n.y) * 0.0008;
            n.vx *= 0.85; n.vy *= 0.85;
            n.x += n.vx; n.y += n.vy;
        }

        // Hover hit test
        hoverId = null;
        for (const n of nodes) {
            const dx = mouseX - n.x, dy = mouseY - n.y;
            if (Math.hypot(dx, dy) < n.r + 4) { hoverId = n.id; break; }
        }

        draw();
        raf = requestAnimationFrame(tick);
    }

    function draw() {
        if (!ctx) return;
        ctx.fillStyle = 'rgba(8, 9, 11, 0.30)';
        ctx.fillRect(0, 0, W, H);

        // Edges
        ctx.lineCap = 'round';
        for (const e of edges) {
            const a = nodes.find(n => n.id === e.source);
            const b = nodes.find(n => n.id === e.target);
            if (!a || !b) continue;
            const isHover = hoverId && (e.source === hoverId || e.target === hoverId);
            const isSel = selectedId && (e.source === selectedId || e.target === selectedId);
            ctx.strokeStyle = isSel ? 'rgba(34,211,238,0.85)'
                            : isHover ? 'rgba(252,211,77,0.65)'
                            : relationColor(e.relation);
            ctx.lineWidth = 0.6 + e.weight * 1.4 + (isHover || isSel ? 1 : 0);
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
            // Arrow head
            const dx = b.x - a.x, dy = b.y - a.y;
            const d = Math.hypot(dx, dy) || 1;
            const ex = b.x - (dx / d) * (b.r + 2);
            const ey = b.y - (dy / d) * (b.r + 2);
            const ang = Math.atan2(dy, dx);
            ctx.beginPath();
            ctx.moveTo(ex, ey);
            ctx.lineTo(ex - Math.cos(ang - 0.4) * 6, ey - Math.sin(ang - 0.4) * 6);
            ctx.lineTo(ex - Math.cos(ang + 0.4) * 6, ey - Math.sin(ang + 0.4) * 6);
            ctx.closePath();
            ctx.fill();
        }
        // Nodes
        for (const n of nodes) {
            const c = typeColor(n.type);
            const isHover = n.id === hoverId;
            const isSel = n.id === selectedId;
            // Halo
            const halo = ctx.createRadialGradient(n.x, n.y, n.r * 0.5, n.x, n.y, n.r * 3);
            halo.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${isSel ? 0.55 : 0.30})`);
            halo.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
            ctx.fillStyle = halo;
            ctx.fillRect(n.x - n.r * 3, n.y - n.r * 3, n.r * 6, n.r * 6);
            // Body
            ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${isSel || isHover ? 1 : 0.85})`;
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
            ctx.fill();
            // Label
            ctx.fillStyle = isSel ? 'rgb(244,244,245)' : isHover ? 'rgba(244,244,245,0.85)' : 'rgba(244,244,245,0.55)';
            ctx.font = `${isSel || isHover ? '500' : '400'} ${isSel || isHover ? 12 : 10}px Inter, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(n.name, n.x, n.y + n.r + 11);
            // Criticality dot
            if ((n.criticality || 0) >= 8) {
                ctx.fillStyle = 'rgba(252,211,77,0.95)';
                ctx.beginPath();
                ctx.arc(n.x + n.r - 2, n.y - n.r + 2, 2.5, 0, Math.PI * 2);
                ctx.fill();
            }
        }
    }

    function resize() {
        if (!canvasEl || !containerEl) return;
        dpr = Math.min(2, window.devicePixelRatio || 1);
        const rect = containerEl.getBoundingClientRect();
        W = Math.max(1, Math.floor(rect.width));
        H = Math.max(1, Math.floor(rect.height));
        canvasEl.width = Math.floor(W * dpr);
        canvasEl.height = Math.floor(H * dpr);
        canvasEl.style.width = W + 'px';
        canvasEl.style.height = H + 'px';
        if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (entities.length) seedLayout();
    }

    function onMouseMove(e: MouseEvent) {
        if (!containerEl) return;
        const rect = containerEl.getBoundingClientRect();
        mouseX = e.clientX - rect.left;
        mouseY = e.clientY - rect.top;
    }
    function onMouseLeave() { mouseX = -9999; mouseY = -9999; hoverId = null; }
    async function onClick() {
        if (!hoverId) return;
        selectedId = hoverId;
        try {
            impact = await api.worldImpact(selectedId, 3);
        } catch (e) {
            impact = null;
        }
    }

    let ro: ResizeObserver | null = null;
    onMount(() => {
        if (!canvasEl) return;
        ctx = canvasEl.getContext('2d', { alpha: true });
        load().then(() => {
            resize();
            raf = requestAnimationFrame(tick);
        });
        ro = new ResizeObserver(() => resize());
        if (containerEl) ro.observe(containerEl);
    });
    onDestroy(() => {
        if (raf) cancelAnimationFrame(raf);
        ro?.disconnect();
    });

    let typeCounts = $derived.by(() => {
        const m = new Map<string, number>();
        for (const e of entities) m.set(e.type, (m.get(e.type) || 0) + 1);
        return [...m.entries()].sort((a, b) => b[1] - a[1]);
    });
    let selectedEntity = $derived(entities.find(e => e.id === selectedId));
</script>

<div class="h-full flex flex-col">
    <div class="px-6 py-4 hairline-b">
        <div class="flex items-end gap-8">
            <div>
                <span class="eyebrow">causal graph</span>
                <h1 class="text-2xl font-light text-zinc-100 mt-1.5 tracking-tight">World model</h1>
            </div>
            <div class="ml-auto flex gap-10">
                <Stat label="entities" value={entities.length} />
                <Stat label="edges" value={edges.length} accent="cyan" />
                {#if selectedEntity}
                    <Stat label="selected" value={selectedEntity.name} />
                    <Stat label="criticality" value={selectedEntity.criticality ?? 0} accent="gold" />
                {/if}
            </div>
        </div>
        <!-- Type legend -->
        <div class="flex items-center gap-4 mt-3 text-[0.7rem]">
            {#each typeCounts as [t, n] (t)}
                {@const c = typeColor(t)}
                <span class="flex items-center gap-1.5">
                    <span class="inline-block w-2.5 h-2.5 rounded-full" style="background: rgb({c[0]},{c[1]},{c[2]});"></span>
                    <span class="mono text-zinc-400">{t}</span>
                    <span class="mono text-zinc-600 tabular">{n}</span>
                </span>
            {/each}
            <span class="ml-auto mono text-[0.65rem] text-zinc-600">click un nodo para impact analysis</span>
        </div>
    </div>

    <div class="flex-1 grid grid-cols-12 min-h-0">
        <!-- Canvas -->
        <div bind:this={containerEl} class="col-span-9 relative" onmousemove={onMouseMove} onmouseleave={onMouseLeave} onclick={onClick} role="presentation">
            <canvas bind:this={canvasEl} class="absolute inset-0 cursor-pointer"></canvas>
        </div>
        <!-- Inspector -->
        <div class="col-span-3 hairline border-l overflow-y-auto p-4 space-y-3" style="border-left: 1px solid var(--color-hairline);">
            {#if !selectedEntity}
                <p class="text-[0.78rem] text-zinc-500">Click un nodo para ver detalles + análisis de impacto.</p>
            {:else}
                <div>
                    <span class="eyebrow">entity</span>
                    <h3 class="text-base text-zinc-100 mt-0.5">{selectedEntity.name}</h3>
                    <div class="flex items-center gap-2 mt-1">
                        <span class="chip mono text-[0.6rem] bg-white/[0.04] text-zinc-300">{selectedEntity.type}</span>
                        <span class="chip mono text-[0.6rem] bg-amber-300/10 text-amber-200">crit {selectedEntity.criticality}</span>
                    </div>
                </div>
                {#if Object.keys(selectedEntity.state ?? {}).length > 0}
                    <div>
                        <div class="eyebrow mb-1">state</div>
                        <pre class="mono text-[0.7rem] text-zinc-400 bg-white/[0.02] p-2 rounded-lg whitespace-pre-wrap">{JSON.stringify(selectedEntity.state, null, 2)}</pre>
                    </div>
                {/if}
                {#if selectedEntity.notes}
                    <div>
                        <div class="eyebrow mb-1">notes</div>
                        <p class="text-[0.78rem] text-zinc-300">{selectedEntity.notes}</p>
                    </div>
                {/if}
                {#if impact}
                    <div class="hairline-t pt-3">
                        <div class="eyebrow mb-2">impact analysis (depth 3)</div>
                        <div class="mb-2 flex items-baseline justify-between">
                            <span class="text-[0.78rem] text-zinc-400">score</span>
                            <span class="mono text-lg tabular {impact.score >= 10 ? 'text-fuchsia-200' : impact.score >= 5 ? 'text-amber-200' : 'text-zinc-300'}">{impact.score.toFixed(2)}</span>
                        </div>
                        <div class="mb-2 flex items-baseline justify-between">
                            <span class="text-[0.78rem] text-zinc-400">recommend</span>
                            <span class="mono text-[0.7rem] text-cyan-200">{impact.recommendation}</span>
                        </div>
                        <div class="mb-2 flex items-baseline justify-between">
                            <span class="text-[0.78rem] text-zinc-400">affected</span>
                            <span class="mono text-[0.78rem] tabular text-zinc-300">{impact.affected_count}</span>
                        </div>
                        {#if impact.affected.length > 0}
                            <div class="eyebrow mb-1 mt-3">downstream</div>
                            <ul class="space-y-0.5 text-[0.7rem] mono">
                                {#each impact.affected as a, i (i)}
                                    <li class="text-zinc-400">→ {JSON.stringify(a).slice(0, 60)}</li>
                                {/each}
                            </ul>
                        {/if}
                    </div>
                {/if}
            {/if}
        </div>
    </div>
</div>
