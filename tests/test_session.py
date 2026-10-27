import base64
import json
import time

import httpx
import pytest

from ortec.errors import OrtecError
from ortec.session import Session

ESS = "workforce-ess.ortecapps.com"
SERVER_STATE = base64.b64encode(b'{"sessionId":"s","userId":"u"}').decode()


def jwt(ttl=300):
    claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + ttl}).encode()).decode()
    return f"x.{claims.rstrip('=')}.y"


def cookie(name, value):
    return ("set-cookie", f"{name}={value}; Domain=.{ESS}; Path=/; Secure; HttpOnly")


def redirect(to):
    return httpx.Response(302, headers={"location": to})


class FakeTenant:
    """The ESS + Auth0 login chain, with a single-use rotating refresh cookie."""

    def __init__(self, mfa=False, password="pw"):
        self.mfa, self.password = mfa, password
        self.refresh = None
        self.log = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.log.append(f"{request.method} {path}")
        match request.method, request.url.host, path:
            case "POST", "discovery.ortec-relevance.com", "/v1/platform-code":
                code = dict(httpx.QueryParams(request.content.decode()))["platformCode"]
                tenant = {"ACME1": 101, "OTHER": 999}.get(code)
                if tenant is None:
                    return redirect("https://discovery.ortec-relevance.com/v1/login?x=1")
                return redirect(f"https://login.onimgzine.com/auth0/{tenant}/login?x=1")
            case "GET", "workforce-ess.ortecapps.com", "/api/auth/authConfig":
                if request.url.params["magazineId"] != "101":
                    return httpx.Response(404, json={"statusCode": 404})
                return httpx.Response(200, json={
                    "authorizeEndpoint": "https://login.acme.example/authorize",
                    "clientId": "client", "audience": "https://acme/ortec.ws.essapp"})
            case "GET", "workforce-ess.ortecapps.com", "/api/auth/start":
                assert request.url.params["magazine_id"] == "101"
                return redirect(f"https://{ESS}/api/auth/login?x=1")
            case "GET", "workforce-ess.ortecapps.com", "/api/auth/login":
                return redirect(f"https://{ESS}/auth/index.html?state={SERVER_STATE}&magazineId=101")
            case "GET", "login.acme.example", "/authorize":
                assert f"state={SERVER_STATE}" in request.url.params["redirect_uri"]
                return redirect("/u/login?state=ulp")
            case "GET", "login.acme.example", "/u/login":
                return httpx.Response(200, text="<form>")
            case "POST", "login.acme.example", "/u/login":
                form = dict(httpx.QueryParams(request.content.decode()))
                if form["password"] != self.password:
                    return httpx.Response(400, text="Wrong email or password")
                return redirect("/mfa/challenge" if self.mfa else "/authorize/resume?state=ulp")
            case "GET", "login.acme.example", "/authorize/resume":
                return redirect(f"https://{ESS}/auth/callback.html?magazineId=101"
                                f"&state={SERVER_STATE}&code=auth0code")
            case "GET", "workforce-ess.ortecapps.com", "/api/auth/callback":
                assert request.url.params["state"] == SERVER_STATE
                return redirect(f"https://{ESS}/?state=e30=&code=appcode&magazine=101")
            case "POST", "workforce-ess.ortecapps.com", "/api/auth/exchange":
                assert json.loads(request.content) == {"code": "appcode", "state": "e30="}
                return self._rotate({"accessToken": jwt()})
            case "POST", "workforce-ess.ortecapps.com", "/api/auth/token":
                assert "content-type" not in request.headers
                if f"ressRefreshToken={self.refresh}" not in request.headers.get("cookie", ""):
                    return httpx.Response(500, json={"statusCode": 500})
                return self._rotate({"accessToken": jwt()}, chunks=True)
            case "POST", "workforce-ess.ortecapps.com", "/api/setup-user":
                return httpx.Response(201, json={"metadata": {
                    "resourceId": 100, "departments": [],
                    "contracts": [{"EmployeeContractId": 100}]}})
            case "GET", "workforce-ess.ortecapps.com", "/api/v2/thing":
                if "ess.cookie.0" not in request.headers.get("cookie", ""):
                    return httpx.Response(401, text="Unauthorized")
                return httpx.Response(200, json={"ok": True})
            case "POST", "workforce-ess.ortecapps.com", "/api/requests/swaps/decline":
                return httpx.Response(201, text="Created")
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def _rotate(self, body, chunks=False):
        self.refresh = f"r{len(self.log)}"
        headers = [cookie("ressRefreshToken", self.refresh)]
        if chunks:
            headers.append(cookie("ess.cookie.0", f"c{len(self.log)}"))
        return httpx.Response(200, json=body, headers=headers)


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv("ORTEC_USER", "me@example.com")
    monkeypatch.setenv("ORTEC_PASS", "pw")


def session(tmp_path, tenant, company="acme1"):
    return Session(company, path=tmp_path / "ortec" / "session.json",
                   transport=httpx.MockTransport(tenant))


