# signage-automation-tool

Designer flow: upload master `.cdr` → pick brand → add shops with W × H → **Generate** →
per-shop `.cdr`, print PDF, PNG preview and an object position/size report.

## Structure

- `backend/` FastAPI. `app/layout.py` = pure layout rules (tested). `app/engines.py` = `CorelEngine` (COM, Windows) and `MockEngine` (dev).
- `frontend/` React + Vite.

## Run (dev)

```
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api)
```

Engine: `SIGNAGE_ENGINE=auto|corel|mock` (auto = Corel on Windows, mock elsewhere).
The mock engine ignores your real file and uses a demo scene, so you can test the UI anywhere.

## Real output (Windows + CorelDRAW)

Run the backend on a Windows PC with CorelDRAW installed and `pip install pywin32`.
Jobs run one at a time because CorelDRAW is a single desktop instance.
The COM calls in `CorelEngine` are written from the CorelDRAW object model but have NOT been run
against a real CorelDRAW yet. Test with one real master file first. Likely tweaks: the enum values
(`CDR_PNG`, `CDR_CURRENT_PAGE`), versions of CorelDRAW that require `doc.SaveAs` options, and
shapes inside multiple layers/pages.

## Layout rules

Name objects in CorelDRAW (Object Manager) with a prefix to control behaviour:

| Prefix | Behaviour |
|---|---|
| `bg` | stretches to fill the whole page |
| `frame` | keeps its margins, stretches between them |
| `fixed` | keeps size, keeps distance to nearest edge |
| `text`, `logo` | scaled uniformly, centre stays proportional |

Untagged objects: ≥90% of the page → `bg`; text → `text`; otherwise `logo`.
Warnings are flagged in the report (clamped, tiny scale, big aspect change).

## Next ideas

Multi-page masters, per-brand rule presets, Excel/CSV import of shops, auth, job history in a DB.
"# signage-automation-tool" 
