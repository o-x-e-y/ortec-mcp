import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx

from . import actions, dates, render
from .errors import OrtecError
from .session import Session

EPILOG = """\
dates: YYYY-MM-DD, MM-DD or DD (the next such date), today/vandaag,
tomorrow/morgen, or +N days. A time is 'DATE HH:MM'.

Writes (swap-request, swap-accept, swap-decline, leave) only print the request
they would send. Add --yes to send it.

examples:
  ortec shifts                                  next two weeks
  ortec shifts tomorrow                         just tomorrow
  ortec swaps                                   swap requests, next month
  ortec swap-candidates 2026-10-28              who can swap my shift that day
  ortec swap-candidates tomorrow --give-away    who can take it over
  ortec swap-request 2026-10-28 tess            swap for Tess's shift that day
  ortec swap-request 2026-10-28 tess --their-date 2026-10-30
  ortec swap-request 2026-10-28 12345678 --give-away -m "kan niet"
  ortec swap-accept 2026-10-28                  the open incoming request that day
  ortec working-with                            who works with you, 7 days from tomorrow
  ortec working-with 28                         ... on the 28th (only days you work)
  ortec leave 2026-11-10                        one whole day
  ortec leave 2026-11-10 2026-11-12             three whole days
  ortec leave "2026-11-10 14:00" 18:00          part of a day

setup: $ORTEC_COMPANY is the company code you enter in the ORTEC app (e.g.
ACME1). Credentials: $ORTEC_USER (e-mail) and $ORTEC_PASS, or $ORTEC_USER_FILE
and $ORTEC_PASS_FILE. The session is kept in ~/.cache/ortec/session.json.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ortec", description="ORTEC Employee Self Service: shifts, swaps and leave.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def command(name, help_):
        return sub.add_parser(name, help=help_, description=help_)

    def span(p, days):
        p.add_argument("start", nargs="?", metavar="FROM",
                       help=f"default: the next {days} days; FROM alone is that one day")
        p.add_argument("end", nargs="?", metavar="TO")
        p.set_defaults(days=days)

    def confirm(p):
        p.add_argument("--yes", action="store_true", help="actually send it")

    command("login", "log in afresh and show who you are")
    span(command("shifts", "your shifts"), actions.DEFAULT_DAYS["shifts"])
    span(command("swaps", "swap requests, incoming and outgoing"), actions.DEFAULT_DAYS["swaps"])
    span(command("leaves", "your leave requests"), actions.DEFAULT_DAYS["leaves"])
    command("leave-types", "the leave types you can request")

    p = command("working-with", "who works with you, on days you work")
    span(p, actions.DEFAULT_DAYS["working-with"])
    p.add_argument("--json", action="store_true", help="one calendar entry per day, as JSON")
    p.add_argument("--previous", metavar="FILE",
                   help="JSON object of date -> an earlier --json description; colleagues "
                        "whose time slot has vanished since are kept")

    p = command("swap-candidates", "who you can swap a shift with (or give it to)")
    p.add_argument("date")
    p.add_argument("--give-away", action="store_true", help="who can take it over instead")
    p.add_argument("--their-date", help="look for their shifts on another day")
    p.add_argument("--shift", help="your shift id or start time, if you have several")

    p = command("swap-request", "ask a colleague to swap (or take over) your shift")
    p.add_argument("date", help="the date of your shift")
    p.add_argument("colleague", help="colleague id or part of their name")
    p.add_argument("--give-away", action="store_true", help="give the shift away, get none back")
    p.add_argument("--their-date", help="their shift is on another day")
    p.add_argument("--their-shift", help="their shift id or start time, if they have several")
    p.add_argument("--shift", help="your shift id or start time, if you have several")
    p.add_argument("-m", "--remark", default="")
    confirm(p)

    for name, verb in (("swap-accept", "accept"), ("swap-decline", "decline")):
        p = command(name, f"{verb} an incoming swap request")
        p.add_argument("date", nargs="?", help="a date either shift of the request is on")
        p.add_argument("--id", type=int, help="the request id, if several match")
        confirm(p)

    p = command("leave", "request time off")
    p.add_argument("start", metavar="FROM", help="DATE, or 'DATE HH:MM' for part of a day")
    p.add_argument("end", nargs="?", metavar="TO",
                   help="last whole day, or 'DATE HH:MM' / HH:MM for part of a day")
    p.add_argument("--type", dest="leave_type", help="leave type name or id (default: the first)")
    p.add_argument("-m", "--remark", default="")
    confirm(p)
    return parser


def _read_previous(path: str) -> dict[str, str]:
    try:
        previous = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise OrtecError(f"--previous {path}: {error}") from None
    if not isinstance(previous, dict):
        raise OrtecError(f"--previous {path}: expected a JSON object of date -> description")
    return previous


def run(args, session: Session) -> str:
    match args.command:
        case "login":
            session.forget()
            return (f"Logged in. Employee id {session.employee_id}, "
                    f"contracts {session.contract_ids}.")
        case "shifts":
            start, end = dates.span(args.start, args.end, args.days)
            return render.shifts(actions.shifts(session, start, end), start, end)
        case "swaps":
            start, end = dates.span(args.start, args.end, args.days)
            return render.swap_requests(actions.swap_requests(session, start, end), start, end)
        case "leaves":
            start, end = dates.span(args.start, args.end, args.days)
            return render.leaves(actions.leaves(session, start, end), start, end)
        case "leave-types":
            return render.leave_types(actions.leave_types(session))
        case "working-with":
            start, end = dates.span(args.start, args.end, args.days, first=dates.tomorrow())
            found = actions.working_with(session, start, end)
            if args.previous:
                found = actions.carry_over(found, _read_previous(args.previous))
            if args.json:
                return render.working_with_json(found, dt.datetime.now(dates.TZ))
            return render.working_with(found, start, end)
        case "swap-candidates":
            day = dates.parse_date(args.date)
            their_day = dates.parse_date(args.their_date) if args.their_date else None
            found = actions.swap_candidates(session, day, give_away=args.give_away,
                                            their_day=their_day, shift=args.shift)
            return render.swap_candidates(found, day, args.give_away)
        case "swap-request":
            return render.write(actions.request_swap(
                session, dates.parse_date(args.date), args.colleague, shift=args.shift,
                their_shift=args.their_shift,
                their_day=dates.parse_date(args.their_date) if args.their_date else None,
                give_away=args.give_away, remark=args.remark, confirm=args.yes), "run it again with --yes")
        case "swap-accept" | "swap-decline":
            return render.write(actions.respond_swap(
                session, dates.parse_date(args.date) if args.date else None,
                request_id=args.id, decline=args.command == "swap-decline", confirm=args.yes), "run it again with --yes")
        case "leave":
            return render.write(actions.request_leave(
                session, args.start, args.end, leave_type=args.leave_type, remark=args.remark,
                confirm=args.yes), "run it again with --yes")


def main(argv=None, session: Session | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(run(args, session or Session()))
    except OrtecError as error:
        print(f"ortec: {error}", file=sys.stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"ortec: network error: {error}", file=sys.stderr)
        return 1
    return 0
