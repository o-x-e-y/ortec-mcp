"""The operations, as plain functions over a Session.

Writes never send unless called with `confirm=True`; otherwise they return the
request they would have sent, so a caller can show it and ask first.
"""

import datetime as dt
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from . import dates, render
from .errors import OrtecError
from .session import Session

DEFAULT_DAYS = {"shifts": 13, "swaps": 30, "leaves": 90, "working-with": 6}


@dataclass(frozen=True)
class Person:
    id: int
    name: str

    @classmethod
    def parse(cls, raw: dict) -> "Person":
        return cls(raw["Id"], display_name(raw.get("FirstName"), raw.get("LastName")))


@dataclass(frozen=True)
class Shift:
    id: int
    name: str
    begin: dt.datetime
    end: dt.datetime

    @classmethod
    def parse(cls, raw: dict | None) -> "Shift | None":
        if not raw:
            return None
        name = (raw.get("Name") or (raw.get("Duty") or {}).get("Name") or "?").strip()
        begin = raw.get("BeginDateTime") or raw["BeginDate"]
        end = raw.get("EndDateTime") or raw["EndDate"]
        return cls(raw["Id"], name, dates.local(begin), dates.local(end))


@dataclass(frozen=True)
class SwapRequest:
    id: int
    status: str
    incoming: bool
    requester: Person
    colleague: Person
    offered: Shift | None
    wanted: Shift | None
    remark: str

    def touches(self, day: dt.date) -> bool:
        return any(s and s.begin.date() == day for s in (self.offered, self.wanted))


@dataclass(frozen=True)
class Candidate:
    person: Person
    shift: Shift | None


@dataclass(frozen=True)
class Seen:
    """A colleague listed by an earlier run, in a time slot that is gone now."""

    start: str
    end: str
    duty: str
    name: str
    last_seen: dt.date

    @property
    def slot(self) -> tuple[str, str, str]:
        return self.start, self.end, self.duty


@dataclass(frozen=True)
class WorkingDay:
    day: dt.date
    mine: list[Shift]
    colleagues: list[Candidate]
    incomplete: bool
    kept: list[Seen] = field(default_factory=list)


@dataclass
class Write:
    """A write, either sent or only described."""

    summary: list[str]
    method: str
    path: str
    body: dict
    sent: bool = False
    result: Any = None
    after: list[str] = field(default_factory=list)


def display_name(first: str | None, last: str | None) -> str:
    """`Linden, T.M.H. van der` + `Tess` -> `Tess van der Linden`."""
    surname, _, rest = (last or "").partition(",")
    prefix = [word for word in rest.split() if word.islower()]
    return " ".join(part for part in [first, *prefix, surname.strip()] if part) or "?"


def shifts(s: Session, start: dt.date, end: dt.date | None = None) -> list[Shift]:
    end = end or start
    raw = s.get("/api/shifts", fromDate=start.isoformat(),
                untilDate=(end + dt.timedelta(days=1)).isoformat(), resourceId=s.employee_id)
    found = [Shift.parse(r) for r in raw]
    return sorted((x for x in found if start <= x.begin.date() <= end), key=lambda x: x.begin)


def swap_requests(s: Session, start: dt.date, end: dt.date | None = None) -> list[SwapRequest]:
    end = end or start
    # The server matches either shift's date, and untilDate is exclusive.
    raw = s.get(f"/api/requests/{s.employee_id}/swaps", fromDate=start.isoformat(),
                untilDate=(end + dt.timedelta(days=1)).isoformat())
    mine = set(s.contract_ids)
    requests = [
        SwapRequest(
            id=r["Id"],
            status=r["Status"],
            incoming=r["RequestEmployee"]["Id"] not in mine,
            requester=Person.parse(r["RequestEmployee"]),
            colleague=Person.parse(r["ColleagueEmployee"]),
            offered=Shift.parse(r.get("ShiftToSwap")),
            wanted=Shift.parse(r.get("ShiftWanted")),
            remark=r.get("RequestRemark") or "",
        )
        for r in raw
    ]
    return sorted(requests, key=lambda r: min(x.begin for x in (r.offered, r.wanted) if x))


