# Deployment plan: DigitalOcean site + a CorelDRAW agent on every designer PC

Status: PLAN. Nothing below is built except the items under "Already done". Decisions taken with the project owner on 2026-10-07.
A Tanglish copy of this plan is in `docs/deployment-plan.tanglish.md`; keep the two in step.

## Decisions

| Topic | Decision |
|---|---|
| Scale | 5+ designer PCs, several offices |
| Where CorelDRAW runs | Only on the designer PCs, through a small Windows **agent**. A browser cannot call COM, so the droplet never runs CorelDRAW. |
| Hosting | One DigitalOcean droplet (API + built React app + SQLite) and one DigitalOcean Spaces bucket (all large files) |
| Big files | Spaces with presigned URLs: browser -> Spaces for masters, agent <-> Spaces for masters and results. The droplet only holds metadata. |
| Database | SQLite on the droplet first. Agents talk to the API, never to the database, so there is one writer process. Postgres only if more than one API process is ever needed. |
| Login | Email + password accounts (session cookie). The agent has its own per-PC token, revocable. |
| Masters | **Global.** One shared set per brand, visible and editable from every PC. Admin-only delete / replace, soft delete, `master_hash` stored. |
| Brands | **Global**, one shared list. |
| Corel Intelligence corrections | **Global.** One table, no owner filter: a correction saved by one designer applies to every later board of that brand / master / size. `created_by` and `office` are stored as provenance only. |
| Recently generated | **Per agent (PC).** Each job stores the `agent_id` that ran it; the list shows only the connected agent's jobs. |

## Already done (in the code today)

- **Masters page** (sidebar "Masters", `frontend/src/pages/Masters.jsx`): pick or create a brand, then upload, edit and delete its landscape and portrait masters.
- **One shared registry**: the Masters page and the Automation page both read and write `context/MasterContext.jsx` (masters and brands), so a master or brand added on one page shows on the other straight away.
- **Edit master** dialog (`components/MasterEditModal.jsx`) on both pages: rename, orientation, default size, replace the `.cdr`.
- **API**: `GET/POST /api/masters`, `DELETE /api/masters/{id}` (soft delete), `PATCH /api/masters/{id}` (name, orientation, default size), `PUT /api/masters/{id}/file` (replace the file). The last two answer 409 while a shop using that master is converting.
- **Masters are stored across server restarts.** `SIGNAGE_ARCHIVE_MASTERS_ON_START=1` brings back the old "hide everything on every start" behaviour. This is the behaviour the hosted site needs.
- **Fresh start**: on a fresh page load both the Automation page and the Masters page have no brand selected (no Adinn default); picking a brand shows the masters already stored for it. The Masters page keeps its brand while you move between pages and forgets it on reload; the Automation page and the Masters page each keep their own brand choice.
- Checked in the browser pane against a scratch backend (mock engine, separate data folder): fresh load, brand pick, masters persisting across a backend restart, upload on the Masters page appearing on the Automation page.
- Not checked: a real CorelDRAW file through the new upload / replace path (only dummy `.cdr` bytes), and a multi-user situation with real concurrent designers.
- **Security gaps closed (2026-11-07), `backend/app/auth.py`:** every `/api` route needs a login (cookie session, scrypt password hashes, roles `admin` / `designer`; `/api/health` and `/api/shared/*` links stay public); admin-only: delete / edit / replace a master, delete a correction, manage users (`/api/users`, Users page); login throttle (5 failures -> 5 min); CORS closed unless `SIGNAGE_CORS_ORIGINS` lists origins (was `*`); upload limit `SIGNAGE_MAX_UPLOAD_MB` (default 1024, 413). First admin: `SIGNAGE_ADMIN_EMAIL` + `SIGNAGE_ADMIN_PASSWORD`, or the setup form from the server itself. `SIGNAGE_AUTH=off` = local development only. Still open: the agent token (Phase 3-4), rate limits beyond login, audit log. The UI hides the admin-only buttons (master edit / delete, correction delete, Users page) from designers; the server enforces it regardless.
- **Server basics (2026-11-07), `backend/app/main.py`:** the API serves the built web app (`frontend/dist`, or `SIGNAGE_FRONTEND_DIST`) with a single-page-app fallback, so one process is enough behind Caddy; `GET /healthz` (public: database, data folder writable, free disk; 503 when a check fails) for Docker / uptime monitors; `SIGNAGE_REQUIRE_REAL_ENGINE=1` refuses to start on the mock engine (`auto` silently falls back to it without Windows + CorelDRAW). Still open for M0: storage interface (local | spaces), `SIGNAGE_ENGINE=remote`, Dockerfile, data retention / cleanup of `SIGNAGE_DATA` (21 GB here).

