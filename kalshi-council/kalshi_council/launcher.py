"""Open Claude Code as one council agent, on your own Claude subscription (Pro or Max).

    kalshi-council claude line-shark

Claude Code gets only this agent's MCP server (so its calls are attributed to the right crew
member), the persona's standing instructions, and permission to use the council tools without a
prompt each time. The council tools research and forecast; none of them place orders.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from .personas import forecaster_prompt, persona_for

KICKOFF = ("Run a forecasting session on your beat: find markets where you have a real view, read their "
           "rules, research current information, and submit calibrated forecasts with the council tools.")

INSTALL_HELP = """Claude Code isn't installed (or isn't on your PATH). Install it, sign in with your
Claude account, then run this again:
  macOS / Linux:       curl -fsSL https://claude.ai/install.sh | bash
  Windows PowerShell:  irm https://claude.ai/install.ps1 | iex"""


def prepare(agent_id: str, engine_url: str, workdir: str | Path, *, personas_path: str | None = None,
            prompt: str | None = None, claude: str = "claude") -> list[str]:
    """Write the agent's MCP config and prompt files and return the ``claude`` command to run."""
    p = persona_for(agent_id, personas_path)
    d = Path(workdir)
    d.mkdir(parents=True, exist_ok=True)
    mcp_file = d / f"{p.id}.mcp.json"
    mcp_file.write_text(json.dumps({"mcpServers": {"council": {
        "command": sys.executable,
        "args": ["-m", "kalshi_council.cli", "mcp", "--agent", p.id, "--name", p.name,
                 "--model", "claude-code", "--engine-url", engine_url],
    }}}, indent=2))
    prompt_file = d / f"{p.id}.prompt.md"
    prompt_file.write_text(forecaster_prompt(p))
    # --allowedTools takes several values, so a single-value option must follow it before the prompt.
    return [claude, "--strict-mcp-config", "--mcp-config", str(mcp_file),
            "--append-system-prompt-file", str(prompt_file),
            "--allowedTools", "mcp__council",
            "--name", f"council-{p.id}",
            prompt or KICKOFF]


def run(agent_id: str, engine_url: str, *, personas_path: str | None = None, prompt: str | None = None,
        workdir: str | Path = "data/agents") -> int:
    claude = shutil.which("claude")
    if not claude:
        print(INSTALL_HELP)
        return 1
    try:
        httpx.get(engine_url.rstrip("/") + "/api/state", timeout=5).raise_for_status()
    except httpx.HTTPError:
        print(f"The council isn't running at {engine_url}. Start it first, in another terminal:\n"
              "  kalshi-council run")
        return 1
    cmd = prepare(agent_id, engine_url, workdir, personas_path=personas_path, prompt=prompt, claude=claude)
    print(f"Opening Claude Code as {persona_for(agent_id, personas_path).name}. Watch it on {engine_url}")
    return subprocess.call(cmd)
