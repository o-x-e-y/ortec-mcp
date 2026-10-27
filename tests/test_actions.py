import datetime as dt

import pytest

from ortec import actions
from ortec.errors import OrtecError

OCT28 = dt.date(2026, 10, 28)


def test_display_names():
    assert actions.display_name("Tess", "Linden, T.M.H. van der") == "Tess van der Linden"
    assert actions.display_name("Emma", "Wal, E van de") == "Emma van de Wal"
    assert actions.display_name("Kim", "Bos, K") == "Kim Bos"
    assert actions.display_name("Mono", None) == "Mono"


def test_shifts_filters_to_the_local_days_and_queries_an_exclusive_end(fake):
    found = actions.shifts(fake, dt.date(2026, 10, 30))
    assert [s.id for s in found] == [1002, 1003]
    assert fake.calls[0][2] == {"fromDate": "2026-10-30", "untilDate": "2026-10-31",
                                "resourceId": 100}


def test_swap_requests_split_on_who_asked(fake):
    found = {r.id: r for r in actions.swap_requests(fake, OCT28, dt.date(2026, 11, 2))}
    assert found[5001].incoming and found[5001].requester.name == "Anna van den Berg"
    assert not found[5002].incoming
    assert fake.calls[0][2] == {"fromDate": "2026-10-28", "untilDate": "2026-11-03"}


def test_accept_matches_either_shift_and_previews_without_posting(fake):
    # 5003 touches the 31st only through the shift the user would give away.
    with pytest.raises(OrtecError, match="(?s)no open incoming.*5003.*Declined"):
        actions.respond_swap(fake, dt.date(2026, 10, 31))
    write = actions.respond_swap(fake, OCT28)
    assert write.body == {"id": 5001} and not write.sent
    assert any("You get: Desk" in line for line in write.summary)
    assert any("You give: Hall" in line for line in write.summary)
    assert fake.posts() == []


def test_accept_never_picks_an_outgoing_request(fake):
    fake.swaps = [r for r in fake.swaps if r["Id"] == 5002]
    with pytest.raises(OrtecError, match="no open incoming"):
        actions.respond_swap(fake, OCT28)


def test_accept_refuses_to_guess_between_several(fake):
    with pytest.raises(OrtecError, match="(?s)several open requests.*5004.*5005"):
        actions.respond_swap(fake, dt.date(2026, 11, 2))
    write = actions.respond_swap(fake, dt.date(2026, 11, 2), request_id=5005)
    assert write.body == {"id": 5005}
    assert any("nothing" in line for line in write.summary)


def test_accept_with_confirm_posts_the_request_id_and_rereads(fake):
    write = actions.respond_swap(fake, OCT28, confirm=True)
    assert fake.posts() == [("POST", "/api/requests/swaps/accept", {"id": 5001})]
    assert write.sent and write.after == ["Status now: Open"]


def test_decline_uses_its_own_endpoint(fake):
    actions.respond_swap(fake, OCT28, decline=True, confirm=True)
    assert fake.posts() == [("POST", "/api/requests/swaps/decline", {"id": 5001})]


def test_swap_request_body_for_a_swap(fake):
    write = actions.request_swap(fake, OCT28, "bob")
    assert write.body == {"ShiftIdToSwap": 1001, "ColleagueEmployeeId": 201,
                          "RequestRemark": "", "ShiftIdWanted": 2002}
    assert fake.posts() == []


def test_swap_request_body_for_a_give_away_has_no_wanted_shift(fake):
    write = actions.request_swap(fake, OCT28, "dirk", give_away=True, remark="ziek",
                                 confirm=True)
    assert fake.posts() == [("POST", "/api/requests/swaps",
                             {"ShiftIdToSwap": 1001, "ColleagueEmployeeId": 204,
                              "RequestRemark": "ziek"})]
    assert write.sent


def test_swap_request_refuses_an_ambiguous_colleague(fake):
    with pytest.raises(OrtecError, match="ambiguous"):
        actions.request_swap(fake, OCT28, "smit")
    with pytest.raises(OrtecError, match="ambiguous"):
        actions.request_swap(fake, OCT28, "anna")
    write = actions.request_swap(fake, OCT28, "anna", their_shift="11:00")
    assert write.body["ShiftIdWanted"] == 2007