## Architecture

```
Designer browser --HTTPS--> DigitalOcean droplet (FastAPI + React build, SQLite)
        |                            |  job queue, login, metadata
        | presigned upload/download  |
        v                            v
   DO Spaces  <-- presigned GET/PUT -->  Windows agent (one per designer PC)
 (masters, outputs,                       claims jobs, runs corel_worker + CorelDRAW,
  scenes, thumbs)                         uploads results
```

`main.py` calls CorelDRAW in exactly three places, all through `corel_supervisor.run_batch`: `_v2_convert_worker` (convert), `_scene_build_worker` (editor scene) and `_export_worker` (editor export / publish). Everything else (editor ops, print sheet, ZIP, thumbnails, corrections, the masters registry) is plain Python and Pillow. The agent is the existing `corel_worker` + `corel_supervisor` + `corel_util` + `corel_watchdog`, driven by a job feed instead of in-process calls. The engine, layout rules and example libraries are not touched.

## What to buy on DigitalOcean (prices from memory, confirm on the pricing page)

| Item | Plan | About / month |
|---|---|---|
| Droplet | Basic, Regular SSD, 2 vCPU / 4 GB / 80 GB, Ubuntu 24.04 (1 vCPU / 2 GB at ~$12 is enough to start; resizing is easy) | ~$24 |
| Spaces | Standard plan (250 GB storage + 1 TB outbound included) | ~$5 |
| Droplet backups (optional) | Weekly | ~20% of the droplet |

