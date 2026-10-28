"""MCP server: the CLI's operations as tools, over stdio or streamable HTTP."""

import argparse
import functools
import os
from typing import Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import actions, dates, render
from .errors import OrtecError
from .session import Session

INSTRUCTIONS = """\
Shifts, shift swaps and leave (time off) for one employee in ORTEC Employee
Self Service. Dates are YYYY-MM-DD, `today`, `tomorrow` or `+N` days, in
Europe/Amsterdam; work out the date yourself for words like "Friday" before
calling.

Tool output is Markdown written for the user: show it as-is.

request_swap, respond_to_swap and request_leave change the real schedule. Call
them first without `confirm`: that sends nothing and returns a preview. Show the
preview to the user and call again with `confirm: true` only after they have
said yes to that exact preview. Never confirm on your own initiative.

To swap a shift: swap_candidates for that date, then request_swap with the
colleague's name or id. To answer "do I have swap requests": swap_requests.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                        openWorldHint=True)

server = MCPServer("ortec", instructions=INSTRUCTIONS)


CONFIRM_HINT = "call again with `confirm: true`, once the user has agreed to this preview"


@functools.cache
def session() -> Session:
    return Session()


def tool(annotations: ToolAnnotations):
    def register(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except OrtecError as error:
                raise ToolError(str(error)) from error
            except httpx.HTTPError as error:
                raise ToolError(f"network error: {error}") from error

        return server.tool(annotations=annotations)(wrapper)

    return register


@tool(READ)
def shifts(start: str | None = None, end: str | None = None) -> str:
    """The user's own shifts from `start` to `end` inclusive. Only `start`: that
    day. Neither: the next two weeks."""
    first, last = dates.span(start, end, actions.DEFAULT_DAYS["shifts"])
    return render.shifts(actions.shifts(session(), first, last), first, last)


@tool(READ)
def swap_requests(start: str | None = None, end: str | None = None) -> str:
    """Swap requests, incoming and outgoing, with either shift between `start`
    and `end` inclusive. Only `start`: that day. Neither: the next month."""
    first, last = dates.span(start, end, actions.DEFAULT_DAYS["swaps"])
    return render.swap_requests(actions.swap_requests(session(), first, last), first, last)


@tool(READ)
def swap_candidates(date: str, give_away: bool = False, their_date: str | None = None,
                    shift: str | None = None) -> str:
    """Who can swap with the user's shift on `date`, and which of their shifts.

    give_away: list who can take the shift over instead (the user gets no shift back).
    their_date: look for the colleagues' shifts on another day.
    shift: the user's shift id or start time (HH:MM), only if they have several that day.
    """
    day = dates.parse_date(date)
    found = actions.swap_candidates(
        session(), day, give_away=give_away,
        their_day=dates.parse_date(their_date) if their_date else None, shift=shift)
    return render.swap_candidates(found, day, give_away)


@tool(WRITE)
def request_swap(date: str, colleague: str, give_away: bool = False,
                 their_date: str | None = None, their_shift: str | None = None,
                 shift: str | None = None, remark: str = "", confirm: bool = False) -> str:
    """Ask a colleague to swap the user's shift on `date` (or take it over).

    colleague: their name (or part of it) or employee id, as swap_candidates lists them.
    give_away: hand the shift over without getting one back.
    their_date: the colleague's shift is on another day.
    their_shift: the colleague's shift id or start time, if they have several.
    shift: the user's shift id or start time, if they have several that day.
    confirm: false returns a preview and sends nothing; true sends it.
    """
    return render.write(actions.request_swap(
        session(), dates.parse_date(date), colleague, shift=shift, their_shift=their_shift,
        their_day=dates.parse_date(their_date) if their_date else None,
        give_away=give_away, remark=remark, confirm=confirm), CONFIRM_HINT)


@tool(WRITE)
def respond_to_swap(date: str | None = None, request_id: int | None = None,
                    decision: Literal["accept", "decline"] = "accept",
                    confirm: bool = False) -> str:
    """Accept or decline an open swap request a colleague sent the user.

    date: a date either shift of the request is on. request_id: the request's id
    (from swap_requests); needed when several requests match the date.
    confirm: false returns a preview and sends nothing; true sends it.
    """
    return render.write(actions.respond_swap(
        session(), dates.parse_date(date) if date else None, request_id=request_id,
        decline=decision == "decline", confirm=confirm), CONFIRM_HINT)


@tool(READ)
def working_with(start: str | None = None, end: str | None = None) -> str:
    """Who the user works with, only on days they have a shift. Only `start`: that
    day. Neither: the 7 days from tomorrow. Shows the colleagues the swap screen
    lists for the user's own shift, so it can miss people; refuses days the user is
    off, and today, whose shifts can no longer be swapped."""
    first, last = dates.span(start, end, actions.DEFAULT_DAYS["working-with"],
                             first=dates.tomorrow())
    return render.working_with(actions.working_with(session(), first, last), first, last)


@tool(READ)
def leave_types() -> str:
    """The leave types the user can request."""
    return render.leave_types(actions.leave_types(session()))


@tool(READ)
def leave_requests(start: str | None = None, end: str | None = None) -> str:
    """The user's leave requests and their status, from `start` to `end` inclusive.
    Only `start`: that day. Neither: the next 90 days."""
    first, last = dates.span(start, end, actions.DEFAULT_DAYS["leaves"])
    return render.leaves(actions.leaves(session(), first, last), first, last)


@tool(WRITE)
def request_leave(start: str, end: str | None = None, leave_type: str | None = None,
                  remark: str = "", confirm: bool = False) -> str:
    """Request time off.

    Whole days: start = first day, end = last day (omit for one day).
    Part of a day: start = "YYYY-MM-DD HH:MM", end = "HH:MM" (or "YYYY-MM-DD HH:MM").
    leave_type: name or id from leave_types; default is the first type (usually Vakantie).
    confirm: false returns a preview and sends nothing; true checks the balance and sends it.
    """
    return render.write(actions.request_leave(
        session(), start, end, leave_type=leave_type, remark=remark, confirm=confirm), CONFIRM_HINT)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ortec-mcp", description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default=os.environ.get("ORTEC_MCP_PATH", "/mcp"),
                        help="URL path; make it secret when exposed ($ORTEC_MCP_PATH)")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="public Host header to accept, e.g. behind a tunnel (repeatable)")
    args = parser.parse_args(argv)
    if not args.http:
        server.run()
        return
    security = TransportSecuritySettings(
        allowed_hosts=[f"{args.host}:{args.port}", f"localhost:{args.port}", *args.allowed_host])
    server.run("streamable-http", host=args.host, port=args.port,
               streamable_http_path=args.path, transport_security=security)
