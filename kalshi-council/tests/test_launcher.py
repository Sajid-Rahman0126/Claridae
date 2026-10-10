import json
import sys

from kalshi_council import launcher


def test_prepare_wires_one_agent_with_its_persona(tmp_path):
    cmd = launcher.prepare("line-shark", "http://127.0.0.1:8787", tmp_path, claude="/bin/claude")
    mcp = json.loads((tmp_path / "line-shark.mcp.json").read_text())
    server = mcp["mcpServers"]["council"]
    assert server["command"] == sys.executable
    assert server["args"][server["args"].index("--agent") + 1] == "line-shark"
    assert "http://127.0.0.1:8787" in server["args"]
    prompt = (tmp_path / "line-shark.prompt.md").read_text()
    assert prompt.startswith("You are Line Shark") and "Sports" in prompt

    assert cmd[0] == "/bin/claude"
    assert cmd[cmd.index("--mcp-config") + 1] == str(tmp_path / "line-shark.mcp.json")
    assert "--strict-mcp-config" in cmd
    # the variadic --allowedTools must not swallow the kickoff prompt
    allowed = cmd.index("--allowedTools")
    assert cmd[allowed + 1] == "mcp__council" and cmd[allowed + 2].startswith("--")
    assert cmd[-1] == launcher.KICKOFF


def test_unknown_agent_becomes_a_generalist(tmp_path):
    cmd = launcher.prepare("my-bot", "http://x", tmp_path, prompt="Forecast the Fed decision")
    assert (tmp_path / "my-bot.prompt.md").read_text().startswith("You are my-bot")
    assert cmd[-1] == "Forecast the Fed decision"


def test_run_explains_missing_claude(monkeypatch, capsys):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.run("line-shark", "http://127.0.0.1:1") == 1
    assert "install.sh" in capsys.readouterr().out


def test_run_needs_the_dashboard(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/bin/claude")
    called = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    assert launcher.run("line-shark", "http://127.0.0.1:1", workdir=tmp_path) == 1
    assert "kalshi-council run" in capsys.readouterr().out and not called
