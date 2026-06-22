"""Tool: keecode - Kee's OpenCode-backed coding-agent bridge."""

from __future__ import annotations

from typing import Any

from kee.integrations import keecode as bridge
from kee.tools.base import Tool


class KeeCodeTool(Tool):
    name = "keecode"
    description = (
        "Launch and control KeeCode, Kee's clean-room coding-agent surface "
        "backed by OpenCode and the current local Ollama model. Use this "
        "when the user wants a Claude-Code-like workflow without proprietary "
        "or leaked source: open an interactive terminal, run a one-shot code "
        "prompt, sync continuity notes between Kee chat and KeeCode, or check "
        "the OpenCode/Ollama configuration status."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "launch", "prompt", "sync_context", "write_config"],
                "default": "status",
            },
            "prompt": {
                "type": "string",
                "description": "Task prompt or continuity note for KeeCode.",
            },
            "workdir": {
                "type": "string",
                "description": "Project directory for OpenCode. Defaults to D:/Kee.",
            },
            "session_id": {
                "type": "string",
                "description": "Kee chat/session id used in the shared context bridge.",
                "default": "dashboard",
            },
            "model": {
                "type": "string",
                "description": "Override coding model. Defaults to KEE_CODE_AGENT_MODEL or KEE_MODEL.",
            },
            "timeout_s": {
                "type": "integer",
                "default": 600,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        action: str = "status",
        prompt: str = "",
        workdir: str = "",
        session_id: str = "dashboard",
        model: str = "",
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        if action == "status":
            return bridge.status()

        if action == "write_config":
            path = bridge.write_opencode_config(model=model or None)
            return {"ok": True, "path": str(path), **bridge.status()}

        if action == "sync_context":
            path = bridge.write_context_bridge(
                notes=prompt,
                session_id=session_id,
                workdir=None,
            )
            return {"ok": True, "context_path": str(path), **bridge.status()}

        if action == "launch":
            return bridge.launch_terminal(
                workdir=workdir or None,
                prompt=prompt,
                model=model or None,
            )

        if action == "prompt":
            if not prompt.strip():
                return {"ok": False, "error": "prompt required"}
            return await bridge.run_prompt(
                prompt=prompt,
                workdir=workdir or None,
                model=model or None,
                timeout_s=timeout_s,
            )

        return {"ok": False, "error": f"unknown action: {action}"}


tool = KeeCodeTool()
