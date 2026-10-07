# Deployment plan: DigitalOcean site + a CorelDRAW agent on every designer PC

Status: PLAN (nothing below is built yet except where marked **done**). Decisions were taken with the project owner on 2026-10-07.

## Decisions

| Topic | Decision |
|---|---|
| Scale | 5+ designer PCs, several offices |
| Where CorelDRAW runs | Only on the designer PCs, through a small Windows **agent**. A browser cannot call COM, so the droplet never runs CorelDRAW. |
| Hosting | One DigitalOcean droplet (API + built React app + SQLite) and one DigitalOcean Spaces bucket (all large files) |
| Big files | Spaces with presigned URLs: browser -> Spaces for masters, agent <-> Spaces for masters and results. The droplet only holds metadata. |
| Database | SQLite on the droplet first. Agents talk to the API, never to the database, so there is one writer process. Postgres only if more than one API process is ever needed. |
| Login | Email + password accounts (session cookie). The agent has its own per-PC token, revocable. |
| Masters | **Global.** One shared set per brand, visible and editable from every PC. Admin-only delete/replace, soft delete, `master_hash` stored. **Done in the UI**: the Masters page and the Automation page share one registry, masters persist across restarts. |
| Corel Intelligence corrections | **Global.** One table, no owner filter: a correction saved by one designer applies to every later board of that brand / master / size. Provenance (`created_by`, `office`) is stored but not used to filter. |
| Recently generated | **Per agent (PC).** Each job stores the `agent_id` that ran it; the list shows only the connected agent's jobs. Not global. |

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

`main.py` calls CorelDRAW in exactly three places, all through `corel_supervisor.run_batch`: `_v2_convert_worker` (convert), `_scene_build_worker` (editor scene) and `_export_worker` (editor export / publish). Everything else (editor ops, print sheet, ZIP, thumbnails, corrections) is plain Python and Pillow. The agent is the existing `corel_worker` + `corel_supervisor` + `corel_util` + `corel_watchdog`, driven by a job feed instead of in-process calls. The engine, layout rules and example libraries are not touched.

## What to buy on DigitalOcean (prices checked from memory, confirm on the pricing page)

| Item | Plan | About / month |
|---|---|---|
| Droplet | Basic, Regular SSD, 2 vCPU / 4 GB / 80 GB, Ubuntu 24.04 (1 vCPU / 2 GB at ~$12 is enough to start; resizing is easy) | ~$24 |
| Spaces | Standard plan (250 GB storage + 1 TB outbound included) | ~$5 |
| Droplet backups (optional) | Weekly | ~20% of the droplet |

