import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
    kit: {
        // Static adapter so the dashboard ships as a folder of files —
        // future Tauri wrapping just copies `build/` into the bundle.
        adapter: adapter({
            pages: 'build',
            assets: 'build',
            fallback: 'index.html',  // SPA mode — all routes resolve client-side
            precompress: false,
            strict: true
        }),
        // The API mounts the built dashboard at /app, so every asset and
        // route URL needs the /app prefix baked in. Without this the HTML
        // references /_app/immutable/... which 404s under our mount.
        paths: {
            base: '/app',
        },
        // The API URL is hard-wired to the local backend; configurable via
        // VITE_KEE_API in `.env` if you proxy elsewhere.
    }
};

export default config;