def swap_candidates(s: Session, day: dt.date, *, give_away: bool = False,
                    their_day: dt.date | None = None,
                    shift: str | None = None) -> list[tuple[Shift, list[Candidate]]]:
    mine = shifts(s, day)
    if shift:
        mine = [_pick_shift(mine, shift, day)]
    return [(m, _candidates(s, m, give_away, their_day)) for m in mine]


def _candidates(s: Session, mine: Shift, give_away: bool,
                their_day: dt.date | None) -> list[Candidate]:
    params = {"type": "Reassignment" if give_away else "Swap"}
    if their_day and their_day != mine.begin.date():
        params["date"] = their_day.isoformat()
    groups = s.get(f"/api/shifts/{mine.id}/swap-candidates", **params)
    found = [
        Candidate(Person.parse(employee),
                  None if group.get("IsReassignment")
                  else Shift.parse(employee.get("Shift") or group))
        for group in groups
        for employee in group["Employees"]
    ]
    return sorted(found, key=lambda c: (c.shift.begin if c.shift else mine.begin, c.person.name))


def working_with(s: Session, start: dt.date, end: dt.date | None = None, *,
                 pause: float = 0.5) -> list[WorkingDay]:
    """Who works on the days the user works, as far as the swap screen shows it.

    Only days the user has a shift on: the candidates for their own shift are
    colleagues working that day, filtered by both sides' availability. Days off
    are deliberately not looked at. An outgoing swap request on a shift that is
    still open or awaiting approval shrinks its list, so that day is marked
    incomplete.
    """
    end = end or start
    if start <= dates.today():
        raise OrtecError("working-with only looks at tomorrow and later: from midnight "
                         "a day's shifts are realised and can no longer be swapped")
    mine = shifts(s, start, end)
    if not mine:
        raise OrtecError(f"you have no shift on {render.span(start, end)}; "
                         "working-with only looks at days you work")
    pending = {r.offered.id for r in swap_requests(s, start, end)
               if not r.incoming and r.status in ("Open", "Processing") and r.offered}
    found, calls = [], 0
    for day in sorted({m.begin.date() for m in mine}):
        today_mine = [m for m in mine if m.begin.date() == day]
        seen = {}
        for shift in today_mine:
            if calls:
                time.sleep(pause)
            calls += 1
            for c in _candidates(s, shift, False, None):
                if c.shift and c.shift.begin.date() == day:
                    seen[(c.person.id, c.shift.id)] = c
        colleagues = sorted(seen.values(), key=lambda c: (c.shift.begin, c.person.name))
        incomplete = any(m.id in pending for m in today_mine)
        found.append(WorkingDay(day, today_mine, colleagues, incomplete))
    return found


def carry_over(found: list[WorkingDay], previous: dict[str, str]) -> list[WorkingDay]:
    """Merges in what an earlier run wrote, by time slot (duty, start, end).

    A slot that still has someone keeps only today's names: that is a swap. A
    slot that has vanished keeps its earlier names, because people are rarely
    taken off a shift; usually the swap screen just stopped listing them, as it
    does while a swap request is pending. The user's own slots are never kept.
    """
    merged = []
    for d in found:
        text = previous.get(d.day.isoformat())
        if not text:
            merged.append(d)
            continue
        current = {_slot(c.shift) for c in d.colleagues} | {_slot(m) for m in d.mine}
        kept = [Seen(*entry) for entry in render.read_working_with(text)]
        merged.append(replace(d, kept=[k for k in kept if k.slot not in current]))
    return merged


def _slot(shift: Shift) -> tuple[str, str, str]:
    return f"{shift.begin:%H:%M}", f"{shift.end:%H:%M}", shift.name