Total about $30-35 a month. Not needed now: Managed Postgres, load balancer, Kubernetes, App Platform, Block Volume (files live in Spaces). Region: Bangalore (BLR1) if the users are in India and Spaces is offered there, otherwise Singapore (SGP1); droplet and Spaces in the same region. A domain name (HTTPS through Caddy / Let's Encrypt), and a Cloud Firewall with 80/443 open and SSH only from known IPs.

## Phases

**Phase 0 - Preparation, no behaviour change**
- A storage interface (`local` | `spaces`) behind every file read and write; today code uses `DATA / ...` paths directly and the local data folder is ~19 GB. This includes the master files that `POST /api/masters/upload` and `PUT /api/masters/{id}/file` write.
- `SIGNAGE_DATA` stays the only path root; add `/healthz` and an env/config layer.
- Dockerfile for the API; `SIGNAGE_ENGINE=remote` so the API never imports pywin32.

**Phase 1 - Accounts and the droplet (single user first)**
- `users` table, email + password, session cookie, admin seeded, open sign-up off; every `/api` route requires a login.
- Roles: `admin` (upload / replace / delete masters, manage brands, disable corrections, manage agents) and `designer` (convert, edit, export). Today any user can delete or replace a master; that must become admin-only once logins exist.
- Owner / team columns on `jobs`, `shops`, `brands`, `corrections`; `agent_id` on `jobs` and `shops`.
- Deploy: Caddy (HTTPS), uvicorn without `--reload`, Vite build served as static files. Nightly SQLite copy to Spaces.

**Phase 2 - Spaces for the large files**
- The browser asks the API for a presigned PUT URL and uploads the master straight to Spaces (the progress bar keeps working through XHR). New `jobs.file_key` and `master_hash` columns. The master registry stays in SQLite; only the file moves.
- Replace-file (`PUT /api/masters/{id}/file`) becomes "upload the new file, then switch the registry entry": the hash is recomputed and the previous version is kept (version history) so a wrong replace can be undone.
- Downloads, thumbnails and ZIPs are served by presigned GET or short-lived redirect; lifecycle rules: temporary ZIPs expire after a day, share links after three.

**Phase 3 - Job queue between the droplet and the agents**
- `agent_jobs` table: kinds `convert`, `scene_export`, `export_replay`; states `queued`, `claimed`, `running`, `done`, `failed`; a lease that expires.
- Agent API: `POST /agent/register`, `POST /agent/claim`, `POST /agent/{job}/heartbeat` (carries the step progress `/status` shows today), `POST /agent/{job}/result`.
- `_v2_convert_worker`, `_scene_build_worker` and `_export_worker` enqueue and return; `/status` reads progress from heartbeat rows.
- Existing rules keep their meaning: one CorelDRAW job at a time per agent; batching of up to 5 shops = an agent claiming up to 5 queued jobs for the same master; the 45 s per-shop limit and the retry-alone path stay inside the agent's local supervisor.
- The "409 while a shop using this master is converting" rule for master edits is evaluated against `agent_jobs` (any agent), not only the local pool.

**Phase 4 - The Windows agent** (first part BUILT 2026-11-07: `agent/` + `backend/app/agents.py`; job running below is still to do)
- Built and verified live (exe against a scratch site, browser pane): `SignageAgent.exe` (PyInstaller, ~7 MB, `python agent/build.py`); the website offers it at `/api/agent/download` (a zip: exe + a `config.json` naming that site + README); double-clicking the exe installs it for the Windows user (`%LOCALAPPDATA%\AdinnSignageAgent`, start-with-Windows Run key, no admin rights); a 127.0.0.1 status server (ports 47890-47899, CORS only for the site it came from, Private-Network-Access header, Host check) lets the page DETECT it; the page offers "Connect this PC" (one-time pairing code -> per-PC revocable token); heartbeat every 15 s reports version + the CorelDRAW installs; the Agent page and a banner recommend the download when none is found (`SIGNAGE_AGENT_REQUIRED=1`, or automatically when the server has no real CorelDRAW engine); self-update (version check, SHA-256, swap while idle, restart, rollback + skip-that-version if the new exe cannot reach the site) was exercised for real 0.1.0 -> 0.1.1.
- **Convert jobs through the agent (BUILT 2026-11-08, verified with the real exe + a remote-mode site + the mock engine; NOT yet run with real CorelDRAW through the agent):** `SIGNAGE_ENGINE=remote` makes `POST /api/v2/shops/{id}/convert` queue a job (table `agent_jobs`: queued -> claimed -> running -> done/failed, a 120 s lease the agent renews, 3 attempts, expiry loop every 15 s, claims atomic via BEGIN IMMEDIATE) for the signed-in user's OWN agent; `/api/agent/jobs/claim|{id}/master|progress|files/{name}|result` (agent token auth); results are stored exactly like a local conversion (names relative to the shop's output folder), so downloads, previews, ZIP and the print sheet work unchanged; the queue survives a server restart; a queued shop shows "Waiting for agent" when none of the owner's agents is online. The agent (`signage_agent/jobs.py`) claims up to 5, caches masters (15 kept), runs the website's engine code through `corel_supervisor.run_batch` (bundled into the exe, which re-launches itself as `--corel-worker`), retries a failed batch member alone, pumps progress + keep-alives, uploads and reports. SQLite is in write-ahead mode.
- **Editor through the agent (BUILT 2026-11-08, agent 0.2.0; VERIFIED LIVE with the real exe + real CorelDRAW 27: convert -> scene build (21 images) -> export with one edit, replay verified 22 objects / 0 mismatches):** two more job kinds. `scene_build` (input: the board's .cdr; the agent zips the scene folder, scene.json last, the site unpacks it safely) and `export` (inputs: .cdr, scene.json, the designer's product images; options + edits travel in the job; results stored as export files and report). A round claims one kind (converts batch, scene / export go alone); an agent says what it can do (`kinds`), so an older agent is never handed work it cannot do. Jobs go to the board's owner (whoever converted it). Corel Intelligence chains through the queue (convert -> scene build -> `_learned_nested_finish` saves the learned ops and queues the publish export). Exports and scene builds survive a server restart and a dead agent (lease expiry requeues, then fails after 3 tries).
- Not built yet: per-agent "Recently generated", master hash / CorelDRAW-version check, Spaces, tray icon, code-signing of the exe (Windows SmartScreen warns for an unsigned exe), an installer UI, upload of results to Spaces. Release flow: raise `signage_agent/version.py`, `python agent/build.py [--min-supported X]`, copy `agent/dist` to the server (`SIGNAGE_AGENT_DIST`); agents update themselves at their next check (a PC below `min_supported` is told at its next heartbeat).
- On the server set `SIGNAGE_PUBLIC_BASE_URL=https://<site>` (the zip's config.json and the agent's allowed origin come from it; behind a proxy that rewrites Host it must be set).
- Packaged as a service or PyInstaller exe: claim a job, download the master (cached by file hash), run it locally, upload CDR / PDF / PNG / report to Spaces, post the result.
- Reports its CorelDRAW version, free RAM and installed fonts; replaces `/api/corel/health`, so the splash's "Connect to CorelDRAW" becomes "an agent is online".
- Outbound HTTPS only (no inbound ports, no VPN); per-PC token; auto-update by checking a version endpoint at start.

**Phase 5 - Several offices**
- Jobs run on the connected agent (or the least busy one that has the needed fonts). Admin page: agents online / offline, current job, version, last seen.
- A dead agent's jobs are failed and re-queued when the lease expires (replaces `_recover_interrupted_work`).

**Phase 6 - Hardening**
- Rate limits, audit log (who changed or deleted a master, who saved a correction), error reporting, uptime alerts; Postgres only if needed; a deploy step in Jenkins next to SonarQube.