Total about $30-35 a month. Not needed now: Managed Postgres, load balancer, Kubernetes, App Platform, Block Volume (files live in Spaces). Region: Bangalore (BLR1) if the users are in India and Spaces is offered there, otherwise Singapore (SGP1); droplet and Spaces in the same region. A domain name (HTTPS through Caddy / Let's Encrypt), and a Cloud Firewall with 80/443 open and SSH only from known IPs.

## Phases

**Phase 0 - Preparation, no behaviour change**
- A storage interface (`local` | `spaces`) behind every file read and write; today code uses `DATA / ...` paths directly and the local data folder is ~19 GB.
- `SIGNAGE_DATA` stays the only path root; add `/healthz` and an env/config layer.
- Dockerfile for the API; `SIGNAGE_ENGINE=remote` so the API never imports pywin32.
- **Done**: masters are stored across restarts (`SIGNAGE_ARCHIVE_MASTERS_ON_START=1` brings the old per-session behaviour back).

**Phase 1 - Accounts and the droplet (single user first)**
- `users` table, email + password, session cookie, admin seeded, open sign-up off; every `/api` route requires a login.
- Owner / team columns on `jobs`, `shops`, `brands`, `corrections`; `agent_id` on `jobs` and `shops`.
- Deploy: Caddy (HTTPS), uvicorn without `--reload`, Vite build served as static files. Nightly SQLite copy to Spaces.

**Phase 2 - Spaces for the large files**
- The browser asks the API for a presigned PUT URL and uploads the master straight to Spaces (the progress bar keeps working through XHR). New `jobs.file_key` and `master_hash` columns.
- Downloads, thumbnails and ZIPs are served by presigned GET or short-lived redirect; lifecycle rules: temporary ZIPs expire after a day, share links after three.

**Phase 3 - Job queue between the droplet and the agents**
- `agent_jobs` table: kinds `convert`, `scene_export`, `export_replay`; states `queued`, `claimed`, `running`, `done`, `failed`; a lease that expires.
- Agent API: `POST /agent/register`, `POST /agent/claim`, `POST /agent/{job}/heartbeat` (carries the step progress `/status` shows today), `POST /agent/{job}/result`.
- `_v2_convert_worker`, `_scene_build_worker` and `_export_worker` enqueue and return; `/status` reads progress from heartbeat rows.
- Existing rules keep their meaning: one CorelDRAW job at a time per agent; batching of up to 5 shops = an agent claiming up to 5 queued jobs for the same master; the 45 s per-shop limit and the retry-alone path stay inside the agent's local supervisor.

**Phase 4 - The Windows agent**
- Packaged as a service or PyInstaller exe: claim a job, download the master (cached by file hash), run it locally, upload CDR / PDF / PNG / report to Spaces, post the result.
- Reports its CorelDRAW version, free RAM and installed fonts; replaces `/api/corel/health`, so the splash's "Connect to CorelDRAW" becomes "an agent is online".
- Outbound HTTPS only (no inbound ports, no VPN); per-PC token; auto-update by checking a version endpoint at start.

**Phase 5 - Several offices**
- Jobs run on the connected agent (or the least busy one that has the needed fonts). Admin page: agents online / offline, current job, version, last seen.
- A dead agent's jobs are failed and re-queued when the lease expires (replaces `_recover_interrupted_work`).

**Phase 6 - Hardening**
- Rate limits, audit log, error reporting, uptime alerts; Postgres only if needed; a deploy step in Jenkins next to SonarQube.

## Data scopes

| Data | Scope | How |
|---|---|---|
| Masters (per brand, landscape + portrait) | Global | One registry; admin-only delete / replace; `master_hash` so the same master matches under any file name |
| Brands | Global | Shared list (Masters page and Automation page already share it) |
| Corel Intelligence records | Global | Same brand + master + page size + board type; `created_by`, `office` kept as provenance |
| Recently generated, editor, print sheet, ZIP | Per agent / PC | `agent_id` on every job; presigned URLs only for that agent's jobs |

## Risks and open points

1. **Fonts and CorelDRAW version differ per PC.** Output depends on the PC that ran the job, so every job records its agent, CorelDRAW version and fonts; the agent reports them.
2. **One bad global correction affects every office.** Needs an admin delete / disable button on the corrections page, and the UI should say how many designers a learned change came from.
3. **Global masters:** a wrong replace or delete hits everyone. Soft delete (already in place), admin-only changes, and a version history are the guard rails. Optionally brand-level permissions per office.
4. **Editor and Save Changes become asynchronous** (the replay runs on an agent); scene JSON and images live in Spaces.
5. **WeTransfer uploader** (Edge + Playwright) moves to the agent or is replaced by a Spaces share link, which does the same job more simply.
6. **Tamil text measures differently between CorelDRAW sessions** (see CLAUDE.md "Open items"); with several PCs this can differ per PC.
7. **Security:** client artwork sits in Spaces. Keep the bucket private with presigned access only; keep `signage_dataset/` and designer CDRs outside the repo.

## Rollout

Run the agent and droplet beside the current local setup first, move one office, then the rest. Suggested first build step: Phase 0 plus the Phase 3 queue schema, because the other phases depend on them.
