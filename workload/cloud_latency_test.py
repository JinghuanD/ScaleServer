import argparse
import csv
import json
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--label", required=True)
parser.add_argument("--url", default="http://127.0.0.1:8000/work")
args = parser.parse_args()

output = (
    Path(__file__).resolve().parent.parent
    / "data"
    / f"latency_{datetime.now():%Y%m%d_%H%M%S_%f}"
)
output.mkdir(parents=True)

def work(number, scheduled):
    started = time.perf_counter()
    row = {
        "request_id": number,
        "started_timestamp": time.time(),
        "dispatch_delay_ms": round((started - scheduled) * 1000, 3),
        "success": False,
        "http_status": None,
        "hostname": "",
        "error": "",
    }
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(args.url, timeout=(2, 30))
            row["http_status"] = response.status_code
            row["success"] = response.status_code == 200
            try:
                row["hostname"] = response.json().get("hostname", "")
            except (ValueError, AttributeError):
                pass
            if not row["success"]:
                row["error"] = response.text[:200]
    except requests.RequestException as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"

    row["latency_ms"] = round(
        (time.perf_counter() - started) * 1000, 3
    )
    return row

print(f"Label: {args.label}")
print(f"Output: {output}", flush=True)

rows = []
with ThreadPoolExecutor(max_workers=32) as pool:
    futures = []
    start = time.perf_counter()

    number = 0
    stage_offset = 0
    for rate, duration in [(1, 30), (2, 90), (4, 120), (0.5, 180)]:
        print(f"Stage: {rate} rps for {duration}s", flush=True)
        for index in range(int(rate * duration)):
            scheduled = start + stage_offset + index / rate
            delay = scheduled - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            number += 1
            futures.append(pool.submit(work, number, scheduled))
        stage_offset += duration

    remaining = start + stage_offset - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)

    with (output / "requests.csv").open("w", newline="") as file:
        writer = None
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            if writer is None:
                writer = csv.DictWriter(file, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            file.flush()

latencies = sorted(r["latency_ms"] for r in rows if r["success"])
summary = {
    "label": args.label,
    "url": args.url,
    "workload_pattern": [[1, 30], [2, 90], [4, 120], [0.5, 180]],
    "duration_seconds": 420,
    "submitted": len(rows),
    "success": len(latencies),
    "failed": len(rows) - len(latencies),
    "mean_success_latency_ms": (
        sum(latencies) / len(latencies) if latencies else None
    ),
    "p95_success_latency_ms": (
        latencies[math.ceil(len(latencies) * 0.95) - 1]
        if latencies else None
    ),
    "max_dispatch_delay_ms": max(r["dispatch_delay_ms"] for r in rows),
    "successful_backends": dict(Counter(
        r["hostname"] for r in rows if r["success"]
    )),
}
(output / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
