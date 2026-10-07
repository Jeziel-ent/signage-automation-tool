# Deployment plan (Tanglish): DigitalOcean site + ovvoru designer PC-lum CorelDRAW agent

Status: PLAN. "Already done" la irukkaradhu thavira vera edhuvum build aagala. Decisions 2026-10-07 anniku project owner-oda eduthadhu.
English version: `docs/deployment-plan.md`. Rendayum onna maathanum.

## Decisions

| Topic | Decision |
|---|---|
| Scale | 5+ designer PCs, pala offices |
| CorelDRAW enga run aagum | Designer PC-la mattum, oru chinna Windows **agent** vazhiya. Browser-la irundhu COM call panna mudiyaadhu, so droplet-la CorelDRAW run aagaadhu. |
| Hosting | Oru DigitalOcean droplet (API + built React app + SQLite) + oru Spaces bucket (ella periya files-um) |
| Periya files | Spaces + presigned URLs. Browser -> Spaces (master upload), agent <-> Spaces (master download, result upload). Droplet-la metadata mattum. |
| Database | Mudhalla droplet-la SQLite. Agents database-ku direct-ah pogaadhu, API vazhiya thaan pesum, so oru writer process. API process onnukku mela venum-na mattum Postgres. |
| Login | Email + password accounts (session cookie). Agent-ku thani per-PC token (revoke panna mudiyum). |
| Masters | **Global.** Brand-ku oru shared set, ella PC-lum paakkalam, maathalam. Delete / replace admin mattum, soft delete, `master_hash` store. |
| Brands | **Global**, oru shared list. |
| Corel Intelligence corrections | **Global.** Oru table, owner filter illa. Oru designer save panna correction, adhe brand / master / size-la next ellaa boards-kum apply aagum. `created_by`, `office` provenance-ku mattum. |
| Recently generated | **Per agent (PC).** Ovvoru job-um edhu `agent_id` run pannuchu nu store pannum. Connected agent-oda jobs mattum list-la varum. |

## Already done (ippo code-la irukkaradhu)

- **Masters page** (sidebar "Masters", `frontend/src/pages/Masters.jsx`): brand select / create, appuram andha brand-oda landscape + portrait masters upload, edit, delete.
- **Oru shared registry**: Masters page-um Automation page-um `context/MasterContext.jsx` (masters + brands) read / write pannudhu. Oru page-la add panna master / brand matha page-lum udane theriyum.
- **Edit master** dialog (`components/MasterEditModal.jsx`) rendu page-lum: rename, orientation, default size, `.cdr` replace.
- **API**: `GET/POST /api/masters`, `DELETE /api/masters/{id}` (soft delete), `PATCH /api/masters/{id}` (name, orientation, default size), `PUT /api/masters/{id}/file` (file replace). Kadaisi rendu-um andha master-ah use panra shop convert aagikittu irundha 409 tharum.
- **Masters server restart-ku appuram-um irukkum.** `SIGNAGE_ARCHIVE_MASTERS_ON_START=1` pottaa pazhaya "ovvoru start-lum ellam hide" behaviour thirumba varum. Hosted site-ku venum-nadhu idhu thaan (store aagura behaviour).
- **Fresh start**: page fresh-ah load aagumbodhu Automation-lum Masters-lum brand select aagirukkaadhu (Adinn default illa). Brand select panna andha brand-ku already store aana masters varum. Masters page tab maathumbodhu brand-ah vechukkum, reload-la marandhudum. Automation-um Masters-um thaniya brand choice vechukkudhu.
- Browser pane-la check pannen (scratch backend, mock engine, vera data folder): fresh load, brand pick, backend restart-ku appuram masters irukkaradhu, Masters page-la upload panna Automation-la varudhu.
- Check pannala: real CorelDRAW file-ah puthu upload / replace path-la (dummy `.cdr` bytes mattum use pannen), multi-user (login innum illa).

## Architecture

```
Designer browser --HTTPS--> DigitalOcean droplet (FastAPI + React build, SQLite)
        |                            |  job queue, login, metadata
        | presigned upload/download  |
        v                            v
   DO Spaces  <-- presigned GET/PUT -->  Windows agent (ovvoru designer PC-la onnu)
 (masters, outputs,                       job claim pannum, corel_worker + CorelDRAW run pannum,
  scenes, thumbs)                         result upload pannum
```

