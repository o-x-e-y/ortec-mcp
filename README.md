# ortec

A CLI and an MCP server for ORTEC Employee Self Service
(`workforce-ess.ortecapps.com`): your shifts, shift swaps and time off, without
the web app.

```sh
ortec shifts                              # next two weeks
ortec swaps                               # swap requests, incoming and outgoing
ortec swap-candidates 2026-10-28          # who can swap my shift that day
ortec swap-request 2026-10-28 tess        # preview; add --yes to send
ortec swap-accept 2026-10-28              # preview; add --yes to send
ortec leave 2026-11-10 2026-11-12         # preview; add --yes to send
ortec working-with                        # who you work with on your shifts, 7 days from tomorrow
```

`ortec --help` documents every command.

## Writes are previews until confirmed

`swap-request`, `swap-accept`, `swap-decline` and `leave` print the request
they would send and stop. `--yes` (CLI) or `confirm: true` (MCP) sends it. A
confirmed leave request runs the server's balance check first and stops if it
fails, as the web app does.

## Login

Three things to set, the same three the ORTEC app asks for:

| variable | |
|---|---|
| `ORTEC_COMPANY` | the company code you enter in the app, e.g. `ACME1` |
| `ORTEC_USER` | your login e-mail address |
| `ORTEC_PASS` | your password |

The company code is looked up once, the way the app does it, and gives the
employer's ESS tenant; the tenant's own login settings come from the server.
Only employers on `workforce-ess.ortecapps.com` are supported, and it has
been tested with one employer.

It logs in by itself through the same Auth0 form the web app uses, and keeps
the session in
`~/.cache/ortec/session.json` (mode 0600). The refresh cookie is single-use, so
the file is rewritten after every call; a spent or broken session falls back to
a fresh login (about 11 requests, a few seconds).

The credentials can also come from files named by `$ORTEC_USER_FILE` /
`$ORTEC_PASS_FILE` (for sops-nix). They are never
written to disk. If the account ever gets MFA, login fails with a message
saying so.

## MCP server

`ortec-mcp` exposes the same operations as tools: `shifts`, `swap_requests`,
`swap_candidates`, `working_with`, `request_swap`, `respond_to_swap`, `leave_types`,
`leave_requests`, `request_leave`. The three writes are annotated destructive
and default to a preview.

```sh
ortec-mcp                                            # stdio, for Claude Code
ortec-mcp --http --port 8765 --path /mcp/<secret>    # streamable HTTP
```

To reach it from the Claude apps (phone included) it has to be on a public
HTTPS URL, added as a custom connector. Claude.ai cannot send a static
bearer token, so the path is the secret: pick a long random one. Behind a
tunnel, pass the public hostname with `--allowed-host`, or the server answers
`421`.

## Nix

The flake exports `packages.default`, `overlays.default` (`pkgs.ortec-cli`) and
`homeManagerModules.default`:

```nix
programs.ortec = {
  enable = true;
  company = "ACME1";
  userFile = "/run/secrets/ortec-email";
  passwordFile = "/run/secrets/ortec-password";
};
```

## Development

`nix develop`, then `pytest` and `ruff check`. The suite is offline: the
actions run against `tests/conftest.py`'s fake session, the login chain against
an `httpx.MockTransport` tenant. Payload shapes were recorded from the live API
on 2026-10-27, with names and ids replaced.
