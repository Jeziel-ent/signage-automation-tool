"""Fake corel_worker for testing corel_supervisor's timeout/kill/progress
logic without needing real CorelDRAW.

Reads the same jobs-file/results-file arguments as the real worker. Each
job's `shop` dict may set:
    "_test_action": "done" (default) - complete normally, write a result
    "_test_action": "hang"           - never return (like a stuck Dispatch())
    "_test_seconds": N               - sleep N seconds before completing

Writes results incrementally exactly like the real worker, so tests can
verify the supervisor picks up partial progress before a later job hangs.
"""
import json
import sys
import time
from pathlib import Path


def main():
    jobs_path, results_path = Path(sys.argv[1]), Path(sys.argv[2])
    heartbeat_path = results_path.with_suffix(".heartbeat")
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

    results = []
    for i, job in enumerate(jobs):
        shop = job.get("shop", {})
        heartbeat_path.write_text(json.dumps({"job_index": i, "shop_name": shop.get("name", "?"), "step": "fake_step"}), encoding="utf-8")
        action = shop.get("_test_action", "done")
        if action == "hang":
            time.sleep(10_000)
        time.sleep(shop.get("_test_seconds", 0))
        results.append({
            "master_path": job["master_path"], "shop": shop, "out_dir": job["out_dir"],
            "status": "done", "result": {"ok": True}, "seconds": shop.get("_test_seconds", 0),
        })
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    results_path.with_suffix(".done").write_text("1", encoding="utf-8")


if __name__ == "__main__":
    main()
