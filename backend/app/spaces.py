"""How much of the DigitalOcean Spaces bucket is used, for the admin page. Read-only: it lists the bucket, it never writes or deletes.
Needs SIGNAGE_SPACES_KEY, SIGNAGE_SPACES_SECRET, SIGNAGE_SPACES_BUCKET (+ SIGNAGE_SPACES_REGION, default sgp1; SIGNAGE_SPACES_QUOTA_GB to show a
percentage) and the boto3 package. Not configured = the page says so; nothing breaks."""
from __future__ import annotations

import os
import threading
import time

CACHE_S = 600            # listing a big bucket is slow and billed per request: at most one count per 10 minutes
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None}


def reset() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)


def settings() -> dict | None:
    key, secret, bucket = (os.environ.get(k, "").strip() for k in ("SIGNAGE_SPACES_KEY", "SIGNAGE_SPACES_SECRET", "SIGNAGE_SPACES_BUCKET"))
    if not (key and secret and bucket):
        return None
    region = os.environ.get("SIGNAGE_SPACES_REGION", "sgp1").strip() or "sgp1"
    try:
        quota = float(os.environ.get("SIGNAGE_SPACES_QUOTA_GB", "0")) * 1024 ** 3
    except ValueError:
        quota = 0.0
    return {"key": key, "secret": secret, "bucket": bucket, "region": region, "quota": quota}


def _client(cfg: dict):
    import boto3  # imported here: only a server that uses Spaces needs the package

    return boto3.client("s3", region_name=cfg["region"], endpoint_url=f"https://{cfg['region']}.digitaloceanspaces.com",
                        aws_access_key_id=cfg["key"], aws_secret_access_key=cfg["secret"])


def count(client, bucket: str) -> tuple[int, int]:
    """(bytes, objects) of everything in the bucket."""
    total = objects = 0
    token = None
    while True:
        args = {"Bucket": bucket, **({"ContinuationToken": token} if token else {})}
        page = client.list_objects_v2(**args)
        for item in page.get("Contents", []):
            total += int(item["Size"])
            objects += 1
        if not page.get("IsTruncated"):
            return total, objects
        token = page["NextContinuationToken"]


def usage(client_factory=_client, now: float | None = None, force: bool = False) -> dict:
    """{configured, ...}: bytes used, objects, bucket, region, quota, `checked_at`, or `error` when it could not be read."""
    cfg = settings()
    if cfg is None:
        return {"configured": False, "message": "Spaces is not connected yet. Set SIGNAGE_SPACES_KEY, SIGNAGE_SPACES_SECRET and SIGNAGE_SPACES_BUCKET."}
    now = time.time() if now is None else now
    with _lock:
        if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_S:
            return _cache["value"]
        base = {"configured": True, "bucket": cfg["bucket"], "region": cfg["region"], "quota_bytes": cfg["quota"] or None}
        try:
            used, objects = count(client_factory(cfg), cfg["bucket"])
            value = {**base, "bytes": used, "objects": objects, "checked_at": now,
                     "pct": round(used / cfg["quota"] * 100, 1) if cfg["quota"] else None}
        except ImportError:
            value = {**base, "error": "the boto3 package is not installed on the server (pip install boto3)"}
        except Exception as e:  # noqa: BLE001 - wrong keys, no network, bucket missing: tell the admin, keep the page alive
            value = {**base, "error": f"could not read the bucket: {type(e).__name__}: {e}"}
        _cache.update(at=now, value=value)
        return value


# ------------------------------------------------------------------ the "signage-automation" area: where backups (and later files) go
ROOT = "signage-automation"
FOLDERS = ("database-backups", "masters", "generated-boards", "recently-generated", "editor-exports", "users",
           "corrections", "agent-downloads", "shared-links", "logs")


def backup_settings() -> dict | None:
    """Same keys as the usage check; the backups may live in ANOTHER bucket: SIGNAGE_SPACES_BACKUP_BUCKET (default = SIGNAGE_SPACES_BUCKET)."""
    cfg = settings()
    if cfg is None:
        return None
    return {**cfg, "bucket": os.environ.get("SIGNAGE_SPACES_BACKUP_BUCKET", "").strip() or cfg["bucket"]}


def ensure_layout(client, bucket: str) -> list[str]:
    """Create the empty folders (zero-byte keys ending in '/', which is how Spaces shows a folder). Returns the ones created."""
    made = []
    for name in ("", *FOLDERS):
        key = f"{ROOT}/{name}/" if name else f"{ROOT}/"
        try:
            client.head_object(Bucket=bucket, Key=key)
        except Exception:  # noqa: BLE001 - not there (404) -> create it
            client.put_object(Bucket=bucket, Key=key, Body=b"")
            made.append(key)
    return made


def backup_key(file_name: str, when: float | None = None) -> str:
    return f"{ROOT}/database-backups/{time.strftime('%Y-%m-%d', time.localtime(when or time.time()))}/{file_name}"


def upload_backup(path, client_factory=_client, when: float | None = None) -> dict | None:
    """Upload one backup file to Spaces. None when Spaces is not configured; raises on a real failure (the caller reports it)."""
    cfg = backup_settings()
    if cfg is None:
        return None
    client = client_factory(cfg)
    ensure_layout(client, cfg["bucket"])
    key = backup_key(path.name, when)
    client.upload_file(str(path), cfg["bucket"], key)
    return {"bucket": cfg["bucket"], "key": key, "region": cfg["region"]}


def prune_remote(days: int, client_factory=_client, now: float | None = None) -> int:
    """Delete backups in Spaces older than `days` (by their upload time), never the newest one. Only touches signage-automation/database-backups/."""
    cfg = backup_settings()
    if cfg is None:
        return 0
    now = time.time() if now is None else now
    client = client_factory(cfg)
    prefix = f"{ROOT}/database-backups/"
    items, token = [], None
    while True:
        page = client.list_objects_v2(Bucket=cfg["bucket"], Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        items += [i for i in page.get("Contents", []) if not i["Key"].endswith("/")]
        if not page.get("IsTruncated"):
            break
        token = page["NextContinuationToken"]
    items.sort(key=lambda i: i["LastModified"])
    old = [i for i in items[:-1] if now - i["LastModified"].timestamp() > days * 86400]
    for i in old:
        client.delete_object(Bucket=cfg["bucket"], Key=i["Key"])
    return len(old)
