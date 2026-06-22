"""Tool: scaffold — bootstrap project skeletons in `D:/Kee/workspaces/<slug>/`.

Six built-in templates:
  - svelte         — SvelteKit + Tailwind 4 + TypeScript
  - next           — Next.js 14 + Tailwind + TypeScript (app router)
  - python_cli     — pyproject + click + ruff + pytest
  - python_api     — FastAPI + uvicorn + httpx
  - landing        — single index.html + Tailwind CDN (one-page sites)
  - docs           — mkdocs-material with starter config

Each template writes ~5-15 files. After scaffolding, the agent can
optionally hand off to `claude_code` for the real implementation —
that's the Jarvis pattern that 10x's new-project speed.

Risk: 1 (writes to disk under workspaces/, never overwrites existing).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool


WORKSPACES_DIR = settings.project_root / "workspaces"


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "project"


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _scaffold_python_cli(root: Path, name: str) -> list[str]:
    written: list[str] = []
    pkg = _slug(name).replace("-", "_")
    _write(root, "pyproject.toml", f"""\
[project]
name = "{_slug(name)}"
version = "0.1.0"
description = ""
requires-python = ">=3.12"
dependencies = ["click>=8.1", "rich>=13.7"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.7"]

[project.scripts]
{pkg} = "{pkg}.main:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["{pkg}"]
""")
    _write(root, f"{pkg}/__init__.py", '__version__ = "0.1.0"\n')
    _write(root, f"{pkg}/main.py", f"""\
\"\"\"{name} CLI entry-point.\"\"\"
import click
from rich.console import Console

console = Console()


@click.group()
def cli():
    \"\"\"{name}\"\"\"


@cli.command()
@click.argument("target")
def hello(target):
    console.print(f"Hello, [bold cyan]{{target}}[/]!")


if __name__ == "__main__":
    cli()
""")
    _write(root, "tests/test_smoke.py", f"""\
def test_import():
    import {pkg}
    assert {pkg}.__version__
""")
    _write(root, ".gitignore", ".venv/\n__pycache__/\n*.pyc\n.ruff_cache/\n.pytest_cache/\n.env\n")
    _write(root, "README.md", f"# {name}\n\nA Python CLI.\n\n## Setup\n\n```\npython -m venv .venv\n.venv\\Scripts\\activate\npip install -e .[dev]\n{pkg} hello world\n```\n")
    written = ["pyproject.toml", f"{pkg}/__init__.py", f"{pkg}/main.py",
               "tests/test_smoke.py", ".gitignore", "README.md"]
    return written


def _scaffold_python_api(root: Path, name: str) -> list[str]:
    written = []
    _write(root, "pyproject.toml", f"""\
[project]
name = "{_slug(name)}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "httpx>=0.27", "pydantic>=2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
""")
    _write(root, "app/__init__.py", "")
    _write(root, "app/main.py", """\
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="API")

class Echo(BaseModel):
    text: str

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/echo")
async def echo(body: Echo):
    return {"echoed": body.text}
""")
    _write(root, "Dockerfile", """\
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY app ./app
RUN pip install -e .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
""")
    _write(root, ".gitignore", ".venv/\n__pycache__/\n*.pyc\n.env\n")
    _write(root, "README.md", f"# {name}\n\nFastAPI service.\n\n```\nuvicorn app.main:app --reload\n```\n")
    return ["pyproject.toml", "app/__init__.py", "app/main.py",
            "Dockerfile", ".gitignore", "README.md"]


def _scaffold_landing(root: Path, name: str) -> list[str]:
    _write(root, "index.html", f"""<!doctype html>