def request_swap(s: Session, day: dt.date, colleague: str, *, shift: str | None = None,
                 their_shift: str | None = None, their_day: dt.date | None = None,
                 give_away: bool = False, remark: str = "", confirm: bool = False) -> Write:
    mine = _pick_shift(shifts(s, day), shift, day)
    pool = _candidates(s, mine, give_away, their_day)
    matches = [c for c in pool if _matches_person(c.person, colleague)]
    if not matches:
        kind = "take over" if give_away else "swap"
        raise OrtecError(f"no colleague matching {colleague!r} can {kind} your "
                         f"{render.shift_line(mine)}; run swap-candidates to see who can")
    if their_shift:
        matches = [c for c in matches if c.shift and _matches_shift(c.shift, their_shift)]
        if not matches:
            raise OrtecError(f"{colleague!r} has no shift matching {their_shift!r}")
    if len(matches) > 1:
        options = "\n".join(f"  - {render.candidate_line(c)}" for c in matches)
        raise OrtecError(f"{colleague!r} is ambiguous; narrow it down with the colleague id "
                         f"or their shift (id or start time):\n{options}")
    chosen = matches[0]

    body = {"ShiftIdToSwap": mine.id, "ColleagueEmployeeId": chosen.person.id,
            "RequestRemark": remark}
    if chosen.shift:
        body["ShiftIdWanted"] = chosen.shift.id
    summary = [f"Your shift: {render.shift_line(mine)}"]
    if chosen.shift:
        summary.append(f"Swap with {chosen.person.name} for their {render.shift_line(chosen.shift)}")
    else:
        summary.append(f"Give it away to {chosen.person.name} (you get no shift back)")
    if remark:
        summary.append(f"Remark: {remark}")
    return _send(s, Write(summary, "POST", "/api/requests/swaps", body), confirm)


def respond_swap(s: Session, day: dt.date | None = None, *, request_id: int | None = None,
                 decline: bool = False, confirm: bool = False) -> Write:
    if day is None and request_id is None:
        raise OrtecError("give a date, a request id, or both")
    if day:
        found = [r for r in swap_requests(s, day) if r.touches(day)]
    else:
        today = dates.today()
        found = swap_requests(s, today - dt.timedelta(days=7), today + dt.timedelta(days=92))
    if request_id is not None:
        found = [r for r in found if r.id == request_id]
    open_incoming = [r for r in found if r.incoming and r.status == "Open"]
    if not open_incoming:
        where = f"on {day.isoformat()}" if day else f"with id {request_id}"
        others = "".join(f"\n  - {render.request_line(r)}" for r in found)
        raise OrtecError(f"no open incoming swap request {where}"
                         + (f"; found only:{others}" if others else ""))
    if len(open_incoming) > 1:
        options = "\n".join(f"  - {render.request_line(r)}" for r in open_incoming)
        raise OrtecError(f"several open requests match; pick one by id:\n{options}")
    request = open_incoming[0]

    verb = "Decline" if decline else "Accept"
    summary = [f"{verb} swap request {request.id} from {request.requester.name}"]
    if request.offered:
        summary.append(f"You get: {render.shift_line(request.offered)}")
    summary.append(f"You give: {render.shift_line(request.wanted)}" if request.wanted
                   else "You give: nothing (they are giving their shift away)")
    if request.remark:
        summary.append(f"Their remark: {request.remark}")
    if not decline:
        summary.append("There is no undo: accepting changes the schedule.")
    path = "/api/requests/swaps/decline" if decline else "/api/requests/swaps/accept"
    write = _send(s, Write(summary, "POST", path, {"id": request.id}), confirm)
    if write.sent:
        day_of = min(x.begin for x in (request.offered, request.wanted) if x).date()
        after = [r for r in swap_requests(s, day_of, day_of + dt.timedelta(days=1))
                 if r.id == request.id]
        write.after = [f"Status now: {after[0].status}" if after else "Status now: (not listed)"]
    return write


def leave_types(s: Session) -> list[dict]:
    return s.get(f"/api/v2/employees/{s.employee_id}/requests/leaves/leave-types")["leaveTypes"]


def leaves(s: Session, start: dt.date, end: dt.date | None = None) -> list[dict]:
    end = end or start
    raw = s.get(f"/api/v2/employees/{s.employee_id}/requests/leaves",
                **{"from": dates.at(start).isoformat(),
                   "until": dates.at(end + dt.timedelta(days=1)).isoformat()})
    names = {t["id"]: t["name"] for t in leave_types(s)}
    for leave in raw["leaveRequests"]:
        leave["leaveTypeName"] = names.get(leave["leaveTypeId"], str(leave["leaveTypeId"]))
    return sorted(raw["leaveRequests"], key=lambda r: r["fromDateTime"])


