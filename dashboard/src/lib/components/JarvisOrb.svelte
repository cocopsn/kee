<script lang="ts">
    /**
     * JarvisOrb — port of the orb visualizer from bertrandmbanwi/Jarvis.
     *
     * Three.js particle system: 2400 particles across 3 concentric shells,
     * with sinusoidal noise displacement, centripetal pull, soft additive
     * glow halo, and connection lines drawn ONLY in 'thinking' state.
     *
     * Lerp interpolation (0.04) on every state-driven param so transitions
     * are buttery rather than jumpy.
     *
     * Props:
     *   - state: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'
     *   - size: pixel diameter (default 260)
     */
    import { onMount, onDestroy } from 'svelte';
    import * as THREE from 'three';

    type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
    let { state = 'idle', size = 260 }: { state?: OrbState; size?: number } = $props();

    let canvasEl: HTMLCanvasElement;
    let raf: number;
    let cleanup: (() => void) | null = null;

    // Per-state visual targets (compactness, speed, brightness, color, glow)
    const STATES: Record<OrbState, {
        compactness: number; speed: number; brightness: number;
        color: [number, number, number]; glow: number; lines: boolean;
    }> = {
        idle:      { compactness: 0.85, speed: 0.005, brightness: 0.75,
                     color: [0.0, 0.832, 1.0],  glow: 0.45, lines: false },
        listening: { compactness: 0.92, speed: 0.015, brightness: 0.95,
                     color: [0.0, 0.832, 1.0],  glow: 0.55, lines: false },
        thinking:  { compactness: 1.00, speed: 0.035, brightness: 1.00,
                     color: [0.9, 0.95, 1.0],   glow: 0.70, lines: true  },
        speaking:  { compactness: 0.88, speed: 0.018, brightness: 0.95,
                     color: [1.0, 0.88, 0.55],  glow: 0.60, lines: false },
        error:     { compactness: 0.93, speed: 0.022, brightness: 1.00,
                     color: [1.0, 0.40, 0.85],  glow: 0.65, lines: false },
    };

    // Particle config — keep proportional to the original
    const SHELLS = [{ r: 6.6, n: 600 }, { r: 9.4, n: 1200 }, { r: 11.4, n: 600 }];
    const PARTICLE_COUNT = 2400;

    onMount(() => {
        // ── Three.js setup ─────────────────────────────────────────
        const renderer = new THREE.WebGLRenderer({ canvas: canvasEl, alpha: true, antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(size, size, false);
        renderer.setClearColor(0x000000, 0);

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
        camera.position.set(0, 0, 30);
        camera.lookAt(0, 0, 0);

        // ── Soft glow halo (radial-gradient sprite) ────────────────
        const glowCanvas = document.createElement('canvas');
        glowCanvas.width = 256; glowCanvas.height = 256;
        const gctx = glowCanvas.getContext('2d')!;
        const grad = gctx.createRadialGradient(128, 128, 0, 128, 128, 128);
        grad.addColorStop(0, 'rgba(255,255,255,0.95)');
        grad.addColorStop(0.3, 'rgba(255,255,255,0.45)');
        grad.addColorStop(1, 'rgba(255,255,255,0)');
        gctx.fillStyle = grad;
        gctx.beginPath(); gctx.arc(128, 128, 128, 0, Math.PI * 2); gctx.fill();
        const glowTex = new THREE.CanvasTexture(glowCanvas);
        const glowMat = new THREE.SpriteMaterial({
            map: glowTex, color: 0x00d4ff,
            transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending,
        });
        const glowSprite = new THREE.Sprite(glowMat);
        glowSprite.scale.set(22, 22, 1);
        scene.add(glowSprite);

        // ── Particle texture (soft circle) ─────────────────────────
        const ptCanvas = document.createElement('canvas');
        ptCanvas.width = 64; ptCanvas.height = 64;
        const pctx = ptCanvas.getContext('2d')!;
        const pgrad = pctx.createRadialGradient(32, 32, 0, 32, 32, 32);
        pgrad.addColorStop(0, 'rgba(255,255,255,1)');
        pgrad.addColorStop(0.5, 'rgba(255,255,255,0.5)');
        pgrad.addColorStop(1, 'rgba(255,255,255,0)');
        pctx.fillStyle = pgrad;
        pctx.beginPath(); pctx.arc(32, 32, 32, 0, Math.PI * 2); pctx.fill();
        const particleTex = new THREE.CanvasTexture(ptCanvas);

        // ── Particles: 3 shells, distributed on sphere surface ─────
        const particles: any[] = [];
        const positions = new Float32Array(PARTICLE_COUNT * 3);
        const colors = new Float32Array(PARTICLE_COUNT * 3);
        let idx = 0;
        for (let s = 0; s < SHELLS.length; s++) {
            const { r, n } = SHELLS[s];
            for (let k = 0; k < n && idx < PARTICLE_COUNT; k++, idx++) {
                // Uniform sphere distribution
                const phi = Math.acos(1 - 2 * Math.random());
                const theta = 2 * Math.PI * Math.random();
                const x = r * Math.sin(phi) * Math.cos(theta);
                const y = r * Math.sin(phi) * Math.sin(theta);
                const z = r * Math.cos(phi);
                particles.push({
                    x, y, z,
                    baseX: x, baseY: y, baseZ: z,
                    vx: 0, vy: 0, vz: 0,
                    shell: s,
                    orbitSpeed: 0.5 + Math.random() * 0.5,
                });
                positions[idx * 3] = x;
                positions[idx * 3 + 1] = y;
                positions[idx * 3 + 2] = z;
                colors[idx * 3] = 0;
                colors[idx * 3 + 1] = 0.832;
                colors[idx * 3 + 2] = 1.0;
            }
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        const material = new THREE.PointsMaterial({
            size: 0.18,
            map: particleTex,
            vertexColors: true,
            transparent: true,
            opacity: 0.95,
            blending: THREE.AdditiveBlending,
            depthWrite: false,
        });
        const points = new THREE.Points(geometry, material);
        scene.add(points);

        // ── Connection lines (only in 'thinking' state) ───────────
        const lineMat = new THREE.LineBasicMaterial({
            color: 0xffffff, transparent: true, opacity: 0.25,
            blending: THREE.AdditiveBlending,
        });
        const lineGeom = new THREE.BufferGeometry();
        const linePositions = new Float32Array(120 * 2 * 3);  // 120 line segments
        lineGeom.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
        const lines = new THREE.LineSegments(lineGeom, lineMat);
        lines.visible = false;
        scene.add(lines);

        // ── State interpolation ────────────────────────────────────
        let cur = { ...STATES.idle, color: [...STATES.idle.color] as [number, number, number] };
        let stateTime = 0;
        let breathePhase = 0;
        const LERP = 0.04;

        function frame() {
            const target = STATES[state] || STATES.idle;
            cur.compactness += (target.compactness - cur.compactness) * LERP;
            cur.speed += (target.speed - cur.speed) * LERP;
            cur.brightness += (target.brightness - cur.brightness) * LERP;
            cur.glow += (target.glow - cur.glow) * LERP;
            cur.color[0] += (target.color[0] - cur.color[0]) * LERP;
            cur.color[1] += (target.color[1] - cur.color[1]) * LERP;
            cur.color[2] += (target.color[2] - cur.color[2]) * LERP;
            stateTime += 1;
            breathePhase += 0.018;
            const breathe = Math.sin(breathePhase) * 0.03;

            const pos = (geometry.getAttribute('position') as THREE.BufferAttribute).array as Float32Array;
            const col = (geometry.getAttribute('color') as THREE.BufferAttribute).array as Float32Array;
            for (let i = 0; i < PARTICLE_COUNT; i++) {
                const p = particles[i];
                const t = stateTime * p.orbitSpeed * cur.speed * 8;
                const noiseX = Math.sin(t + p.baseX * 0.5) * 0.08;
                const noiseY = Math.cos(t * 0.7 + p.baseY * 0.5) * 0.08;
                const noiseZ = Math.sin(t * 0.9 + p.baseZ * 0.5) * 0.08;
                p.vx += noiseX * cur.speed * 2;
                p.vy += noiseY * cur.speed * 2;
                p.vz += noiseZ * cur.speed * 2;
                p.vx *= 0.92; p.vy *= 0.92; p.vz *= 0.92;
                p.x += p.vx; p.y += p.vy; p.z += p.vz;

                const targetR = SHELLS[p.shell].r * cur.compactness * (1 + breathe);
                const dist = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
                if (dist > 0.01) {
                    const sf = 1.0 + (targetR / dist - 1.0) * 0.04;
                    p.x *= sf; p.y *= sf; p.z *= sf;
                }
                pos[i * 3] = p.x; pos[i * 3 + 1] = p.y; pos[i * 3 + 2] = p.z;
                col[i * 3] = cur.color[0] * cur.brightness;
                col[i * 3 + 1] = cur.color[1] * cur.brightness;
                col[i * 3 + 2] = cur.color[2] * cur.brightness;
            }
            (geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
            (geometry.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true;

            // Glow tracking
            glowMat.opacity = cur.glow;
            glowMat.color.setRGB(cur.color[0], cur.color[1], cur.color[2]);
            const sc = 22 + Math.sin(breathePhase) * 2;
            glowSprite.scale.set(sc, sc, 1);

            // Connection lines (thinking only)
            if (target.lines) {
                lines.visible = true;
                const lp = linePositions;
                let lineIdx = 0;
                // Sample inner shell only (first 600 particles = shell 0)
                const checkN = Math.min(400, PARTICLE_COUNT);
                for (let i = 0; i < checkN && lineIdx < 120; i++) {
                    for (let j = i + 1; j < checkN && lineIdx < 120; j++) {
                        const a = particles[i]; const b = particles[j];
                        const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
                        const d2 = dx * dx + dy * dy + dz * dz;
                        if (d2 < 4) {  // close pairs
                            lp[lineIdx * 6] = a.x; lp[lineIdx * 6 + 1] = a.y; lp[lineIdx * 6 + 2] = a.z;
                            lp[lineIdx * 6 + 3] = b.x; lp[lineIdx * 6 + 4] = b.y; lp[lineIdx * 6 + 5] = b.z;
                            lineIdx++;
                        }
                    }
                }
                // Zero out unused
                for (let k = lineIdx * 6; k < linePositions.length; k++) lp[k] = 0;
                (lineGeom.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
            } else {
                lines.visible = false;
            }

            // Slow rotation
            points.rotation.y += 0.0008;

            renderer.render(scene, camera);
            raf = requestAnimationFrame(frame);
        }
        raf = requestAnimationFrame(frame);

        cleanup = () => {
            cancelAnimationFrame(raf);
            geometry.dispose();
            material.dispose();
            particleTex.dispose();
            glowMat.dispose();
            glowTex.dispose();
            lineGeom.dispose();
            lineMat.dispose();
            renderer.dispose();
        };
    });

    onDestroy(() => { if (cleanup) cleanup(); });
</script>

<canvas bind:this={canvasEl} width={size} height={size} class="orb-canvas"></canvas>

<style>
    .orb-canvas {
        display: block;
        background: transparent;
    }
</style>