def test_the_company_code_is_looked_up_once(tmp_path):
    tenant = FakeTenant()
    s = session(tmp_path, tenant)
    s.get("/api/v2/thing")
    assert s.tenant.magazine_id == "101"
    assert tenant.log.count("POST /v1/platform-code") == 1
    tenant.log.clear()
    session(tmp_path, tenant).get("/api/v2/thing")
    assert tenant.log == ["GET /api/v2/thing"]


def test_a_session_for_another_company_is_not_reused(tmp_path):
    tenant = FakeTenant()
    session(tmp_path, tenant).get("/api/v2/thing")
    other = session(tmp_path, tenant, "OTHER")
    assert other.token is None
    assert other.tenant.magazine_id == "999"


def test_an_unknown_company_code_is_reported(tmp_path):
    with pytest.raises(OrtecError, match="unknown company code 'NOPE1'"):
        session(tmp_path, FakeTenant(), "nope1").get("/api/v2/thing")


def test_a_company_without_an_ess_tenant_is_reported(tmp_path):
    with pytest.raises(OrtecError, match="no ESS tenant 999"):
        session(tmp_path, FakeTenant(), "OTHER").get("/api/v2/thing")


def test_the_company_code_is_required(tmp_path, monkeypatch):
    monkeypatch.delenv("ORTEC_COMPANY", raising=False)
    with pytest.raises(OrtecError, match=r"\$ORTEC_COMPANY"):
        Session(path=tmp_path / "s.json")


def test_logs_in_and_persists_a_private_session(tmp_path):
    tenant = FakeTenant()
    s = session(tmp_path, tenant)
    assert s.get("/api/v2/thing") == {"ok": True}
    assert s.employee_id == 100
    path = tmp_path / "ortec" / "session.json"
    assert path.stat().st_mode & 0o777 == 0o600
    saved = json.loads(path.read_text())
    assert {c["name"] for c in saved["cookies"]} == {"ressRefreshToken", "ess.cookie.0"}
    assert next(c["value"] for c in saved["cookies"]
                if c["name"] == "ressRefreshToken") == tenant.refresh
    assert "pw" not in path.read_text()


def test_a_new_process_reuses_the_session_without_logging_in(tmp_path):
    tenant = FakeTenant()
    session(tmp_path, tenant).get("/api/v2/thing")
    tenant.log.clear()
    assert session(tmp_path, tenant).get("/api/v2/thing") == {"ok": True}
    assert tenant.log == ["GET /api/v2/thing"]


def test_expired_token_is_refreshed_with_the_rotated_cookie(tmp_path):
    tenant = FakeTenant()
    session(tmp_path, tenant).get("/api/v2/thing")
    path = tmp_path / "ortec" / "session.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "token_exp": 0}))
    tenant.log.clear()
    session(tmp_path, tenant).get("/api/v2/thing")
    assert tenant.log == ["POST /api/auth/token", "GET /api/v2/thing"]


def test_spent_refresh_cookie_falls_back_to_a_login(tmp_path):
    tenant = FakeTenant()
    session(tmp_path, tenant).get("/api/v2/thing")
    path = tmp_path / "ortec" / "session.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "token_exp": 0}))
    tenant.refresh = "rotated-elsewhere"
    tenant.log.clear()
    assert session(tmp_path, tenant).get("/api/v2/thing") == {"ok": True}
    assert tenant.log[:2] == ["POST /api/auth/token", "GET /api/auth/authConfig"]


def test_a_write_answered_with_plain_text_is_not_parsed_as_json(tmp_path):
    # The real accept/decline endpoints answer 2xx with a non-JSON body.
    s = session(tmp_path, FakeTenant())
    assert s.post("/api/requests/swaps/decline", {"id": 1}) == "Created"


def test_a_401_is_retried_once_with_a_fresh_token(tmp_path):
    tenant = FakeTenant()
    s = session(tmp_path, tenant)
    s.get("/api/v2/thing")
    s.http.cookies.delete("ess.cookie.0", domain=f".{ESS}")
    tenant.log.clear()
    assert s.get("/api/v2/thing") == {"ok": True}
    assert tenant.log == ["GET /api/v2/thing", "POST /api/auth/token", "GET /api/v2/thing"]


def test_mfa_is_reported_not_worked_around(tmp_path):
    with pytest.raises(OrtecError, match="MFA"):
        session(tmp_path, FakeTenant(mfa=True)).get("/api/v2/thing")


def test_wrong_password_is_reported(tmp_path):
    with pytest.raises(OrtecError, match="rejected"):
        session(tmp_path, FakeTenant(password="other")).get("/api/v2/thing")


def test_missing_credentials_are_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("ORTEC_PASS")
    with pytest.raises(OrtecError, match=r"\$ORTEC_PASS"):
        session(tmp_path, FakeTenant()).get("/api/v2/thing")


def test_credentials_can_come_from_files(tmp_path, monkeypatch):
    monkeypatch.delenv("ORTEC_PASS")
    (tmp_path / "pass").write_text("pw\n")
    monkeypatch.setenv("ORTEC_PASS_FILE", str(tmp_path / "pass"))
    assert session(tmp_path, FakeTenant()).get("/api/v2/thing") == {"ok": True}
