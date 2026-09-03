# PCO MCP Server

Standalone MCP server exposing the PCO song-tracking sync as MCP tools
and a resource, instead of the in-process `@tool` functions used in
`agent-demo/sdk_loop.py`. Built as Waypoint-02 (Stage 4) of the AI
tools roadmap — see the root README for the project as a whole.

Status: **Phases 1–2 complete and verified. Phases 3–4 not yet built.**

| Phase | What                                                | Status                                                                                                                        |
| ----- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 1     | stdio → streamable HTTP transport                   | ✅ Done, verified locally with MCP Inspector                                                                                  |
| 2     | Deploy to Fly.io                                    | ✅ Done — see [Deploying](#deploying)                                                                                         |
| 3     | OAuth (bearer token auth)                           | ⬜ Not started. The deployed server is currently **unauthenticated** — do not leave it running unattended until this is done. |
| 4     | Confirm a non-local client can discover and call it | ⬜ Not started. Depends on Phase 3.                                                                                           |

---

## Technical: running it

### Phase 1 — local, streamable HTTP

```bash
export $(grep -v '^#' .env.mcp-test | xargs)
venv/bin/python mcp/pco_server.py
```

Starts an HTTP server on `http://127.0.0.1:8000/mcp`. No output on
success — it just sits there serving.

Verify with the MCP Inspector instead of `curl`, since a bare GET
won't complete a real MCP handshake:

```bash
npx @modelcontextprotocol/inspector
```

In the UI, choose **Streamable HTTP**, enter `http://127.0.0.1:8000/mcp`,
connect. You should see 4 tools (`get_this_weeks_songs`,
`get_existing_songs`, `append_date_to_song`, `create_song_entry`) and
1 resource (`sheet://tracking/current`).

**Always point this at the Test Song Bank spreadsheet
(`.env.mcp-test`), never production**, until Phase 3 auth is in place.
`song_tools.py`'s `load_dotenv()` only loads a file literally named
`.env` — exporting `.env.mcp-test` into the shell first is what makes
it take precedence, per `load_dotenv()`'s `override=False` default.

### Phase 2 — deploying

Files: `Dockerfile`, `entrypoint.sh`, `fly.toml`, `.dockerignore`, all
at the project root.

One-time setup:

```bash
fly launch --no-deploy
```

Confirm the app name Fly assigns matches `fly.toml`'s `app =` line —
`pco_server.py` builds its Host allowlist from `FLY_APP_NAME`, which
Fly sets automatically at runtime, so a mismatch here means every
request gets rejected with `421 Misdirected Request`.

```bash
fly secrets set \
  PCO_CLIENT_ID=... \
  PCO_SECRET=... \
  PCO_SERVICE_TYPE_ID=... \
  SPREADSHEET_ID=<Test Song Bank spreadsheet ID> \
  WORKSHEET_NAME=mcp-test

fly secrets set GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service_account.json)"
```

Deploy / redeploy after any code change:

```bash
fly deploy
```

Check it booted clean:

```bash
fly logs
```

Looking for the server starting with no traceback after it — same
signal as every local run throughout this project.

**One log line that looks like a crash but isn't:** when the machine
auto-stops from idle (`min_machines_running = 0` doing its job), Fly
sends `SIGINT`, uvicorn shuts down cleanly, and asyncio's own shutdown
handling logs a `KeyboardInterrupt` traceback as part of that normal
sequence. If you see a clean startup line, some successful requests,
_then_ this traceback several minutes later with no request in
between — that's a scheduled idle shutdown, not a failure. A real
crash shows the traceback immediately after startup, with no
successful requests logged first.

#### Verifying it with the MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

In the UI: transport type **Streamable HTTP**, URL:

```
https://<your-app-name>.fly.dev/mcp
```

Get `<your-app-name>` from `fly status` — it matches `fly.toml`'s
`app =` line.

Two mistakes that look like the server is broken but aren't:

- **Don't use `http://0.0.0.0:8080`.** That's the address the
  container binds _inside itself_ — it's not reachable from your
  laptop, deployed or local. Always connect to the real
  `https://<app-name>.fly.dev` hostname.
- **Always `https://`, never `http://`.** `fly.toml` sets
  `force_https = true`, so plain HTTP gets rejected or redirected.

First connection after idle will take a few seconds — that's
`auto_start_machines` cold-starting the stopped machine, not a hang.

Stop it when you're not actively testing (no auth yet, see the status
table above):

```bash
fly apps stop pco-mcp-server
```

(swap in your actual app name.) `fly deploy` or `fly apps start` brings it back.

---

## Operating this POC

This section is for whoever is running test syncs against the
deployed server — right now, that's just you, manually, not an
automated cron job. (The production weekly sync still runs through
`pco_script.py` via GitHub Actions, untouched by any of this.)

**Before every test run, confirm which sheet you're pointed at.**
Everything in this project is built around the assumption that MCP
testing never touches the real Song Bank. If you're not sure, stop
and check `SPREADSHEET_ID` / `WORKSHEET_NAME` in whatever `.env` file
or Fly secrets are active before running anything that writes.

**To run a test sync against the deployed server** (once Phase 4
wires a real remote client up to it — not yet available):

1. Confirm the server is running: `fly status`.
2. Trigger the sync from whatever client Phase 4 sets up.
3. Check the result: either read `sheet://tracking/current` via the
   Inspector, or open the Test Song Bank sheet directly and eyeball
   the row that should have changed.
4. Compare against what `pco_script.py` would have produced for the
   same input — that's the project's standing ground-truth check.

**If something looks wrong:** `fly logs` first, same as every local
debugging session in this project — the failure signal is almost
always in there (a traceback, a permission error, a 421). Nothing
about deploying to Fly changes how you diagnose it, only where the
logs live.

**Known gap:** until Phase 3 ships, this server has no auth. Treat the
deployed URL as something only you should know, keep it stopped
between test sessions, and do not point any client at it that isn't
one you're personally running.
