import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ortec import cli, server


def test_cli_write_is_a_preview_without_yes(fake, capsys):
    assert cli.main(["swap-accept", "2026-10-28"], session=fake) == 0
    out = capsys.readouterr().out
    assert "Not sent" in out and "--yes" in out
    assert fake.posts() == []


def test_cli_write_sends_with_yes(fake, capsys):
    assert cli.main(["swap-accept", "2026-10-28", "--yes"], session=fake) == 0
    assert "# Sent" in capsys.readouterr().out
    assert fake.posts() == [("POST", "/api/requests/swaps/accept", {"id": 5001})]


def test_cli_errors_go_to_stderr(fake, capsys):
    assert cli.main(["swap-request", "2026-10-28", "zed"], session=fake) == 1
    assert "no colleague matching 'zed'" in capsys.readouterr().err


def test_cli_lists_incoming_and_outgoing(fake, capsys):
    cli.main(["swaps", "2026-10-28", "2026-11-02"], session=fake)
    out = capsys.readouterr().out
    assert out.index("## Incoming") < out.index("5001") < out.index("## Outgoing")
    assert out.index("## Outgoing") < out.index("5002")


@pytest.fixture
def tools(fake, monkeypatch):
    monkeypatch.setattr(server, "session", lambda: fake)

    def call(name, **arguments):
        result = asyncio.run(server.server.call_tool(name, arguments))
        return result.content[0].text

    return call


def test_mcp_writes_default_to_a_preview(tools, fake):
    out = tools("respond_to_swap", date="2026-10-28")
    assert "Not sent" in out and "confirm: true" in out
    assert fake.posts() == []
    tools("respond_to_swap", date="2026-10-28", decision="decline", confirm=True)
    assert fake.posts() == [("POST", "/api/requests/swaps/decline", {"id": 5001})]


def test_mcp_errors_reach_the_model(tools):
    with pytest.raises(ToolError, match="bad date 'someday'"):
        tools("shifts", start="someday")


def test_mcp_write_tools_are_marked_destructive():
    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    writes = {name for name, t in tools.items() if not t.annotations.read_only_hint}
    assert writes == {"request_swap", "respond_to_swap", "request_leave"}
    assert all(tools[name].annotations.destructive_hint for name in writes)


def test_cli_rejects_an_unreadable_previous_file(fake, capsys, tmp_path, monkeypatch):
    from ortec import dates

    monkeypatch.setattr(dates, "today", lambda: dates.dt.date(2026, 10, 27))
    (tmp_path / "prev.json").write_text("[]")
    assert cli.main(["working-with", "2026-10-28", "--previous", str(tmp_path / "prev.json")],
                    session=fake) == 1
    assert "expected a JSON object" in capsys.readouterr().err
    assert cli.main(["working-with", "2026-10-28", "--previous", str(tmp_path / "nope")],
                    session=fake) == 1
