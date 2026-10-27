"""The only place output is formatted. Everything is Markdown."""

import datetime as dt
import json
import re


def shift_line(shift) -> str:
    return (f"{shift.name} {shift.begin:%a %Y-%m-%d %H:%M}–{shift.end:%H:%M} "
            f"(shift {shift.id})")


def candidate_line(c) -> str:
    their = f"their {shift_line(c.shift)}" if c.shift else "takes it over"
    return f"{c.person.name} (id {c.person.id}): {their}"


def request_line(r) -> str:
    direction = f"from {r.requester.name}" if r.incoming else f"to {r.colleague.name}"
    shifts = " / ".join(shift_line(x) for x in (r.offered, r.wanted) if x)
    return f"request {r.id} {direction}, {r.status}: {shifts}"


def period_line(begin: dt.datetime, finish: dt.datetime, duration: str) -> str:
    if duration == "PartOfADay" or begin.time() != dt.time() or finish.time() != dt.time():
        if finish.date() == begin.date():
            return f"{begin:%a %Y-%m-%d %H:%M} – {finish:%H:%M}"
        return f"{begin:%a %Y-%m-%d %H:%M} – {finish:%a %Y-%m-%d %H:%M}"
    if duration == "OneFullDay":
        return f"{begin:%a %Y-%m-%d}, whole day"
    last = (finish - dt.timedelta(days=1)).date()
    return f"{begin:%a %Y-%m-%d} to {last:%a %Y-%m-%d} inclusive, whole days"


def shifts(found, start: dt.date, end: dt.date) -> str:
    title = f"# Your shifts, {span(start, end)}"
    if not found:
        return f"{title}\n\nNo shifts."
    return f"{title}\n\n" + "\n".join(f"- {shift_line(s)}" for s in found)


def swap_requests(found, start: dt.date, end: dt.date) -> str:
    title = f"# Swap requests touching {span(start, end)}"
    if not found:
        return f"{title}\n\nNone."
    out = [title]
    for heading, incoming in (("Incoming", True), ("Outgoing", False)):
        group = [r for r in found if r.incoming == incoming]
        if group:
            out += ["", f"## {heading}", ""] + [_request_block(r) for r in group]
    return "\n".join(out)


def _request_block(r) -> str:
    if r.incoming:
        lines = [f"- **{r.id}** from {r.requester.name} — {r.status}",
                 f"  - you get: {shift_line(r.offered) if r.offered else 'nothing'}",
                 f"  - you give: {shift_line(r.wanted) if r.wanted else 'nothing'}"]
    else:
        lines = [f"- **{r.id}** to {r.colleague.name} — {r.status}",
                 f"  - you give: {shift_line(r.offered) if r.offered else 'nothing'}",
                 f"  - you get: {shift_line(r.wanted) if r.wanted else 'nothing'}"]
    if r.remark:
        lines.append(f"  - remark: {r.remark}")
    return "\n".join(lines)


def swap_candidates(found, day: dt.date, give_away: bool) -> str:
    kind = "who can take over" if give_away else "who you can swap with"
    title = f"# {kind.capitalize()}, {day:%a %Y-%m-%d}"
    if not found:
        return f"{title}\n\nYou have no shift that day."
    out = [title]
    for mine, candidates in found:
        out += ["", f"## Your {shift_line(mine)}", ""]
        out += [f"- {candidate_line(c)}" for c in candidates] or ["Nobody."]
    return "\n".join(out)


def leave_types(types) -> str:
    return "# Leave types\n\n" + "\n".join(f"- {t['name']} (id {t['id']})" for t in types)


def leaves(found, start: dt.date, end: dt.date) -> str:
    title = f"# Leave requests, {span(start, end)}"
    if not found:
        return f"{title}\n\nNone."
    out = [title, ""]
    for leave in found:
        begin = dt.datetime.fromisoformat(leave["fromDateTime"])
        finish = dt.datetime.fromisoformat(leave["toDateTime"])
        out.append(f"- **{leave['id']}** {leave['leaveTypeName']}: "
                   f"{period_line(begin, finish, leave['duration'])} — {leave['_status']}")
        if leave.get("_declineReason"):
            out.append(f"  - reason: {leave['_declineReason']}")
        if leave.get("remarks"):
            out.append(f"  - remark: {leave['remarks']}")
    return "\n".join(out)


