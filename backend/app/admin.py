"""The Server page's API (administrators only): the audit log, what is wrong right now, what is on the disk, and the cleanup."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

import time

from . import agents, alerts, audit, auth, db, retention, spaces, sysmetrics

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


@router.get("/api/admin/health")
def health():
    """Live strength of the server: CPU, memory, disk, load, uptime, and how busy the job queue is."""
    snap = sysmetrics.snapshot(_data())
    with db._conn() as conn:
        queued = conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE state = 'queued'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE state IN ('claimed', 'running')").fetchone()[0]
        failed_day = conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE state = 'failed' AND created_at >= ?", (time.time() - 86400,)).fetchone()[0]
    online = [a for a in db.list_agents() if agents._agent_json(a)["online"]]
    db_path = db.DB_PATH
    return {**snap, "queue": {"queued": queued, "running": running, "failed_24h": failed_day}, "agents_online": len(online),
            "agents_total": len(db.list_agents()), "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "engine": os.environ.get("SIGNAGE_ENGINE", "auto")}


SERIES_DAYS = 14


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


@router.get("/api/admin/overview")
def overview():
    return build_overview(time.time())


def build_overview(now: float) -> dict:
    """The monitor's activity panels, all read from the database (nothing estimated):
    `series` - one row per day (UTC) for the last SERIES_DAYS days: boards converted / failed / stopped and exports done, by the day
    the job ended; `live` - every job waiting or running now; `recent` - the last finished jobs with how long they took;
    `counts` - masters, brands, users, shops; `median_convert_s` - the median time of the last 7 days' finished conversions."""
    since = now - SERIES_DAYS * 86400
    days = [_day(now - i * 86400) for i in range(SERIES_DAYS - 1, -1, -1)]
    series = {d: {"day": d, "converted": 0, "failed": 0, "stopped": 0, "exported": 0} for d in days}
    with db._conn() as conn:
        for r in conn.execute("SELECT kind, state, COALESCE(finished_at, created_at) AS t FROM agent_jobs "
                              "WHERE COALESCE(finished_at, created_at) >= ? AND state IN ('done', 'failed', 'cancelled')", (since,)):
            row = series.get(_day(r["t"]))
            if row is None:
                continue
            if r["kind"] == "convert":
                row[{"done": "converted", "failed": "failed", "cancelled": "stopped"}[r["state"]]] += 1
            elif r["kind"] == "export" and r["state"] == "done":
                row["exported"] += 1
        job_cols = ("SELECT j.id, j.kind, j.state, j.step, j.error, j.created_at, j.claimed_at, j.finished_at, s.name AS shop, "
                    "u.email AS user_email, a.name AS pc FROM agent_jobs j LEFT JOIN shops s ON s.id = j.shop_id "
                    "LEFT JOIN users u ON u.id = j.user_id LEFT JOIN agents a ON a.id = j.agent_id ")
        live = [dict(r) for r in conn.execute(job_cols + "WHERE j.state IN ('queued', 'claimed', 'running') ORDER BY j.created_at LIMIT 50")]
        recent = [dict(r) for r in conn.execute(job_cols + "WHERE j.state IN ('done', 'failed', 'cancelled') "
                                                "ORDER BY COALESCE(j.finished_at, j.created_at) DESC LIMIT 12")]
        took = sorted(r[0] - r[1] for r in conn.execute(
            "SELECT finished_at, claimed_at FROM agent_jobs WHERE kind = 'convert' AND state = 'done' AND finished_at >= ? "
            "AND claimed_at IS NOT NULL", (now - 7 * 86400,)))
        counts = {
            "masters": conn.execute("SELECT COUNT(*) FROM jobs WHERE registered = 1 AND deleted_at IS NULL").fetchone()[0],
            "brands": conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0],
            "users": conn.execute("SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0],
            "shops": conn.execute("SELECT COUNT(*) FROM shops").fetchone()[0],
        }
    for r in recent:
        r["seconds"] = round(r["finished_at"] - r["claimed_at"], 1) if r["finished_at"] and r["claimed_at"] else None
    median = took[len(took) // 2] if took else None
    return {"now": now, "series": list(series.values()), "live": live, "recent": recent, "counts": counts,
            "median_convert_s": round(median, 1) if median is not None else None}


@router.get("/api/admin/spaces")
def spaces_usage(refresh: bool = False):
    return spaces.usage(force=refresh)


@router.get("/api/admin/designers")
def designers(days: int = Query(30, ge=1, le=365)):
    """Every account with its usage over the last `days`, and its connected PCs."""
    rows = db.designer_usage(time.time() - days * 86400)
    pcs: dict[int, list] = {}
    for a in db.list_agents():
        j = agents._agent_json(a)
        pcs.setdefault(a["user_id"], []).append({"name": j["name"], "online": j["online"], "last_seen": j["last_seen"], "version": j["version"],
                                                 "corel": [c.get("major") for c in j["corel"]]})
    for r in rows:
        r["pcs"] = pcs.get(r["id"], [])
        r["active"] = bool(r["active"])
    return {"days": days, "users": rows}


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
