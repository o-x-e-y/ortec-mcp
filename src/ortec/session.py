import base64
import json
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .errors import OrtecError

DEFAULT_ESS = "https://workforce-ess.ortecapps.com"
DISCOVERY = "https://discovery.ortec-relevance.com/v1"

HEADERS = {
    "Accept-Language": "nl-NL",
    "X-App-Timezone": "Europe/Amsterdam",
}

TOKEN_MARGIN = 30


def default_path() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "ortec" / "session.json"


def company_code() -> str:
    if code := os.environ.get("ORTEC_COMPANY"):
        return code
    raise OrtecError("no company: set $ORTEC_COMPANY to the company code you enter in the app")


def credentials() -> tuple[str, str]:
    def read(name):
        if value := os.environ.get(name):
            return value
        if path := os.environ.get(f"{name}_FILE"):
            return Path(path).read_text().strip()
        raise OrtecError(f"no credentials: set ${name} or ${name}_FILE")

    return read("ORTEC_USER"), read("ORTEC_PASS")


@dataclass(frozen=True)
class Tenant:
    """Which employer's ESS to log in to. Everything else comes from the server."""

    magazine_id: str
    ess: str = DEFAULT_ESS

    @property
    def host(self) -> str:
        return urlparse(self.ess).hostname


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class Session:
    """An ESS session that logs itself in and survives between runs.

    The refresh cookie is single-use, so every response's cookies are written
    back to disk before the next request can need them.
    """

    def __init__(self, company: str | None = None, path: Path | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.company = (company or company_code()).strip().upper()
        self._tenant: Tenant | None = None
        self.path = path or default_path()
        self.http = httpx.Client(timeout=30, transport=transport)
        self.token: str | None = None
        self.token_exp = 0.0
        self.identity: dict[str, Any] | None = None
        self.lock = threading.RLock()
        self._load()

    @property
    def tenant(self) -> Tenant:
        if self._tenant is None:
            self._tenant = self._lookup()
        return self._tenant

    @property
    def employee_id(self) -> int:
        return self._identity()["employee_id"]

    @property
    def contract_ids(self) -> list[int]:
        return self._identity()["contract_ids"]

    def forget(self):
        with self.lock:
            self.http.cookies.clear()
            self.token, self.token_exp, self.identity = None, 0.0, None

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, json=body)

    def request(self, method: str, path: str, **kwargs) -> Any:
        with self.lock:
            response = self._send(method, path, **kwargs)
            if response.status_code == 401:
                self.token = None
                response = self._send(method, path, **kwargs)
            self._save()
        if response.is_error:
            raise OrtecError(_error_message(response))
        if not response.content:
            return None
        # Accept and decline answer 2xx with a body that is not JSON.
        if "json" not in response.headers.get("content-type", ""):
            return response.text
        return response.json()

    def _send(self, method, path, **kwargs) -> httpx.Response:
        headers = {**HEADERS, "Authorization": f"Bearer {self._token()}"}
        return self.http.request(method, self.tenant.ess + path, headers=headers, **kwargs)

    def _identity(self) -> dict[str, Any]:
        with self.lock:
            if self.identity is None:
                self._token()
            if self.identity is None:
                self._setup_user()
            return self.identity

    def _token(self) -> str:
        if self.token and time.time() < self.token_exp - TOKEN_MARGIN:
            return self.token
        if not self._refresh():
            self._login()
        return self.token

    def _refresh(self) -> bool:
        if not any(c.name == "ressRefreshToken" for c in self._ess_cookies()):
            return False
        # A Content-Type header makes this endpoint answer 400.
        response = self.http.post(f"{self.tenant.ess}/api/auth/token")
        # A spent or mangled refresh cookie is answered with 401 or 500.
        if response.is_error:
            return False
        self._set_token(response.json()["accessToken"])
        self._save()
        return True

    def _set_token(self, token: str):
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.token, self.token_exp = token, float(claims["exp"])

    def _login(self):
        username, password = credentials()
        self.forget()
        ess, magazine_id = self.tenant.ess, self.tenant.magazine_id

        response = self.http.get(f"{ess}/api/auth/authConfig", params={"magazineId": magazine_id})
        if response.is_error:
            raise OrtecError(f"login: no ESS tenant {magazine_id} at {ess} ({response.status_code})")
        auth = response.json()
        auth0 = "https://" + urlparse(auth["authorizeEndpoint"]).hostname

        response = self.http.get(f"{ess}/api/auth/start", params={
            "magazine_id": magazine_id, "language": "nl", "uid": str(uuid.uuid4()),
            "state": base64.b64encode(b"{}").decode(), "app_scheme": "web", "os_name": "web",
        })
        while response.is_redirect and "/auth/index.html" not in response.headers["location"]:
            response = self.http.get(urljoin(ess, response.headers["location"]))
        if not response.is_redirect:
            raise OrtecError(f"login: /api/auth/start answered {response.status_code}")
        server_state = _query(response.headers["location"])["state"]

        response = self.http.get(auth["authorizeEndpoint"], params={
            "client_id": auth["clientId"], "response_type": "code", "response_mode": "query",
            "scope": "openid profile email offline_access", "audience": auth["audience"],
            "redirect_uri": f"{ess}/auth/callback.html?magazineId={magazine_id}&state={server_state}",
            "state": secrets.token_urlsafe(16), "nonce": secrets.token_urlsafe(16),
        })
        login_url = urljoin(auth0, response.headers.get("location", ""))
        ulp_state = _query(login_url).get("state")
        if not ulp_state:
            raise OrtecError(f"login: Auth0 /authorize answered {response.status_code}")
        self.http.get(login_url)

        response = self.http.post(login_url, data={
            "state": ulp_state, "username": username, "password": password, "action": "default",
        })
        if response.status_code in (400, 401):
            raise OrtecError("login: Auth0 rejected the e-mail address or password")
        while response.is_redirect:
            location = urljoin(auth0, response.headers["location"])
            if "/mfa" in urlparse(location).path:
                raise OrtecError("login: MFA is enabled on this account; cannot log in headlessly")
            if location.startswith(f"{ess}/auth/callback.html"):
                break
            response = self.http.get(location)
        else:
            raise OrtecError(f"login: Auth0 answered {response.status_code} without a code")
        code = _query(location).get("code")
        if not code:
            raise OrtecError(f"login: Auth0 redirected without a code ({_query(location)})")

        response = self.http.get(f"{ess}/api/auth/callback",
                                 params={"code": code, "state": server_state})
        app = _query(response.headers.get("location", ""))
        if "code" not in app:
            raise OrtecError(f"login: /api/auth/callback answered {response.status_code}")
        response = self.http.post(f"{ess}/api/auth/exchange",
                                  json={"code": app["code"], "state": app["state"]})
        if response.is_error:
            raise OrtecError(_error_message(response))
        # The exchange only sets the refresh cookie; the ess.cookie.* chunks
        # that /api/v2 also needs arrive with the first refresh.
        if not self._refresh():
            raise OrtecError("login: token refresh failed straight after login")
        self._setup_user()

    def _lookup(self) -> Tenant:
        """The app's company-code form: it answers with a redirect to the
        tenant's Auth0 login, which carries the tenant id in its path."""
        response = self.http.post(f"{DISCOVERY}/platform-code",
                                  data={"platformCode": self.company})
        match = re.search(r"/auth0/(\d+)/", response.headers.get("location", ""))
        if not match:
            raise OrtecError(f"unknown company code {self.company!r}")
        return Tenant(match[1])

    def _setup_user(self):
        response = self.http.post(f"{self.tenant.ess}/api/setup-user", json={"language": "nl"},
                                  headers={**HEADERS, "Authorization": f"Bearer {self.token}"})
        if response.is_error:
            raise OrtecError(_error_message(response))
        metadata = response.json()["metadata"]
        self.identity = {
            "employee_id": metadata["resourceId"],
            "contract_ids": [c["EmployeeContractId"] for c in metadata["contracts"]],
            "departments": metadata["departments"],
        }
        self._save()

    def _load(self):
        try:
            state = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if state.get("company") != self.company:
            return
        self._tenant = Tenant(*state["tenant"])
        for cookie in state.get("cookies", []):
            self.http.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
        self.token, self.token_exp = state.get("token"), state.get("token_exp", 0.0)
        self.identity = state.get("identity")

    def _ess_cookies(self):
        return [c for c in self.http.cookies.jar if c.domain.lstrip(".") == self.tenant.host]

    def _save(self):
        state = {
            "company": self.company,
            "tenant": [self.tenant.magazine_id, self.tenant.ess],
            "cookies": [{"name": c.name, "value": c.value, "domain": c.domain}
                        for c in self._ess_cookies()],
            "token": self.token,
            "token_exp": self.token_exp,
            "identity": self.identity,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle)
        os.replace(tmp, self.path)


def _error_message(response: httpx.Response) -> str:
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        message = response.text.strip()
    return f"{response.request.method} {response.request.url.path}: {response.status_code} {message}"