`main.py`-la 3 idathula thaan CorelDRAW call aagudhu, ellame `corel_supervisor.run_batch` vazhiya: `_v2_convert_worker` (convert), `_scene_build_worker` (editor scene), `_export_worker` (editor export / publish). Matha ellame (editor ops, print sheet, ZIP, thumbnails, corrections, masters registry) pure Python / Pillow. Agent = ippo irukkura `corel_worker` + `corel_supervisor` + `corel_util` + `corel_watchdog`, in-process call-ku badhil job feed-la irundhu run aagum. Engine, layout rules, example libraries touch panna thevai illa.

## DigitalOcean-la edhu vaangalam (vilai ninaivula irundhu, pricing page-la confirm pannunga)

| Item | Plan | Maasam approx |
|---|---|---|
| Droplet | Basic, Regular SSD, 2 vCPU / 4 GB / 80 GB, Ubuntu 24.04 (start-ku 1 vCPU / 2 GB ~$12 podhum, resize easy) | ~$24 |
| Spaces | Standard (250 GB storage + 1 TB outbound included) | ~$5 |
| Droplet backups (optional) | Weekly | droplet-oda ~20% |

Total ~$30-35 / maasam. Ippo vendaam: Managed Postgres, load balancer, Kubernetes, App Platform, Block Volume (files Spaces-la). Region: users India-la irundha Bangalore (BLR1) (Spaces anga irukka nu check pannunga), illana Singapore (SGP1). Droplet + Spaces same region. Domain venum (HTTPS-ku Caddy / Let's Encrypt), Cloud Firewall-la 80/443 open, SSH theriyura IP-la irundhu mattum.

## Phases

**Phase 0 - Preparation, behaviour maaradhu**
- Ella file read / write-kum storage interface (`local` | `spaces`). Ippo code direct-ah `DATA / ...` path use pannudhu, local data ~19 GB. `POST /api/masters/upload`, `PUT /api/masters/{id}/file` ezhudhura master files-um idhula varum.
- `SIGNAGE_DATA` oru path root-ah irukkanum; `/healthz` + env/config layer.
- API-ku Dockerfile; `SIGNAGE_ENGINE=remote` so API pywin32 import pannaadhu.

**Phase 1 - Accounts + droplet (mudhalla single user)**
- `users` table, email + password, session cookie, admin seed, open sign-up off; ella `/api` route-kum login.
- Roles: `admin` (masters upload / replace / delete, brands manage, corrections disable, agents manage) + `designer` (convert, edit, export). Ippo yaar venum-naalum master delete / replace panna mudiyum, login vandhadhum adhu admin mattum aaganum.
- `jobs`, `shops`, `brands`, `corrections`-la owner / team columns; `jobs`, `shops`-la `agent_id`.
- Deploy: Caddy (HTTPS), uvicorn (`--reload` illama), Vite build static-ah. SQLite-ah daily Spaces-ku copy.

**Phase 2 - Periya files-ku Spaces**
- Browser, API kitta presigned PUT URL kekkum, master-ah direct Spaces-ku upload pannum (XHR-la progress bar work aagum). Puthu `jobs.file_key`, `master_hash` columns. Master registry SQLite-la irukkum, file mattum Spaces-ku poagum.
- Replace-file (`PUT /api/masters/{id}/file`) = "puthu file upload panni registry entry-ah maathu": hash thirumba calculate, munnadi version-ah vechukkum (version history), so thappa replace panna undo pannalam.
- Download, thumbnails, ZIP presigned GET / short-lived redirect; lifecycle: temporary ZIP 1 naal, share link 3 naal.

**Phase 3 - Droplet <-> agent job queue**
- `agent_jobs` table: kinds `convert`, `scene_export`, `export_replay`; states `queued`, `claimed`, `running`, `done`, `failed`; expire aagura lease.
- Agent API: `POST /agent/register`, `POST /agent/claim`, `POST /agent/{job}/heartbeat` (ippo `/status` kaattura step progress inga varum), `POST /agent/{job}/result`.
- `_v2_convert_worker`, `_scene_build_worker`, `_export_worker` enqueue panni return aagum; `/status` heartbeat rows-la irundhu progress padikkum.
- Ippo irukkura rules apdiye: oru agent-ku oru CorelDRAW job oru nerathula; 5 shop batching = same master-ku queued 5 jobs-ah agent claim pannum; 45 s per-shop limit, retry-alone path agent-oda local supervisor-kulla.
- "Shop convert aagum bodhu master edit panna 409" rule ippo `agent_jobs` (ella agents) vachu check aaganum, local pool mattum illa.

**Phase 4 - Windows agent**
- Service / PyInstaller exe: job claim, master download (file hash-la cache), local-la run, CDR / PDF / PNG / report Spaces-ku upload, result post.
- CorelDRAW version, free RAM, installed fonts report pannum; `/api/corel/health`-ku badhil, splash "Connect to CorelDRAW" -> "agent online-ah irukka".
- Outbound HTTPS mattum (inbound port / VPN illa); per-PC token; start-la version endpoint check panni auto-update.

**Phase 5 - Pala offices**
- Job connected agent-la run aagum (illana fonts irukkura least busy agent). Admin page: agents online / offline, current job, version, last seen.
- Agent saagirucha, lease expire aanadhum jobs fail panni re-queue (`_recover_interrupted_work`-ku badhil).

**Phase 6 - Hardening**
- Rate limits, audit log (yaar master maathinaanga / delete pannanga, yaar correction save pannanga), error reporting, uptime alerts; venum-na Postgres; Jenkins-la SonarQube-oda deploy step.

## Data scopes

| Data | Scope | Epdi |
|---|---|---|
| Masters (brand-ku, landscape + portrait) | Global | Oru registry; delete / replace admin mattum; `master_hash` so file name edhuvaa irundhaalum same master match aagum |
| Brands | Global | Oru shared list (Masters + Automation page ippove share pannudhu) |
| Page-la select panna brand | Browser tab-ku, UI mattum | Ovvoru fresh load-lum empty; server-la store aagaadhu |
| Corel Intelligence records | Global | Same brand + master + page size + board type; `created_by`, `office` provenance |
| Recently generated, editor, print sheet, ZIP | Per agent / PC | Ovvoru job-lum `agent_id`; andha agent-oda jobs-ku mattum presigned URL |

## Risks / open points

1. **Fonts + CorelDRAW version PC-ku PC maarum.** Output job run aana PC-ah depend aagum, so job agent, CorelDRAW version, fonts-ah record pannanum.
2. **Oru bad global correction ellaa offices-ayum paadhikkum.** Corrections page-la admin delete / disable button venum, learned change ethana designers-oda nu UI sollanum.
3. **Global masters:** thappa replace / delete ellarukkum paadhikkum. Soft delete (ippove irukku), admin-only maatram, Phase 2 version history idhukku guard. Venum-na office-wise brand permission.
4. **Oru office convert panra master-ah vera office edit panna:** ippo API 409 tharudhu, aana adhuku theriyura jobs-ku mattum. Agents vandha shared queue use pannanum (Phase 3).
5. **Editor + Save Changes asynchronous aagum** (replay agent-la odum); scene JSON + images Spaces-la.
6. **WeTransfer uploader** (Edge + Playwright) agent-ku move aaganum illana Spaces share link-ku maaranum, adhu easy.
7. **Tamil text CorelDRAW sessions-ku idaiyila vera vera measure aagudhu** (CLAUDE.md "Open items" paarunga); pala PC-la idhu PC-ku PC maarum.
8. **Security:** client artwork Spaces-la irukkum. Bucket private, presigned access mattum; `signage_dataset/` + designer CDRs repo-ku veliya.
9. **Open UI kelvi:** Automation page-um Masters page-um thaniya brand choice vechukkudhu. Rendum ore selection follow pannanuma nu mudivu pannanum.

## Rollout

Mudhalla agent + droplet-ah ippo irukkura local setup-oda serthu odunga, oru office move pannunga, appuram matha offices. Mudhal build step: Phase 0 + Phase 3 queue schema, matha phases ellam adha depend aagum.
