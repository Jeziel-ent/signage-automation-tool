# Deploying the website on a DigitalOcean droplet

The site runs in Docker on one Linux droplet; CorelDRAW stays on the designers' Windows PCs (the agent). Files: `Dockerfile`, `docker-compose.yml`,
`deploy/Caddyfile`, `deploy/server.env.example`, `backend/requirements-server.txt`, `backend/tools/backup_db.py`.

Verified without Docker: the server imports and answers with every Windows-only library blocked (`backend/tests/test_server_on_linux.py`), the
backup tool, and the agent flow against a remote-mode site. **Not yet verified: `docker build` / `docker compose up`** (Docker Desktop was not
running on the development PC) - run the first build on the droplet (or any Linux/WSL box) and read its output before relying on it.

## What to create

| Item | Choice |
|---|---|
| Droplet | Basic, Regular SSD, Ubuntu 24.04, **Singapore (SGP1)** (the existing Spaces bucket `adinn-space` is there), 1 vCPU / 2 GB / 50 GB to start (~$12), resize to 2 vCPU / 4 GB if needed |
| Firewall | Cloud Firewall: 80 and 443 open to everyone, 22 only from your own IPs |
| Domain | an A record (e.g. `signage.yourdomain.com`) -> the droplet's IP |

## First install (about 15 minutes)

```bash
ssh root@<droplet-ip>
curl -fsSL https://get.docker.com | sh                 # Docker + the compose plugin
git clone <your repository url> signage && cd signage
cp deploy/server.env.example deploy/server.env
nano deploy/server.env                                  # domain, public address, the first administrator
mkdir agent-dist                                        # the agent build goes here (below)
docker compose up -d --build
docker compose ps                                       # app, caddy, backup should be Up (app: healthy)
curl https://signage.yourdomain.com/healthz             # {"ok": true, "engine": "remote", ...}
```

The Python libraries are installed by the image build from `backend/requirements-server.txt` (nothing is pip-installed on the droplet by hand).
Check them with `docker compose exec app python -c "import fastapi, uvicorn, PIL, psutil, boto3; print('ok')"`; after changing that file run
`docker compose up -d --build` again.

Open the domain, **Connect Agent**, sign in with the administrator from `server.env`, add the designers on the Users page (or
`docker compose exec app python tools/create_user.py designer@adinn.com`).

## Publishing the agent

Build it on a Windows PC (`cd agent && ..\.venv\Scripts\python.exe build.py`), then copy `agent\dist\SignageAgent.exe` and `agent\dist\latest.json`
to `agent-dist/` on the droplet (`scp`). Designers download it from the site; running agents update themselves at their next check. Raise
`agent/signage_agent/version.py` for every release; `build.py --min-supported X` makes older agents update at once.

## Updating the site

```bash
cd signage && sh deploy/deploy.sh      # git pull, rebuild, restart, then checks /healthz
```

## Backups

The app backs the database up itself at **12 am, 6 am, 12 pm and 6 pm India time** (`SIGNAGE_BACKUP_HOURS=0,6,12,18`, `SIGNAGE_BACKUP_TZ`),
into `/data/backups` of the `signage-data` volume, checks each copy, deletes copies older than **7 days** (`SIGNAGE_BACKUP_RETENTION_DAYS`) and posts the result
to Google Chat. The database holds Recently generated, users and masters, so one backup covers all three; the masters' and boards' FILES are never deleted.
Set `SIGNAGE_ALERT_WEBHOOK` in `deploy/server.env` to the space's webhook (Google Chat: space > Apps & integrations > Webhooks) - the same
webhook also receives server alerts (database / disk problems, designers waiting, unhandled errors). Off with `SIGNAGE_BACKUP_SCHEDULE=0`.
When `SIGNAGE_SPACES_KEY/SECRET/BUCKET` are set, each backup is also uploaded to Spaces under `signage-automation/database-backups/<date>/` (another bucket: `SIGNAGE_SPACES_BACKUP_BUCKET`), copies older than 7 days are removed there too, and `python tools/spaces_layout.py` (run inside the app container) creates the empty `signage-automation/` folders (masters, generated-boards, recently-generated, editor-exports, users, corrections, agent-downloads, shared-links, logs). Only backups are uploaded so far; the other folders are where masters and boards will go when they move off the droplet's disk. Copy backups off the droplet too (weekly droplet backups in the DigitalOcean panel, or `docker compose cp app:/data/backups ./`).
Restore: `docker compose stop app`, copy a backup over `/data/signage.db` in the volume (delete `signage.db-wal` / `-shm` next to it),
`docker compose start app`.

## Running it safely (built in, all optional settings in `deploy/server.env`)

- **Admin page** (administrators only, open `https://<your domain>/admin` - it is not in the sidebar; anyone else is sent to the home page): server health (CPU, memory, disk, queue), every designer with their usage (sign-ins, boards, exports, failures, PCs online), create / disable / delete a user, Spaces storage used, problems, disk use, the activity log and "Clean up old files".
- **Spaces usage** on that page: set `SIGNAGE_SPACES_KEY`, `SIGNAGE_SPACES_SECRET`, `SIGNAGE_SPACES_BUCKET` (+ `SIGNAGE_SPACES_REGION`, default `sgp1`, and `SIGNAGE_SPACES_QUOTA_GB` for a percentage); it only reads the bucket, counted at most every 10 minutes.
- **Alerts**: `SIGNAGE_ALERT_WEBHOOK=<Google Chat / Slack / Teams / Discord incoming-webhook URL>` posts when the database or disk is in trouble, a designer waits too long, or the server raises an unhandled error (one message per problem per hour).
- **Clean-up**: temporary files (>1 day) and debug captures (>14 days) always; finished boards older than `SIGNAGE_RETENTION_DAYS` (unset = keep forever). Masters, brands and the database are never removed.
- **Activity log** kept `SIGNAGE_AUDIT_DAYS` (default 365).
- **Rate limits** per signed-in user / address: `SIGNAGE_RATE_LIMIT_PER_MIN` (1500), `..._UPLOADS_PER_MIN` (90), `..._AGENT_PER_MIN` (3000), `..._PAIRING_PER_MIN` (30).
- **Restore**: `python tools/restore_db.py <backup>` inside the app container (checks the backup first, keeps the old database beside it).
- **Links to ZIPs**: on the hosted server the "WeTransfer" button becomes "Create Share Link" (a link served by the site, kept `SIGNAGE_SHARE_DAYS`, default 3) - no Edge or e-mail code needed.
- **Signing the agent** (when you own a certificate): `python agent/build.py --sign` with `SIGNAGE_SIGN_PFX`, `SIGNAGE_SIGN_PASSWORD`; removes the Windows SmartScreen warning.

## Still open

- Masters and boards live on the droplet's disk (volume `signage-data`). Moving them to Spaces (presigned uploads, so the droplet never carries
  a 300 MB upload) needs the bucket's access keys and CORS rule - see the storage item in `docs/deployment-plan.md`.
- `docker build` / `docker compose up` have not been run yet (see the top of this file).
