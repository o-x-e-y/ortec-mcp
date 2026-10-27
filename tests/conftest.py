"""Offline test rig.

The payloads mirror the live API's shapes as recorded on 2026-10-27, with the
names and ids replaced. Nothing here touches the network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ortec.errors import OrtecError

ME = 100


def person(id_, first, last):
    return {"Id": id_, "FirstName": first, "LastName": last, "Initials": "", "NamePrefix": ""}


def shift(id_, name, begin, end):
    return {"Id": id_, "Name": name, "Description": "", "BeginDateTime": begin,
            "EndDateTime": end, "ShiftDisplayCharacters": ""}


def roster_shift(id_, duty, begin, end):
    return {"Id": id_, "Name": None, "Status": 1, "BeginDate": begin, "EndDate": end,
            "Duty": {"Id": 9, "Name": duty, "Department": {"Id": 1, "Name": "Centrum"}},
            "Resource": {"Id": ME, "DisplayName": "Me, M."}}


SHIFTS = [
    roster_shift(1001, "Hall", "2026-10-28T14:00:00+01:00", "2026-10-28T19:30:00+01:00"),
    roster_shift(1002, "Bar", "2026-10-30T09:00:00+01:00", "2026-10-30T12:00:00+01:00"),
    roster_shift(1003, "Desk", "2026-10-30T18:00:00+01:00", "2026-10-30T22:00:00+01:00"),
    roster_shift(1004, "Hall", "2026-10-31T12:00:00+01:00", "2026-10-31T18:00:00+01:00"),
]

ANNA = person(200, "Anna", "Berg, A. van den")
BOB = person(201, "Bob", "Smit, B.")
BEA = person(202, "Bea", "Smits, B.")
CARL = person(203, "Carl", "Jansen, C.")
DIRK = person(204, "Dirk", "Vries, D. de")
MYSELF = person(ME, "Me", "Myself, M.")


def request(id_, status, requester, colleague, offered, wanted):
    return {"Id": id_, "RequestRemark": "", "RequestEmployee": requester,
            "ColleagueEmployee": colleague, "ShiftToSwap": offered, "ShiftWanted": wanted,
            "Status": status, "RequestCreateTime": "2026-10-20T12:00:00+02:00"}


SWAPS = [
    request(5001, "Open", ANNA, MYSELF,
            shift(2001, "Desk", "2026-10-28T15:00:00+01:00", "2026-10-28T22:15:00+01:00"),
            shift(1001, "Hall", "2026-10-28T14:00:00+01:00", "2026-10-28T19:30:00+01:00")),
    request(5002, "Open", MYSELF, BOB,
            shift(1001, "Hall", "2026-10-28T14:00:00+01:00", "2026-10-28T19:30:00+01:00"),
            shift(2002, "Bar", "2026-10-28T19:30:00+01:00", "2026-10-28T23:15:00+01:00")),
    request(5003, "Declined", CARL, MYSELF,
            shift(2003, "Bar", "2026-10-29T12:00:00+01:00", "2026-10-29T18:00:00+01:00"),
            shift(1004, "Hall", "2026-10-31T12:00:00+01:00", "2026-10-31T18:00:00+01:00")),
    request(5004, "Open", DIRK, MYSELF,
            shift(2004, "Hall", "2026-11-02T12:00:00+01:00", "2026-11-02T18:00:00+01:00"), None),
    request(5005, "Open", CARL, MYSELF,
            shift(2005, "Desk", "2026-11-02T18:00:00+01:00", "2026-11-02T22:00:00+01:00"), None),
]


def candidate(p, s):
    return {**p, "ShiftId": s["Id"], "Shift": s}


SWAP_CANDIDATES = [
    {**shift(2001, "Desk", "2026-10-28T15:00:00+01:00", "2026-10-28T22:15:00+01:00"),
     "IsReassignment": False,
     "Employees": [candidate(ANNA, shift(2001, "Desk", "2026-10-28T15:00:00+01:00",
                                         "2026-10-28T22:15:00+01:00"))]},
    {**shift(2002, "Bar", "2026-10-28T19:30:00+01:00", "2026-10-28T23:15:00+01:00"),
     "IsReassignment": False,
     "Employees": [
         candidate(BOB, shift(2002, "Bar", "2026-10-28T19:30:00+01:00",
                              "2026-10-28T23:15:00+01:00")),
         candidate(BEA, shift(2006, "Bar", "2026-10-28T19:30:00+01:00",
                              "2026-10-28T23:00:00+01:00")),
         candidate(ANNA, shift(2007, "Bar", "2026-10-28T11:00:00+01:00",
                               "2026-10-28T14:00:00+01:00")),
     ]},
]

REASSIGNMENT = [{"Name": "Reassignment", "IsReassignment": True,
                 "Employees": [{**DIRK, "ShiftId": None}, {**CARL, "ShiftId": None}]}]

LEAVE_TYPES = {"leaveTypes": [{"id": 8, "name": "Vakantie"},
                              {"id": 13, "name": "Verlof tijdens ziekte"}]}


class FakeSession:
    """Stands in for Session: answers from the payloads above and records calls."""

    employee_id = ME
    contract_ids = [ME]

    def __init__(self, balance_ok=True):
        self.calls = []
        self.balance_ok = balance_ok
        self.swaps = SWAPS

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def post(self, path, body):
        return self.request("POST", path, json=body)

    def request(self, method, path, params=None, json=None):
        self.calls.append((method, path, params or json))
        match method, path:
            case "GET", "/api/shifts":
                return SHIFTS
            case "GET", "/api/requests/100/swaps":
                return self.swaps
            case "GET", "/api/shifts/1001/swap-candidates":
                return REASSIGNMENT if params["type"] == "Reassignment" else SWAP_CANDIDATES
            case "GET", _ if path.endswith("/swap-candidates"):
                return []
            case "GET", "/api/v2/employees/100/requests/leaves/leave-types":
                return LEAVE_TYPES
            case "POST", "/api/v2/requests/leaves/check-balance":
                return {"isOk": self.balance_ok, "details": "" if self.balance_ok else "op"}
            case "POST", "/api/requests/swaps":
                return {"Id": 9999, "Status": "Open"}
            case "POST", ("/api/requests/swaps/accept" | "/api/requests/swaps/decline"
                          | "/api/v2/requests/leaves"):
                return None
        raise OrtecError(f"unexpected {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture(autouse=True)
def no_machine_config(monkeypatch):
    import os

    for name in [n for n in os.environ if n.startswith("ORTEC_")]:
        monkeypatch.delenv(name)


@pytest.fixture
def fake():
    return FakeSession()
