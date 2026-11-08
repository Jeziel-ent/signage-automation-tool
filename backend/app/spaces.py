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