def write(w, confirm_hint: str) -> str:
    out = [("# Sent" if w.sent else "# Not sent — preview"), ""]
    out += [f"- {line}" for line in w.summary]
    out += ["", f"`{w.method} {w.path}`", "", "```json", json.dumps(w.body, indent=2), "```"]
    if w.sent:
        out += [""] + w.after
        if isinstance(w.result, dict) and "Status" in w.result:
            out.append(f"Request {w.result.get('Id')} is {w.result['Status']}.")
    else:
        out += ["", f"Nothing was sent. To send it, {confirm_hint}."]
    return "\n".join(out)


def span(start: dt.date, end: dt.date) -> str:
    if start == end:
        return f"{start:%a %Y-%m-%d}"
    return f"{start:%a %Y-%m-%d} to {end:%a %Y-%m-%d}"


WORKING_WITH_MARKER = "ortec working-with"
INCOMPLETE = ("Incomplete: you have a swap request pending on this shift, which hides "
              "colleagues until it is settled.")


def working_with(found, start: dt.date, end: dt.date) -> str:
    out = [f"# Working with, {span(start, end)}"]
    for d in found:
        own = ", ".join(f"{s.name} {s.begin:%H:%M}–{s.end:%H:%M}" for s in d.mine)
        out += ["", f"## {d.day:%a %Y-%m-%d}: your {own}", ""]
        out += [f"- {line}" for line in _working_lines(d)] or ["Nobody listed."]
        if d.incomplete:
            out += ["", f"_{INCOMPLETE}_"]
    out += ["", "Only colleagues you could swap with show up."]
    return "\n".join(out)


def working_with_json(found, now: dt.datetime) -> str:
    """One calendar entry per day, ready to copy: the routine adds nothing."""
    days = []
    for d in found:
        people = [(f"{c.shift.begin:%H:%M}", c.person.name) for c in d.colleagues]
        people += [(k.start, k.name) for k in d.kept]
        names = list(dict.fromkeys(name.split()[0] for _, name in sorted(people)))
        lines = [f"Your shift: {s.name} {s.begin:%H:%M}–{s.end:%H:%M}" for s in d.mine]
        lines += _working_lines(d) or ["Nobody listed."]
        if d.incomplete:
            lines += ["", INCOMPLETE]
        lines += ["", "Only colleagues you could swap with show up.",
                  f"{WORKING_WITH_MARKER} · updated {now:%Y-%m-%d %H:%M}"]
        days.append({
            "date": d.day.isoformat(),
            "title": "Working with " + (", ".join(names) if names else "nobody listed"),
            "description": "\n".join(lines),
            "incomplete": d.incomplete,
        })
    return json.dumps({"marker": WORKING_WITH_MARKER, "days": days}, indent=2,
                      ensure_ascii=False)


_LINE = re.compile(r"^(\d{2}:\d{2})–(\d{2}:\d{2}) (.+?): (.+?)"
                   r"(?: \(last seen (\d{4}-\d{2}-\d{2})\))?$")


def _working_lines(d) -> list[str]:
    lines = []
    for c in d.colleagues:
        start, end = f"{c.shift.begin:%H:%M}", f"{c.shift.end:%H:%M}"
        lines.append((start, f"{start}–{end} {c.shift.name}: {c.person.name}"))
    for k in d.kept:
        lines.append((k.start, f"{k.start}–{k.end} {k.duty}: {k.name} (last seen {k.last_seen})"))
    return [text for _, text in sorted(lines, key=lambda line: line[0])]


def read_working_with(description: str) -> list[tuple[str, str, str, str, dt.date]]:
    """The colleague lines of a description `working_with_json` wrote, as
    (start, end, duty, name, last seen). A line without a "last seen" was seen
    when the description was last updated."""
    updated = re.search(r"updated (\d{4}-\d{2}-\d{2})", description)
    fallback = dt.date.fromisoformat(updated[1]) if updated else dt.date.min
    entries = []
    for line in description.splitlines():
        if m := _LINE.match(line.strip()):
            seen = dt.date.fromisoformat(m[5]) if m[5] else fallback
            entries.append((m[1], m[2], m[3], m[4], seen))
    return entries