def leave_period(start: str, end: str | None = None, *,
                 now: dt.date | None = None) -> tuple[dt.datetime, dt.datetime, str]:
    """Turns the user's from/to into the API's (from, to, duration).

    Whole days run from 00:00 to 00:00 the day after the last, which is what the
    web app sends for every leave type.
    """
    first, start_time = dates.parse_moment(start, now=now)
    if end and re.fullmatch(r"\d{1,2}:\d{2}", end.strip()):
        last, end_time = first, dates.parse_time(end)
    elif end:
        last, end_time = dates.parse_moment(end, now=now)
    else:
        last, end_time = first, None

    if (start_time is None) != (end_time is None):
        raise OrtecError("give times on both ends (part of a day) or on neither (whole days)")
    if start_time is None:
        if last < first:
            raise OrtecError("leave ends before it starts")
        duration = "OneFullDay" if last == first else "MultipleDays"
        return dates.at(first), dates.at(last + dt.timedelta(days=1)), duration

    begin, finish = dates.at(first, start_time), dates.at(last, end_time)
    if last == first and end_time <= start_time:
        finish = dates.at(first + dt.timedelta(days=1), end_time)
    if not begin < finish <= begin + dt.timedelta(days=1):
        raise OrtecError("part-of-day leave must end within 24 hours of its start; "
                         "use whole dates for leave over several days")
    return begin, finish, "PartOfADay"


def request_leave(s: Session, start: str, end: str | None = None, *, leave_type: str | None = None,
                  remark: str = "", confirm: bool = False) -> Write:
    begin, finish, duration = leave_period(start, end)
    types = leave_types(s)
    if leave_type:
        types = [t for t in types
                 if leave_type == str(t["id"]) or leave_type.lower() in t["name"].lower()]
        if len(types) != 1:
            raise OrtecError(f"leave type {leave_type!r} matches {len(types)} types; "
                             "run leave-types to list them")
    if not types:
        raise OrtecError("this account has no leave types")
    kind = types[0]

    body = {"employeeId": s.employee_id, "leaveTypeId": kind["id"],
            "fromDateTime": begin.isoformat(), "toDateTime": finish.isoformat(),
            "duration": duration, "remarks": remark}
    summary = [f"{kind['name']}: {render.period_line(begin, finish, duration)}"]
    if remark:
        summary.append(f"Remark: {remark}")
    write = Write(summary, "POST", "/api/v2/requests/leaves", body)
    if confirm:
        check = s.post("/api/v2/requests/leaves/check-balance",
                       {k: body[k] for k in ("employeeId", "leaveTypeId", "fromDateTime",
                                             "toDateTime")})
        if not check.get("isOk"):
            raise OrtecError(f"balance check refused this leave: {check.get('details') or '?'}")
    return _send(s, write, confirm)


def _send(s: Session, write: Write, confirm: bool) -> Write:
    if confirm:
        write.result = s.request(write.method, write.path, json=write.body)
        write.sent = True
    return write


def _pick_shift(mine: list[Shift], wanted: str | None, day: dt.date) -> Shift:
    if wanted:
        mine = [m for m in mine if _matches_shift(m, wanted)]
    if not mine:
        raise OrtecError(f"you have no shift on {day.isoformat()}"
                         + (f" matching {wanted!r}" if wanted else ""))
    if len(mine) > 1:
        options = "\n".join(f"  - {render.shift_line(m)}" for m in mine)
        raise OrtecError(f"you have several shifts on {day.isoformat()}; "
                         f"pick one by id or start time:\n{options}")
    return mine[0]


def _matches_shift(shift: Shift, wanted: str) -> bool:
    wanted = wanted.strip()
    return wanted == str(shift.id) or wanted.zfill(5) == shift.begin.strftime("%H:%M")


def _matches_person(person: Person, wanted: str) -> bool:
    wanted = wanted.strip()
    return wanted == str(person.id) or wanted.lower() in person.name.lower()