def test_swap_request_needs_a_candidate(fake):
    with pytest.raises(OrtecError, match="no colleague matching 'zed'"):
        actions.request_swap(fake, OCT28, "zed")


def test_swap_request_asks_which_shift_when_there_are_two(fake):
    with pytest.raises(OrtecError, match="several shifts"):
        actions.request_swap(fake, dt.date(2026, 10, 30), "bob")


def test_candidates_for_another_day_pass_the_date(fake):
    actions.swap_candidates(fake, OCT28, their_day=dt.date(2026, 10, 29))
    assert fake.calls[-1][2] == {"type": "Swap", "date": "2026-10-29"}
    actions.swap_candidates(fake, OCT28, their_day=OCT28, give_away=True)
    assert fake.calls[-1][2] == {"type": "Reassignment"}


def test_leave_preview_sends_nothing(fake):
    write = actions.request_leave(fake, "2026-11-10")
    assert fake.posts() == [] and not write.sent
    assert write.body["leaveTypeId"] == 8


def test_leave_checks_the_balance_before_creating(fake):
    actions.request_leave(fake, "2026-11-10", leave_type="ziekte", confirm=True)
    assert [c[1] for c in fake.posts()] == ["/api/v2/requests/leaves/check-balance",
                                            "/api/v2/requests/leaves"]
    assert fake.posts()[1][2]["leaveTypeId"] == 13


def test_leave_stops_when_the_balance_check_fails():
    from conftest import FakeSession

    fake = FakeSession(balance_ok=False)
    with pytest.raises(OrtecError, match="balance check refused"):
        actions.request_leave(fake, "2026-11-10", confirm=True)
    assert [c[1] for c in fake.posts()] == ["/api/v2/requests/leaves/check-balance"]


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2026-11-10", None,
     ("2026-11-10T00:00:00+01:00", "2026-11-11T00:00:00+01:00", "OneFullDay")),
    ("2026-11-10", "2026-11-10",
     ("2026-11-10T00:00:00+01:00", "2026-11-11T00:00:00+01:00", "OneFullDay")),
    ("2026-11-10", "2026-11-12",
     ("2026-11-10T00:00:00+01:00", "2026-11-13T00:00:00+01:00", "MultipleDays")),
    ("2026-11-10 14:00", "18:00",
     ("2026-11-10T14:00:00+01:00", "2026-11-10T18:00:00+01:00", "PartOfADay")),
    ("2026-11-10T22:00", "2026-11-11 02:00",
     ("2026-11-10T22:00:00+01:00", "2026-11-11T02:00:00+01:00", "PartOfADay")),
    ("2026-11-10 22:00", "02:00",
     ("2026-11-10T22:00:00+01:00", "2026-11-11T02:00:00+01:00", "PartOfADay")),
    # Clocks go back on 2026-10-25: the day starts at +02:00 and ends at +01:00.
    ("2026-10-25", None,
     ("2026-10-25T00:00:00+02:00", "2026-10-26T00:00:00+01:00", "OneFullDay")),
])
def test_leave_period(start, end, expected):
    begin, finish, duration = actions.leave_period(start, end)
    assert (begin.isoformat(), finish.isoformat(), duration) == expected


@pytest.mark.parametrize(("start", "end", "error"), [
    ("2026-11-10 14:00", "2026-11-11", "both ends"),
    ("2026-11-12", "2026-11-10", "ends before"),
    ("2026-11-10 14:00", "2026-11-12 10:00", "within 24 hours"),
    ("10-11-2026", None, "bad date"),
])
def test_leave_period_rejects(start, end, error):
    with pytest.raises(OrtecError, match=error):
        actions.leave_period(start, end)


def test_relative_dates():
    assert actions.leave_period("tomorrow", now=OCT28)[0].date() == dt.date(2026, 10, 29)