## CorelDRAW version per designer PC, and the agent updating itself (decided 2026-11-07)

**Principle: the product adapts to the CorelDRAW the designer already has; nothing forces a version.**

What exists today (`corel_util.py`): no version is hard-coded. `progid_candidates()` reads the registry and tries the generic ProgID, then the installed numbered ones newest first, falling back to the next when one cannot start; `SIGNAGE_COREL_PROGID` pins one (an override for diagnosis only, never set it on a designer PC). `corel_installs()` reports each install's version and `check_file_not_newer()` refuses a master saved by a newer CorelDRAW than the running one with a plain message. Exports default to CDR v21, which any CorelDRAW from 2019 on can open.

What the agent adds (Phase 4):
- **Report, don't assume.** Register + every heartbeat send `{installs: [{progid, version, major}], selected, free_ram_gb, fonts_hash, agent_version}`; each job stores the agent and the CorelDRAW version that ran it (risk 1 above).
- **Check before converting, not after.** `cdr_core_version()` needs no CorelDRAW, so the API reads a master's `CoreVersion` at upload (new `jobs.core_version`) and the UI compares it with the connected agent's `major`: "This master needs CorelDRAW 25 or newer; your PC has 21" appears next to the master, and Convert is disabled for it. Today this only shows after a failed run.
- **Pick per file.** When a PC has several installs, the agent uses the oldest install that can open the master only if that one is faster for it (measured per master hash, since 2019 opens dalmia files in 43 s and 27 in 130-340 s), else the newest; this replaces the global "2019 vs 27" default (open item 4).
- **Output version follows the PC.** CDR export version = min(21, the install's major), so the designer can always open what comes back.

Self-update (agent only; the website updates by deploying the server, designers just reload). **Owner decision 2026-11-07: the agent updates itself automatically; IT does not install each version by hand** (IT installs it once per PC):
- `GET /agent/version` -> `{latest, min_supported, url, sha256}`. The agent checks at start and every few hours.
- It downloads to a temp folder, verifies the SHA-256 (code-signing of the installer later), and installs ONLY when idle: no job claimed, no CorelDRAW job running. A small updater process swaps the files, restarts the Windows service, runs a self-test (register + heartbeat); on failure it restores the previous version and reports it.
- `min_supported` is enforced by the server: an older agent is refused new jobs and shows "updating..." until it has updated, so a breaking API change never meets an old agent.
- Needs no inbound port, no admin rights per update (per-user install folder), and a manual "update now" in the agent tray menu.
- The web app shows the agent version and whether it is current on the admin Agents page (Phase 5).

## Data scopes

| Data | Scope | How |
|---|---|---|
| Masters (per brand, landscape + portrait) | Global | One registry; admin-only delete / replace; `master_hash` so the same master matches under any file name |
| Brands | Global | One shared list (the Masters and Automation pages already share it) |
| Which brand a page has selected | Per browser tab, UI only | Empty on every fresh load; not stored on the server |
| Corel Intelligence records | Global | Same brand + master + page size + board type; `created_by`, `office` kept as provenance |
| Recently generated, editor, print sheet, ZIP | Per agent / PC | `agent_id` on every job; presigned URLs only for that agent's jobs |

## Risks and open points

1. **Fonts and CorelDRAW version differ per PC.** Output depends on the PC that ran the job, so every job records its agent, CorelDRAW version and fonts; the agent reports them.
2. **One bad global correction affects every office.** Needs an admin delete / disable button on the corrections page, and the UI should say how many designers a learned change came from.
3. **Global masters:** a wrong replace or delete hits everyone. Soft delete (already in place), admin-only changes and the version history in Phase 2 are the guard rails. Optionally brand-level permissions per office.
4. **Editing a master while another office converts from it:** today the API refuses with 409 only for jobs it knows about; with agents the check must use the shared queue (Phase 3).
5. **Editor and Save Changes become asynchronous** (the replay runs on an agent); scene JSON and images live in Spaces.
6. **WeTransfer uploader** (Edge + Playwright) moves to the agent or is replaced by a Spaces share link, which does the same job more simply.
7. **Tamil text measures differently between CorelDRAW sessions** (see CLAUDE.md "Open items"); with several PCs this can differ per PC.
8. **Security:** client artwork sits in Spaces. Keep the bucket private with presigned access only; keep `signage_dataset/` and designer CDRs outside the repo.
9. **Open UI question:** the Automation page and the Masters page each keep their own selected brand. Decide whether they should follow one shared selection.

## Rollout

Run the agent and droplet beside the current local setup first, move one office, then the rest. Suggested first build step: Phase 0 plus the Phase 3 queue schema, because the other phases depend on them.
