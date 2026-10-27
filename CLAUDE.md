# ortec

CLI and MCP server over the ORTEC ESS API.

## Using it

Run `ortec --help` first. Output is Markdown formatted by `render.py`: print it
as-is.

**Never pass `--yes` (or `confirm: true`) unless the user has seen that exact
preview and said yes.** These are real schedule changes at the user's job; an
accepted swap has no undo.

## Layout

| file | |
|---|---|
| `session.py` | headless Auth0 login, token refresh, the persisted session |
| `actions.py` | the operations as plain functions over a `Session` |
| `render.py` | **the only** place output is formatted |
| `dates.py` | date parsing, all in Europe/Amsterdam |
| `cli.py` | argparse surface |
| `server.py` | MCP surface over the same actions |

## Things the API does that are easy to get wrong

- The company code resolves through the discovery service's own HTML form:
  `POST https://discovery.ortec-relevance.com/v1/platform-code` with
  `platformCode=…` redirects to `…/auth0/<tenant id>/login`; an unknown code
  redirects back to `/v1/login`. The tenant's Auth0 domain, client id and
  audience then come from `GET /api/auth/authConfig?magazineId=<tenant id>`.
- Accept and decline answer 2xx with a body that is not JSON.
- Login ends with `POST /api/auth/exchange`: the callback
  redirects to `/?code=…&state=…`, and the SPA trades that code for the first
  token and the refresh cookie.
- Cookies come with `Domain=.workforce-ess.ortecapps.com`. Match on the host
  with the dot stripped.
- `/api/auth/token` rotates `ressRefreshToken` *and* the `ess.cookie.*` chunks;
  `/api/setup-user` rotates the chunks too. A spent refresh cookie gets a 500,
  not a 401.
- `/api/v2/**` needs the `ess.cookie.*` chunks as well as the bearer; without
  them it is a plain-text 401.
- The swap list filters on either shift's date, and `untilDate` is exclusive.
- `X-App-Timezone` changes nothing on reads. The offsets in the request bodies
  are what count, and they are always built in Europe/Amsterdam.

## Testing

`nix develop`, `pytest`, `ruff check`. Offline only. Do not test writes against
the live API: the user has not authorised creating real swaps or leave.