def test_spans():
    from ortec import dates

    today = dates.today()
    assert dates.span(None, None, 13) == (today, today + dt.timedelta(days=13))
    assert dates.span("2026-10-28", None, 13) == (OCT28, OCT28)
    assert dates.span("2026-10-28", "2026-10-30", 13) == (OCT28, dt.date(2026, 10, 30))


@pytest.mark.parametrize(("value", "today", "expected"), [
    ("2026-09-13", "2026-10-05", "2026-09-13"),
    ("vandaag", "2026-10-05", "2026-10-05"),
    ("Morgen", "2026-10-05", "2026-10-06"),
    ("+5", "2026-10-05", "2026-10-10"),
    ("20", "2026-10-05", "2026-10-20"),
    ("5", "2026-10-05", "2026-10-05"),
    ("08", "2026-10-19", "2026-11-08"),
    ("3", "2026-12-19", "2027-01-03"),
    ("11-01", "2026-10-05", "2026-11-01"),
    ("10-05", "2026-10-05", "2026-10-05"),
    ("01-15", "2026-11-20", "2027-01-15"),
    ("1-5", "2026-11-20", "2027-01-05"),
])
def test_short_and_relative_dates(value, today, expected):
    from ortec import dates

    assert dates.parse_date(value, now=dt.date.fromisoformat(today)).isoformat() == expected


@pytest.mark.parametrize(("value", "today", "hint"), [
    ("31", "2026-11-05", None),
    ("02-30", "2026-01-01", None),
    ("31-10", "2026-10-05", "did you mean 2026-10-31?"),
    ("13-09-2026", "2026-10-05", "did you mean 2026-09-13?"),
])
def test_impossible_dates_are_rejected(value, today, hint):
    from ortec import dates

    with pytest.raises(OrtecError) as error:
        dates.parse_date(value, now=dt.date.fromisoformat(today))
    assert str(error.value).endswith(hint) if hint else "did you mean" not in str(error.value)


def test_short_dates_take_a_time():
    begin, finish, duration = actions.leave_period("+1 14:00", "18:00", now=OCT28)
    assert (begin.isoformat(), finish.isoformat(), duration) == (
        "2026-10-29T14:00:00+01:00", "2026-10-29T18:00:00+01:00", "PartOfADay")
    assert actions.leave_period("30", "11-02", now=OCT28)[2] == "MultipleDays"


@pytest.fixture
def oct27(monkeypatch):
    from ortec import dates

    monkeypatch.setattr(dates, "today", lambda: dt.date(2026, 10, 27))


def test_working_with_refuses_days_off_without_looking(fake, oct27):
    with pytest.raises(OrtecError, match="no shift on Thu 2026-10-29; working-with only"):
        actions.working_with(fake, dt.date(2026, 10, 29))
    assert not any(c[1].endswith("/swap-candidates") for c in fake.calls)


def test_working_with_refuses_the_past(fake, oct27):
    with pytest.raises(OrtecError, match="today and later"):
        actions.working_with(fake, dt.date(2026, 10, 26))


def test_working_with_lists_colleagues_on_that_day(fake, oct27):
    [day] = actions.working_with(fake, OCT28, pause=0)
    assert [m.id for m in day.mine] == [1001]
    assert [(c.person.name, c.shift.id) for c in day.colleagues] == [
        ("Anna van den Berg", 2007), ("Anna van den Berg", 2001),
        ("Bea Smits", 2006), ("Bob Smit", 2002)]


def test_an_open_outgoing_request_marks_the_day_incomplete(fake, oct27):
    # 5002 is the user's own open request on shift 1001; 5001 (incoming) is not counted.
    assert actions.working_with(fake, OCT28, pause=0)[0].incomplete
    fake.swaps = [r for r in fake.swaps if r["Id"] != 5002]
    assert not actions.working_with(fake, OCT28, pause=0)[0].incomplete


