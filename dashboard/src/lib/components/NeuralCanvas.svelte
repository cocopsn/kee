<script lang="ts" context="module">
    export type AgentState = 'idle' | 'thinking' | 'executing' | 'speaking' | 'error';
</script>

<script lang="ts">
    /**
     * NeuralCanvas v5 — neural-digital synapse / energy sphere.
     *
     * Designed to feel like an organism, not animation. Layers:
     *
     *   1. Plasma background — slow procedural noise field tinted by state
     *   2. Plexus mesh (~80 free nodes, distance-attenuated lines, mouse pull)
     *   3. Wireframe energy SPHERE — meridians + parallels, projected 3D,
     *      slowly rotating, with depth fade
     *   4. Brain core — dense central cluster of glow points firing rapidly
     *   5. Synapse arcs — branching lightning between random pairs of points
     *   6. Energy halo dust — ~700 twinkling particles in concentric bands
     *   7. Equalizer ring — 96 radial bars driven by activity energy
     *   8. Wave shockwaves — concentric expanding rings on event pulses
     *   9. Glitch fragmentation — RGB-channel scanline displacement bursts
     *      (random short flashes when state is `executing` or `error`)
     *  10. Bloom pass — offscreen blur + lighter composite for real glow
     *  11. Central void + label
     *
     * Reactive props:
     *   - agentState: drives palette + speed + glitch frequency
     *   - pulseTrigger: bump → wave + synapse burst
     *   - density: 0.5..1.5 multiplier on particle counts
     *   - label: center text
     *
     * Performance: ~1500 particles, single rAF loop, devicePixelRatio-aware,
     * ResizeObserver. Bloom done at half-res offscreen.
     */
    import { onMount, onDestroy } from 'svelte';

    let {
        agentState = 'idle',
        pulseTrigger = 0,
        label = 'KEE',
        density = 1.0,
    }: {
        agentState?: AgentState;
        pulseTrigger?: number;
        label?: string;
        density?: number;
    } = $props();

    // ── DOM refs ─────────────────────────────────────────────────────────
    // Three canvases:
    //   • canvasEl (visible) holds trail-accumulated final composition
    //   • frameBuf (offscreen) — THIS-frame content only, cleared each frame.
    //     Critical: bloom samples THIS, never canvasEl, to break the feedback
    //     loop (sampling canvasEl + drawing back with `lighter` saturates to
    //     white in ~30 frames).
    //   • glowBuf (offscreen, half-res) — blurred copy of frameBuf for bloom
    let containerEl: HTMLDivElement | undefined = $state();
    let canvasEl: HTMLCanvasElement | undefined = $state();
    let mainCtx: CanvasRenderingContext2D | null = null;
    let frameBuf: HTMLCanvasElement | null = null;
    let frameCtx: CanvasRenderingContext2D | null = null;
    let glowBuf: HTMLCanvasElement | null = null;
    let glowCtx: CanvasRenderingContext2D | null = null;
    let dpr = 1;
    let W = 0, H = 0, cx = 0, cy = 0, minDim = 0;
    let raf = 0;
    let frame = 0;
    let mouseX = -9999, mouseY = -9999;

    // ── Palettes per state ───────────────────────────────────────────────
    type Palette = {
        primary: [number, number, number];
        accent:  [number, number, number];
        spark:   [number, number, number];
        bgPulse: number;
        speed: number;
        clear: number;
        glowAlpha: number;
        glitchProb: number;   // per-frame chance of glitch burst
    };
    const PALETTES: Record<AgentState, Palette> = {
        idle:      { primary: [56, 189, 248],  accent: [165, 180, 252], spark: [186, 230, 253], bgPulse: 0.55, speed: 0.7, clear: 0.18, glowAlpha: 0.55, glitchProb: 0.002 },
        thinking:  { primary: [125, 211, 252], accent: [196, 181, 253], spark: [255, 255, 255], bgPulse: 0.95, speed: 1.5, clear: 0.10, glowAlpha: 0.78, glitchProb: 0.010 },
        executing: { primary: [217, 70,  239], accent: [253, 224, 71],  spark: [56, 189, 248],  bgPulse: 1.20, speed: 1.8, clear: 0.08, glowAlpha: 0.92, glitchProb: 0.030 },
        speaking:  { primary: [56, 189, 248],  accent: [255, 255, 255], spark: [125, 211, 252], bgPulse: 1.30, speed: 2.0, clear: 0.07, glowAlpha: 0.95, glitchProb: 0.005 },
        error:     { primary: [251, 113, 133], accent: [251, 191, 36],  spark: [253, 224, 71],  bgPulse: 1.45, speed: 1.6, clear: 0.06, glowAlpha: 0.95, glitchProb: 0.060 },
    };
    const palette = $derived(PALETTES[agentState] ?? PALETTES.idle);

    // Animated colour state
    let curPrimary: [number, number, number] = $state([56, 189, 248]);
    let curAccent:  [number, number, number] = $state([165, 180, 252]);
    let curSpark:   [number, number, number] = $state([186, 230, 253]);
    let curBgPulse = $state(0.55);
    let curSpeed   = $state(0.7);
    let curClear   = $state(0.18);
    let curGlow    = $state(0.55);
    let curGlitch  = $state(0.002);

    function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
    function lerpCol(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
        return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
    }
    function rgba(c: [number, number, number], a: number): string {
        return `rgba(${c[0]|0}, ${c[1]|0}, ${c[2]|0}, ${a})`;
    }

    // ── Particle systems ─────────────────────────────────────────────────
    type Plexus = { x: number; y: number; vx: number; vy: number; phase: number; depth: number };
    type Dust   = { r: number; angle: number; speed: number; size: number; twinkle: number; warm: boolean };
    type SphereNode = { lat: number; lon: number };
    type Synapse = { points: { x: number; y: number }[]; bornAt: number; life: number; hue: 'primary' | 'accent' | 'spark'; thickness: number };
    type Wave   = { bornAt: number; strength: number; hue: 'primary' | 'accent' | 'spark' };
    type BrainSpike = { x: number; y: number; size: number; bornAt: number };

    let plexus: Plexus[] = [];
    let dust:   Dust[] = [];
    let sphereNodes: SphereNode[] = [];
    let synapses: Synapse[] = [];
    let waves: Wave[] = [];
    let brainSpikes: BrainSpike[] = [];
    let bars = new Float32Array(96);
    let energy = 0;
    let spin = 0;
    let glitchActive = 0; // > 0 while a glitch flash is showing

    // ── Sizing ───────────────────────────────────────────────────────────
    function resize() {
        if (!canvasEl || !containerEl) return;
        dpr = Math.min(2, window.devicePixelRatio || 1);
        const rect = containerEl.getBoundingClientRect();
        W = Math.max(1, Math.floor(rect.width));
        H = Math.max(1, Math.floor(rect.height));
        cx = W / 2; cy = H / 2;
        minDim = Math.min(W, H);
        canvasEl.width = Math.floor(W * dpr);
        canvasEl.height = Math.floor(H * dpr);
        canvasEl.style.width = W + 'px';
        canvasEl.style.height = H + 'px';
        if (mainCtx) mainCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // frameBuf is full-res — every drawing op happens here this frame
        frameBuf = document.createElement('canvas');
        frameBuf.width = Math.floor(W * dpr);
        frameBuf.height = Math.floor(H * dpr);
        frameCtx = frameBuf.getContext('2d', { alpha: true });
        if (frameCtx) frameCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // glowBuf is half-res — blur is cheaper, looks identical at this scale
        glowBuf = document.createElement('canvas');
        glowBuf.width = Math.max(1, Math.floor(W / 2));
        glowBuf.height = Math.max(1, Math.floor(H / 2));
        glowCtx = glowBuf.getContext('2d');
        seedAll();
    }

    function seedAll() {
        const dMul = density;
        // Plexus drifting nodes
        const nP = Math.round(80 * dMul);
        plexus = Array.from({ length: nP }, () => ({
            x: Math.random() * W,
            y: Math.random() * H,
            vx: (Math.random() - 0.5) * 0.16,
            vy: (Math.random() - 0.5) * 0.16,
            phase: Math.random() * Math.PI * 2,
            depth: 0.3 + Math.random() * 0.7,
        }));
        // Halo dust in 6 bands
        const nD = Math.round(700 * dMul);
        const bands = [0.16, 0.20, 0.25, 0.30, 0.36, 0.42].map(f => minDim * f);
        dust = Array.from({ length: nD }, () => {
            const r = bands[Math.floor(Math.random() * bands.length)] + (Math.random() - 0.5) * 16;
            return {
                r,
                angle: Math.random() * Math.PI * 2,
                speed: (Math.random() < 0.5 ? 1 : -1) * (0.0002 + Math.random() * 0.0010),
                size: 0.3 + Math.random() * 0.9,
                twinkle: Math.random() * Math.PI * 2,
                warm: Math.random() < 0.10,
            };
        });
        // Sphere wireframe nodes — distributed on a grid of lat/lon
        sphereNodes = [];
        const latSteps = 14, lonSteps = 24;
        for (let i = 1; i < latSteps; i++) {
            const lat = (i / latSteps - 0.5) * Math.PI;
            for (let j = 0; j < lonSteps; j++) {
                const lon = (j / lonSteps) * Math.PI * 2;
                sphereNodes.push({ lat, lon });
            }
        }
        bars = new Float32Array(96);
    }

    // ── Sphere projection ────────────────────────────────────────────────
    function projectSphere(lat: number, lon: number, t: number) {
        const r = minDim * 0.27;
        const rotY = t * 0.3;
        const rotX = Math.sin(t * 0.15) * 0.4;
        const x = Math.cos(lat) * Math.cos(lon + rotY) * r;
        const y = Math.cos(lat) * Math.sin(lon + rotY) * r;
        const z = Math.sin(lat) * r;
        // Apply X rotation
        const y2 = y * Math.cos(rotX) - z * Math.sin(rotX);
        const z2 = y * Math.sin(rotX) + z * Math.cos(rotX);
        // Simple parallel projection with depth shading
        return { px: cx + x, py: cy + y2, depth: (z2 / r + 1) * 0.5 };
    }

    // ── Lightning generator ──────────────────────────────────────────────
    function jitterPath(x1: number, y1: number, x2: number, y2: number, depth = 5): { x: number; y: number }[] {
        if (depth <= 0) return [{ x: x1, y: y1 }, { x: x2, y: y2 }];
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
        const dx = x2 - x1, dy = y2 - y1;
        const norm = Math.hypot(dx, dy) || 1;
        const offset = (Math.random() - 0.5) * norm * 0.22;
        const px = mx + (-dy / norm) * offset;
        const py = my + ( dx / norm) * offset;
        return [
            ...jitterPath(x1, y1, px, py, depth - 1).slice(0, -1),
            ...jitterPath(px, py, x2, y2, depth - 1),
        ];
    }

    function spawnSynapse(strength = 1) {
        const r1 = minDim * 0.14;
        const r2 = minDim * (0.36 + Math.random() * 0.18);
        const a1 = Math.random() * Math.PI * 2;
        const a2 = a1 + (Math.random() - 0.5) * Math.PI * 1.2;
        const x1 = cx + Math.cos(a1) * r1;
        const y1 = cy + Math.sin(a1) * r1;
        const x2 = cx + Math.cos(a2) * r2;
        const y2 = cy + Math.sin(a2) * r2;
        const points = jitterPath(x1, y1, x2, y2, 6);
        synapses.push({
            points,
            bornAt: frame,
            life: 22 + Math.random() * 18,
            hue: Math.random() < 0.55 ? 'primary' : (Math.random() < 0.6 ? 'accent' : 'spark'),
            thickness: 1 + Math.random() * 1.8,
        });
        // Branches
        const branchCount = strength > 0.7 ? 2 : 1;
        for (let bi = 0; bi < branchCount; bi++) {
            if (Math.random() < 0.65 * strength) {
                const idx = Math.floor(points.length * (0.3 + Math.random() * 0.4));
                const mid = points[idx];
                const a3 = Math.random() * Math.PI * 2;
                const len = minDim * (0.05 + Math.random() * 0.10);
                const x3 = mid.x + Math.cos(a3) * len;
                const y3 = mid.y + Math.sin(a3) * len;
                synapses.push({
                    points: jitterPath(mid.x, mid.y, x3, y3, 4),
                    bornAt: frame,
                    life: 14,
                    hue: 'spark',
                    thickness: 0.7,
                });
            }
        }
        // Brain spike inside the void
        brainSpikes.push({
            x: cx + (Math.random() - 0.5) * minDim * 0.08,
            y: cy + (Math.random() - 0.5) * minDim * 0.08,
            size: 1.5 + Math.random() * 2.5,
            bornAt: frame,
        });
    }

    function spawnWave(strength = 1, hue: 'primary' | 'accent' | 'spark' = 'primary') {
        waves.push({ bornAt: frame, strength, hue });
        energy = Math.min(2.8, energy + 0.85 * strength);
    }

    // Pulse trigger
    let lastTrigger = 0;
    $effect(() => {
        if (pulseTrigger !== lastTrigger) {
            lastTrigger = pulseTrigger;
            spawnWave(1.2, 'primary');
            for (let k = 0; k < 5; k++) setTimeout(() => spawnSynapse(1), k * 45);
        }
    });

    // Ambient activity by state
    function ambientSpawn() {
        const interval = agentState === 'idle' ? 50
                       : agentState === 'thinking' ? 8
                       : agentState === 'executing' ? 5
                       : agentState === 'speaking' ? 4
                       : 8;
        if (frame % interval === 0 && Math.random() < 0.85) spawnSynapse(0.6);

        // Brain spikes constantly firing inside the void
        if (frame % (agentState === 'idle' ? 8 : 3) === 0) {
            brainSpikes.push({
                x: cx + (Math.random() - 0.5) * minDim * 0.10,
                y: cy + (Math.random() - 0.5) * minDim * 0.10,
                size: 0.8 + Math.random() * 1.8,
                bornAt: frame,
            });
        }

        // Occasional waves on speaking
        if (agentState === 'speaking' && frame % 60 === 0) spawnWave(0.5, 'spark');
        if (agentState === 'error' && frame % 30 === 0) spawnWave(0.7, 'accent');

        // Glitch decision
        if (Math.random() < curGlitch) glitchActive = 6 + Math.floor(Math.random() * 6);
    }

    // ── Mouse ────────────────────────────────────────────────────────────
    function onMouseMove(e: MouseEvent) {
        if (!containerEl) return;
        const rect = containerEl.getBoundingClientRect();
        mouseX = e.clientX - rect.left;
        mouseY = e.clientY - rect.top;
    }
    function onMouseLeave() { mouseX = -9999; mouseY = -9999; }
    function onClick() { spawnWave(1.0, 'spark'); for (let k = 0; k < 4; k++) spawnSynapse(1); }

    // ── Main loop ────────────────────────────────────────────────────────
    function tick() {
        if (!mainCtx || !frameCtx) { raf = requestAnimationFrame(tick); return; }
        frame++;
        // Alias frameCtx as `ctx` so all the existing draw code below targets
        // the per-frame buffer (cleared each frame), not the visible canvas.
        // This is the load-bearing fix: previously, the bloom pass sampled
        // canvasEl (which carries trails from past frames) and re-deposited
        // with `lighter`, creating a positive feedback loop that saturated
        // every pixel to white in ~30 frames. Now bloom samples ONLY this
        // frame's content, so trails on canvasEl don't compound.
        const ctx = frameCtx;
        // Clear the per-frame buffer fully so nothing from frame N-1 leaks in
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);

        // Smooth palette interp
        curPrimary = lerpCol(curPrimary, palette.primary, 0.05);
        curAccent  = lerpCol(curAccent,  palette.accent,  0.05);
        curSpark   = lerpCol(curSpark,   palette.spark,   0.05);
        curBgPulse = lerp(curBgPulse, palette.bgPulse, 0.05);
        curSpeed   = lerp(curSpeed,   palette.speed, 0.05);
        curClear   = lerp(curClear,   palette.clear, 0.05);
        curGlow    = lerp(curGlow,    palette.glowAlpha, 0.05);
        curGlitch  = lerp(curGlitch,  palette.glitchProb, 0.05);
        const speedMul = curSpeed;

        // 1. Trail clear with state tint
        // 1. State-tinted plasma vignette — only on this-frame buffer.
        // The trail accumulation happens later when we composite onto mainCtx.
        ctx.globalCompositeOperation = 'source-over';
        const breath = (Math.sin(frame * 0.018) + 1) * 0.5;
        const vigGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, minDim * 0.95);
        vigGrad.addColorStop(0, rgba(curPrimary, 0.07 * curBgPulse * (0.6 + breath * 0.4)));
        vigGrad.addColorStop(0.4, rgba(curPrimary, 0.030 * curBgPulse));
        vigGrad.addColorStop(0.8, rgba(curAccent, 0.015 * curBgPulse));
        vigGrad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = vigGrad;
        ctx.fillRect(0, 0, W, H);

        // Energy decay
        const targetE = agentState === 'idle' ? 0.45
                      : agentState === 'thinking' ? 1.2
                      : agentState === 'executing' ? 1.4
                      : agentState === 'speaking' ? 1.6 : 1.7;
        energy += (targetE - energy) * 0.04;
        spin += 0.0014 * speedMul;

        // 2. Plexus mesh (additive)
        ctx.globalCompositeOperation = 'lighter';
        const maxDist = minDim * 0.30;
        for (const p of plexus) {
            // Mouse pull
            if (mouseX > -9000) {
                const dx = mouseX - p.x, dy = mouseY - p.y;
                const d = Math.hypot(dx, dy);
                if (d < 240) {
                    const f = (240 - d) / 240 * 0.05;
                    p.vx += (dx / d) * f;
                    p.vy += (dy / d) * f;
                }
            }
            p.vx *= 0.985; p.vy *= 0.985;
            p.x += p.vx * speedMul; p.y += p.vy * speedMul;
            if (p.x < 0 || p.x > W) p.vx *= -1;
            if (p.y < 0 || p.y > H) p.vy *= -1;
            p.phase += 0.012;
        }
        ctx.lineWidth = 0.55;
        for (let i = 0; i < plexus.length; i++) {
            const a = plexus[i];
            for (let j = i + 1; j < plexus.length; j++) {
                const b = plexus[j];
                const dx = b.x - a.x, dy = b.y - a.y;
                const d = Math.hypot(dx, dy);
                if (d < maxDist) {
                    const op = (1 - d / maxDist) * 0.30 * (0.6 + curBgPulse * 0.5);
                    ctx.strokeStyle = rgba(curPrimary, op);
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.stroke();
                }
            }
        }
        for (const p of plexus) {
            ctx.fillStyle = rgba(curPrimary, 0.5 + Math.sin(p.phase) * 0.3);
            ctx.beginPath();
            ctx.arc(p.x, p.y, 1.3, 0, Math.PI * 2);
            ctx.fill();
        }

        // 3. Sphere wireframe (3D meridians + parallels projected)
        const t = frame * 0.005 * speedMul;
        // Build projected positions
        const projected = sphereNodes.map(n => ({ ...n, ...projectSphere(n.lat, n.lon, t) }));
        const lonSteps = 24;
        // Connect along longitude (parallels)
        for (let li = 0; li < projected.length; li++) {
            const cur = projected[li];
            // Next on the same latitude (wrap)
            const sameLat = Math.floor(li / lonSteps) * lonSteps;
            const nxLon = sameLat + ((li + 1) % lonSteps);
            const nx = projected[nxLon];
            const op = 0.18 + cur.depth * 0.30;
            ctx.strokeStyle = rgba(curPrimary, op);
            ctx.lineWidth = 0.4;
            ctx.beginPath();
            ctx.moveTo(cur.px, cur.py);
            ctx.lineTo(nx.px, nx.py);
            ctx.stroke();
            // And along meridians (next latitude)
            const nxLat = li + lonSteps;
            if (nxLat < projected.length) {
                const ny = projected[nxLat];
                const op2 = 0.16 + ((cur.depth + ny.depth) / 2) * 0.30;
                ctx.strokeStyle = rgba(curPrimary, op2);
                ctx.beginPath();
                ctx.moveTo(cur.px, cur.py);
                ctx.lineTo(ny.px, ny.py);
                ctx.stroke();
            }
        }
        // Sphere nodes themselves
        for (const n of projected) {
            const sz = 0.5 + n.depth * 1.5;
            ctx.fillStyle = rgba(curPrimary, 0.3 + n.depth * 0.6);
            ctx.beginPath();
            ctx.arc(n.px, n.py, sz, 0, Math.PI * 2);
            ctx.fill();
        }

        // 4. Halo dust
        for (const d of dust) {
            d.angle += d.speed * speedMul;
            d.twinkle += 0.045;
            const x = cx + Math.cos(d.angle) * d.r;
            const y = cy + Math.sin(d.angle) * d.r;
            const tw = (Math.sin(d.twinkle) + 1) * 0.5;
            const col = d.warm ? curAccent : curPrimary;
            ctx.fillStyle = rgba(col, 0.18 + tw * 0.55);
            ctx.beginPath();
            ctx.arc(x, y, d.size, 0, Math.PI * 2);
            ctx.fill();
        }

        // 5. Equalizer bars around core
        const N_BARS = bars.length;
        const rIn = minDim * 0.155;
        const rOutMax = minDim * 0.205;
        for (let i = 0; i < N_BARS; i++) {
            const phase = i * 0.39;
            const wave = (Math.sin(frame * 0.06 + phase) * 0.5
                       + Math.sin(frame * 0.13 + phase * 1.7) * 0.3
                       + Math.sin(frame * 0.22 + phase * 2.3) * 0.2 + 1) * 0.5;
            bars[i] += (energy * wave - bars[i]) * 0.20;
            const v = Math.min(1.5, bars[i]);
            const a = (i / N_BARS) * Math.PI * 2 + spin * 0.4;
            const len = rIn + (rOutMax - rIn) * v;
            const x1 = cx + Math.cos(a) * rIn;
            const y1 = cy + Math.sin(a) * rIn;
            const x2 = cx + Math.cos(a) * len;
            const y2 = cy + Math.sin(a) * len;
            ctx.strokeStyle = rgba(curPrimary, 0.25 + v * 0.65);
            ctx.lineWidth = 1.7;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
        }

        // 6. Wave shockwaves
        for (let i = waves.length - 1; i >= 0; i--) {
            const w = waves[i];
            const age = (frame - w.bornAt) / 110;
            if (age >= 1) { waves.splice(i, 1); continue; }
            const r = minDim * 0.16 + minDim * 0.34 * age;
            const op = (1 - age) * 0.7 * w.strength;
            const col = w.hue === 'primary' ? curPrimary : w.hue === 'accent' ? curAccent : curSpark;
            ctx.strokeStyle = rgba(col, op);
            ctx.lineWidth = 1 + (1 - age) * 1.8;
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            ctx.stroke();
        }

        // 7. Synapse arcs — DISABLED (visual feedback was ugly).
        // We still keep the data array fed by spawnSynapse() so brain
        // spikes co-fire on pulse triggers, but skip drawing the lightning
        // bolts. The synapse list is drained periodically below.
        // (Drain so memory doesn't grow unbounded.)
        synapses = synapses.filter(s => (frame - s.bornAt) <= s.life);

        // 8. Brain spikes inside the void
        for (let i = brainSpikes.length - 1; i >= 0; i--) {
            const b = brainSpikes[i];
            const age = frame - b.bornAt;
            if (age > 16) { brainSpikes.splice(i, 1); continue; }
            const lt = 1 - age / 16;
            ctx.fillStyle = rgba(curSpark, 0.95 * lt);
            ctx.beginPath();
            ctx.arc(b.x, b.y, b.size, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = rgba(curPrimary, 0.45 * lt);
            ctx.beginPath();
            ctx.arc(b.x, b.y, b.size * 3, 0, Math.PI * 2);
            ctx.fill();
        }

        // ── (bloom + glitch moved to compositing stage at end of tick) ──

        // 10. Central halo + void
        const haloGrad = ctx.createRadialGradient(cx, cy, minDim * 0.10, cx, cy, minDim * 0.22);
        haloGrad.addColorStop(0, rgba(curPrimary, 0.60 * (0.6 + energy * 0.4)));
        haloGrad.addColorStop(1, rgba(curPrimary, 0));
        ctx.fillStyle = haloGrad;
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = '#000';
        ctx.beginPath();
        ctx.arc(cx, cy, minDim * 0.150, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = rgba(curPrimary, 0.95);
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(cx, cy, minDim * 0.150, 0, Math.PI * 2);
        ctx.stroke();
        ctx.strokeStyle = rgba(curPrimary, 0.25);
        ctx.lineWidth = 0.6;
        ctx.beginPath();
        ctx.arc(cx, cy, minDim * 0.130, 0, Math.PI * 2);
        ctx.stroke();

        // 11. Center label
        ctx.fillStyle = rgba(curPrimary, 0.70 + energy * 0.25);
        ctx.font = `300 ${Math.round(minDim * 0.055)}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, cx, cy - minDim * 0.005);
        ctx.fillStyle = `rgba(244, 244, 245, 0.40)`;
        ctx.font = `500 ${Math.round(minDim * 0.013)}px JetBrains Mono, monospace`;
        ctx.fillText(agentState.toUpperCase(), cx, cy + minDim * 0.045);

        // ─────────── COMPOSITE TO MAIN CANVAS ───────────
        // Trail-fade the visible canvas (controlled motion blur)
        mainCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
        mainCtx.globalCompositeOperation = 'source-over';
        mainCtx.fillStyle = `rgba(8, 9, 11, ${curClear})`;
        mainCtx.fillRect(0, 0, W, H);

        // Build bloom from THIS frame only — never from canvasEl, otherwise
        // we feedback-loop into white. frameBuf was cleared at start of tick.
        if (glowBuf && glowCtx && frameBuf) {
            glowCtx.globalCompositeOperation = 'source-over';
            glowCtx.clearRect(0, 0, glowBuf.width, glowBuf.height);
            glowCtx.filter = 'blur(9px)';
            glowCtx.drawImage(frameBuf, 0, 0, frameBuf.width, frameBuf.height,
                              0, 0, glowBuf.width, glowBuf.height);
            glowCtx.filter = 'none';
        }

        // Composite this frame's content onto main (crisp, source-over)
        if (frameBuf) {
            mainCtx.globalCompositeOperation = 'source-over';
            mainCtx.drawImage(frameBuf,
                0, 0, frameBuf.width, frameBuf.height,
                0, 0, W, H);
        }

        // Add bloom glow on top with `lighter`. Scaled down so even at the
        // brightest state palette it cannot drive the canvas to white.
        if (glowBuf) {
            mainCtx.globalCompositeOperation = 'lighter';
            mainCtx.globalAlpha = Math.min(0.55, curGlow * 0.55);
            mainCtx.drawImage(glowBuf, 0, 0, glowBuf.width, glowBuf.height, 0, 0, W, H);
            mainCtx.globalAlpha = 1;
            mainCtx.globalCompositeOperation = 'source-over';
        }

        // Glitch flash — applied directly on mainCtx, single-frame, no trail.
        // Sample frameBuf (this-frame content) so the displaced slices show
        // crisp recent content, not stale trails.
        if (glitchActive > 0 && frameBuf) {
            glitchActive--;
            const sliceCount = 4 + Math.floor(Math.random() * 6);
            for (let i = 0; i < sliceCount; i++) {
                const sy = Math.random() * H;
                const sh = 4 + Math.random() * 16;
                const dx = (Math.random() - 0.5) * 30;
                mainCtx.globalCompositeOperation = 'lighter';
                mainCtx.globalAlpha = 0.4;
                mainCtx.drawImage(
                    frameBuf,
                    0, sy * dpr, W * dpr, sh * dpr,
                    dx, sy, W, sh,
                );
            }
            mainCtx.globalAlpha = 1;
            mainCtx.globalCompositeOperation = 'source-over';
            mainCtx.fillStyle = rgba(curSpark, 0.05);
            const ySlice = Math.random() * H;
            mainCtx.fillRect(0, ySlice, W, 2);
        }

        ambientSpawn();
        raf = requestAnimationFrame(tick);
    }

    // ── Lifecycle ────────────────────────────────────────────────────────
    let ro: ResizeObserver | null = null;
    onMount(() => {
        if (typeof window === 'undefined' || !canvasEl) return;
        mainCtx = canvasEl.getContext('2d', { alpha: true });
        if (!mainCtx) return;
        resize();
        ro = new ResizeObserver(() => resize());
        if (containerEl) ro.observe(containerEl);
        raf = requestAnimationFrame(tick);
    });
    onDestroy(() => {
        if (typeof window !== 'undefined' && raf) cancelAnimationFrame(raf);
        ro?.disconnect();
    });
</script>

<div
    bind:this={containerEl}
    onmousemove={onMouseMove}
    onmouseleave={onMouseLeave}
    onclick={onClick}
    role="presentation"
    class="relative h-full w-full overflow-hidden cursor-crosshair"
>
    <canvas bind:this={canvasEl} class="absolute inset-0 block"></canvas>
</div>
