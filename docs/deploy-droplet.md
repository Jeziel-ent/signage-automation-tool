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

Open the domain, **Connect Agent**, sign in with the administrator from `server.env`, add the designers on the Users page (or
`docker compose exec app python tools/create_user.py designer@adinn.com`).

## Publishing the agent

Build it on a Windows PC (`cd agent && ..\.venv\Scripts\python.exe build.py`), then copy `agent\dist\SignageAgent.exe` and `agent\dist\latest.json`
to `agent-dist/` on the droplet (`scp`). Designers download it from the site; running agents update themselves at their next check. Raise
`agent/signage_agent/version.py` for every release; `build.py --min-supported X` makes older agents update at once.

## Updating the site

```bash
cd signage && git pull && docker compose up -d --build
```

## Backups

The `backup` service writes a consistent copy of the database to the `signage-data` volume (`/data/backups`) every 24 hours and keeps the newest 14.
Copy them off the droplet too (weekly droplet backups in the DigitalOcean panel, or `docker compose cp backup:/data/backups ./`).
Restore: `docker compose stop app`, copy a backup over `/data/signage.db` in the volume (delete `signage.db-wal` / `-shm` next to it),
`docker compose start app`.

## Still open

- Masters and boards live on the droplet's disk (volume `signage-data`). Moving them to Spaces (presigned uploads, so the droplet never carries
  a 300 MB upload) needs the bucket's access keys and CORS rule - see the storage item in `docs/deployment-plan.md`.
- No file cleanup yet: `/healthz` turns 503 when less than `SIGNAGE_MIN_FREE_DISK_GB` is left; watch it with an uptime monitor.
- The WeTransfer button needs Edge on a Windows PC and does not work from the droplet (the server-link fallback does).