def test_working_with_a_week_only_visits_work_days_and_pauses(fake, oct27, monkeypatch):
    from ortec import actions as module

    slept = []
    monkeypatch.setattr(module.time, "sleep", slept.append)
    found = actions.working_with(fake, dt.date(2026, 10, 27), dt.date(2026, 11, 2))
    assert [d.day.isoformat() for d in found] == ["2026-10-28", "2026-10-30", "2026-10-31"]
    asked = [c[1] for c in fake.calls if c[1].endswith("/swap-candidates")]
    assert asked == [f"/api/shifts/{i}/swap-candidates" for i in (1001, 1002, 1003, 1004)]
    assert slept == [0.5, 0.5, 0.5]


def test_working_with_json_is_ready_for_a_calendar(fake, oct27):
    import json

    from ortec import render

    found = actions.working_with(fake, OCT28, pause=0)
    out = json.loads(render.working_with_json(found, dt.datetime(2026, 10, 27, 21, 5)))
    [day] = out["days"]
    assert day["date"] == "2026-10-28" and day["incomplete"]
    assert day["title"] == "Working with Anna, Bea, Bob"
    assert day["description"].splitlines()[:2] == [
        "Your shift: Hall 14:00–19:30", "11:00–14:00 Bar: Anna van den Berg"]
    assert day["description"].endswith("ortec working-with · updated 2026-10-27 21:05")
    assert out["marker"] in day["description"]


def test_a_request_awaiting_approval_still_marks_the_day_incomplete(fake, oct27):
    # Accepted by the colleague, waiting for a planner: the live list stayed short.
    fake.swaps = [{**r, "Status": "Processing"} if r["Id"] == 5002 else r for r in fake.swaps]
    assert actions.working_with(fake, OCT28, pause=0)[0].incomplete


def test_shift_names_are_trimmed():
    raw = {"Id": 1, "Name": "Late shift ", "BeginDateTime": "2026-10-30T18:30:00+01:00",
           "EndDateTime": "2026-10-30T23:30:00+01:00"}
    assert actions.Shift.parse(raw).name == "Late shift"


PREVIOUS_28 = """Your shift: Hall 14:00–19:30
15:00–22:15 Desk: Tess van der Linden
19:30–23:15 Hall: Fenna Visser
14:00–19:30 Hall: Someone Before
08:00–12:00 Bar: Kim Bos (last seen 2026-10-20)

Only colleagues you could swap with show up.
ortec working-with · updated 2026-10-26 23:39"""


def test_carry_over_matches_by_time_slot(fake, oct27):
    [day] = actions.carry_over(actions.working_with(fake, OCT28, pause=0),
                               {"2026-10-28": PREVIOUS_28})
    # Desk 15:00–22:15 now has Anna: that was a swap, Tess goes. The user's own
    # slot is never kept. Vanished slots stay, with when they were last seen.
    assert [(k.name, k.slot, k.last_seen.isoformat()) for k in day.kept] == [
        ("Fenna Visser", ("19:30", "23:15", "Hall"), "2026-10-26"),
        ("Kim Bos", ("08:00", "12:00", "Bar"), "2026-10-20")]


def test_kept_colleagues_are_written_back_and_read_again(fake, oct27):
    import json

    from ortec import render

    found = actions.carry_over(actions.working_with(fake, OCT28, pause=0),
                               {"2026-10-28": PREVIOUS_28})
    [day] = json.loads(render.working_with_json(found, dt.datetime(2026, 10, 27, 23, 39)))["days"]
    assert day["title"] == "Working with Kim, Anna, Bea, Bob, Fenna"
    lines = day["description"].splitlines()
    assert lines[1] == "08:00–12:00 Bar: Kim Bos (last seen 2026-10-20)"
    assert "19:30–23:15 Hall: Fenna Visser (last seen 2026-10-26)" in lines
    # A second run reads its own output back without losing anything.
    again = actions.carry_over(actions.working_with(fake, OCT28, pause=0),
                               {"2026-10-28": day["description"]})
    assert [k.name for k in again[0].kept] == ["Kim Bos", "Fenna Visser"]


def test_without_previous_nothing_is_kept(fake, oct27):
    found = actions.working_with(fake, OCT28, pause=0)
    assert actions.carry_over(found, {"2026-10-30": PREVIOUS_28})[0].kept == []
