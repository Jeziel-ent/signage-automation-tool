"""The Server page's API (administrators only): the audit log, what is wrong right now, what is on the disk, and the cleanup."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from . import alerts, audit, auth, db, retention

router = APIRouter(dependencies=[Depends(auth.require_admin)])


def _data():
    return db.DB_PATH.parent


def retention_days() -> int:
    try:
        return max(0, int(os.environ.get("SIGNAGE_RETENTION_DAYS", "0")))
    except ValueError:
        return 0


def protected_exports() -> set[str]:
    """The newest finished export of every board: the one the ZIP and the print sheet use, so it is never cleaned up."""
    with db._conn() as conn:
        rows = conn.execute("SELECT id FROM exports e WHERE status = 'done' AND created_at = "
                            "(SELECT MAX(created_at) FROM exports WHERE shop_id = e.shop_id AND status = 'done')").fetchall()
    return {r["id"] for r in rows}


def do_cleanup(days: float, dry_run: bool) -> dict:
    items = retention.plan(_data(), days=days, protected_exports=protected_exports())
    if dry_run:
        return {"dry_run": True, "would_delete": len(items), "bytes": sum(i["bytes"] for i in items), "by_category": retention.summarize(items)}
    summary = retention.run(items, _data())
    db.delete_exports(summary["export_ids"])
    return {"dry_run": False, **{k: v for k, v in summary.items() if k != "export_ids"}}


@router.get("/api/admin/audit")
def audit_log(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), action: str = "", q: str = ""):
    rows, total = db.list_audit(limit, offset, action.strip(), q.strip())
    return {"entries": rows, "total": total, "actions": db.audit_actions()}


@router.get("/api/admin/status")
def server_status():
    return {"problems": alerts.problems(_data()), "webhook_configured": bool(alerts.webhook()),
            "engine": os.environ.get("SIGNAGE_ENGINE", "auto"), "retention_days": retention_days(), "audit_days": audit.keep_days()}


@router.get("/api/admin/storage")
def storage():
    report = retention.storage_report(_data())
    days = retention_days()
    plan = retention.plan(_data(), days=days, protected_exports=protected_exports())
    free = shutil.disk_usage(_data()).free
    return {"bytes": report, "total_bytes": sum(report.values()), "free_bytes": free, "retention_days": days,
            "cleanable": retention.summarize(plan)}


class CleanupBody(BaseModel):
    days: float | None = None            # None = the configured SIGNAGE_RETENTION_DAYS (0 = temporary leftovers only)
    dry_run: bool = True


@router.post("/api/admin/cleanup")
def cleanup(body: CleanupBody):
    days = body.days if body.days is not None else retention_days()
    result = do_cleanup(max(0.0, days), body.dry_run)
    if not body.dry_run:
        audit.record("cleanup", f"{days:g} days", deleted=result["deleted"], bytes=result["bytes"])
    return result