<html lang="es" class="dark">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" />
    <style>body {{ font-family: 'Inter', system-ui, sans-serif; background: #0a0a0f; color: #f4f4f5; }}</style>
</head>
<body class="min-h-screen flex items-center justify-center">
    <main class="text-center max-w-2xl px-6">
        <h1 class="text-5xl font-light mb-4 tracking-tight">{name}</h1>
        <p class="text-lg text-zinc-400 leading-relaxed">A one-page landing site. Replace this paragraph with your pitch.</p>
        <a href="#" class="inline-block mt-8 px-6 py-3 bg-cyan-500 text-zinc-950 rounded-lg hover:bg-cyan-400 transition-colors">Get started</a>
    </main>
</body>
</html>
""")
    _write(root, "README.md", f"# {name}\n\nOne-page site. Open `index.html` in a browser, or deploy to Vercel/Netlify with no build step.\n")
    _write(root, ".gitignore", "node_modules/\n.DS_Store\n")
    return ["index.html", "README.md", ".gitignore"]


def _scaffold_docs(root: Path, name: str) -> list[str]:
    _write(root, "mkdocs.yml", f"""\
site_name: {name}
theme:
  name: material
  features: [content.code.copy, navigation.tabs]
  palette:
    - scheme: slate
      primary: cyan
      accent: amber
nav:
  - Home: index.md
  - Architecture: architecture.md
  - API: api.md
""")
    _write(root, "docs/index.md", f"# {name}\n\nDocumentation home.\n")
    _write(root, "docs/architecture.md", f"# Architecture\n\nDescribe the system here.\n")
    _write(root, "docs/api.md", f"# API\n\nReference here.\n")
    _write(root, ".gitignore", "site/\n")
    _write(root, "README.md", f"# {name} docs\n\n```\npip install mkdocs-material\nmkdocs serve\n```\n")
    return ["mkdocs.yml", "docs/index.md", "docs/architecture.md",
            "docs/api.md", ".gitignore", "README.md"]


def _scaffold_svelte(root: Path, name: str) -> list[str]:
    _write(root, "package.json", f"""{{
  "name": "{_slug(name)}",
  "version": "0.0.1",
  "private": true,
  "type": "module",
  "scripts": {{
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "devDependencies": {{
    "@sveltejs/adapter-static": "^3.0.0",
    "@sveltejs/kit": "^2.20.0",
    "@sveltejs/vite-plugin-svelte": "^5.0.0",
    "@tailwindcss/vite": "^4.0.0",
    "svelte": "^5.0.0",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.5.0",
    "vite": "^6.0.0"
  }}
}}
""")
    _write(root, "svelte.config.js", """\
import adapter from '@sveltejs/adapter-static';
const config = {
  kit: { adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html' }) },
};
export default config;
""")
    _write(root, "vite.config.ts", """\
import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({ plugins: [tailwindcss(), sveltekit()] });
""")
    _write(root, "src/app.html", """<!doctype html>
<html lang="es" class="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>App</title>
  %sveltekit.head%
</head>
<body class="bg-zinc-950 text-zinc-100">
  <div style="display:contents">%sveltekit.body%</div>
</body>
</html>""")
    _write(root, "src/app.css", "@import 'tailwindcss';\n")
    _write(root, "src/routes/+page.svelte", f"""<script>
  let n = $state(0);
</script>
<main class="min-h-screen flex items-center justify-center">
  <div class="text-center">
    <h1 class="text-5xl font-light mb-6">{name}</h1>
    <button onclick={{() => n++}} class="px-5 py-2 bg-cyan-500 text-zinc-950 rounded">Clicks: {{n}}</button>
  </div>
</main>
""")
    _write(root, ".gitignore", "node_modules/\nbuild/\n.svelte-kit/\ndist/\n.env\n")
    _write(root, "README.md", f"# {name}\n\n```\nnpm install\nnpm run dev\n```\n")
    return ["package.json", "svelte.config.js", "vite.config.ts",
            "src/app.html", "src/app.css", "src/routes/+page.svelte",
            ".gitignore", "README.md"]


def _scaffold_next(root: Path, name: str) -> list[str]:
    _write(root, "package.json", f"""{{
  "name": "{_slug(name)}",
  "version": "0.1.0",
  "private": true,
  "scripts": {{
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  }},
  "dependencies": {{
    "next": "^14.2.0",
    "react": "^18",
    "react-dom": "^18",
    "tailwindcss": "^3.4"
  }},
  "devDependencies": {{
    "@types/node": "^20",
    "@types/react": "^18",
    "typescript": "^5"
  }}
}}
""")
    _write(root, "tsconfig.json", '{"compilerOptions":{"target":"ES2020","lib":["dom","esnext"],"jsx":"preserve","strict":true,"esModuleInterop":true,"skipLibCheck":true,"baseUrl":".","paths":{"@/*":["./src/*"]}},"include":["next-env.d.ts","src/**/*","**/*.tsx","**/*.ts"]}\n')
    _write(root, "tailwind.config.ts", "import type {Config} from 'tailwindcss';\nconst c:Config={content:['./src/**/*.{ts,tsx}'],theme:{extend:{}},plugins:[]};\nexport default c;\n")
    _write(root, "src/app/layout.tsx", f"export const metadata = {{ title: '{name}' }};\nexport default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{\n  return <html lang='es'><body>{{children}}</body></html>;\n}}\n")
    _write(root, "src/app/page.tsx", f"export default function Home() {{\n  return <main className='min-h-screen flex items-center justify-center'><h1 className='text-5xl'>{name}</h1></main>;\n}}\n")
    _write(root, "src/app/globals.css", "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n")
    _write(root, ".gitignore", "node_modules/\n.next/\nout/\n.env\n")
    _write(root, "README.md", f"# {name}\n\n```\nnpm install\nnpm run dev\n```\n")
    return ["package.json", "tsconfig.json", "tailwind.config.ts",
            "src/app/layout.tsx", "src/app/page.tsx", "src/app/globals.css",
            ".gitignore", "README.md"]


_TEMPLATES = {
    "python_cli": _scaffold_python_cli,
    "python_api": _scaffold_python_api,
    "landing":    _scaffold_landing,
    "docs":       _scaffold_docs,
    "svelte":     _scaffold_svelte,
    "next":       _scaffold_next,
}


class ScaffoldTool(Tool):
    name = "scaffold"
    description = (
        "Bootstrap a new project skeleton in D:/Kee/workspaces/<slug>/. "
        "Six templates: svelte, next, python_cli, python_api, landing, docs. "
        "Refuses to overwrite an existing directory. After scaffolding, "
        "you can hand off the workspace path to `claude_code` for the real "
        "implementation."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "enum": list(_TEMPLATES.keys()),
            },
            "name": {"type": "string", "description": "Project name (becomes folder slug)"},
        },
        "required": ["template", "name"],
    }

    async def execute(self, template: str, name: str) -> dict[str, Any]:
        if template not in _TEMPLATES:
            return {"ok": False, "error": f"unknown template '{template}'",
                    "available": list(_TEMPLATES.keys())}
        slug = _slug(name)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = WORKSPACES_DIR / f"{ts}-{slug}-{template}"
        if root.exists():
            return {"ok": False, "error": f"workspace already exists: {root}"}
        root.mkdir(parents=True)
        try:
            written = _TEMPLATES[template](root, name)
        except Exception as e:
            return {"ok": False, "error": f"scaffold failed: {e}", "path": str(root)}
        return {
            "ok": True, "template": template, "name": name,
            "workspace": str(root), "files": written,
            "next_step": (
                f"Hand off to claude_code with cwd='{root}' and a task like "
                f"'implementa la lógica core de {name}'."
            ),
        }


tool = ScaffoldTool()
