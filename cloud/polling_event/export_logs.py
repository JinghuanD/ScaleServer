"""Run in CloudShell. Export one run's Lambda application logs and SDK counters."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True, help="UTC ISO-8601, e.g. 2026-10-30T01:00:00Z")
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    millis = lambda s: int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    start, end = millis(args.start), millis(args.end)
    if end <= start: raise ValueError("end must be after start")
    out = args.out or Path(f"logs_{args.run_id}")
    out.mkdir(exist_ok=False, parents=True)
    client = boto3.client("logs", region_name="us-east-2")
    pager = client.get_paginator("filter_log_events")
    calls, retries, kinds = Counter(), Counter(), Counter()
    arm_counts = Counter()
    with (out / "controller.jsonl").open("w") as f:
        for page in pager.paginate(logGroupName="/aws/lambda/scaleserver-event-controller",
                                  startTime=start, endTime=end,
                                  filterPattern='{ $.experiment_id = "' + args.run_id + '" }'):
            for item in page["events"]:
                try: row = json.loads(item["message"])
                except ValueError: continue
                if row.get("experiment_id") != args.run_id: continue
                f.write(json.dumps({"cloudwatch_timestamp_ms": item["timestamp"],
                                    "log_stream": item["logStreamName"], **row}) + "\n")
                kinds[row.get("kind", "unknown")] += 1
                arm_counts[row.get("controller_mode", "unknown")] += 1
                if row.get("kind") == "api_call_summary":
                    calls.update(row.get("logical_sdk_calls", {}))
                    retries.update(row.get("reported_sdk_retries", {}))
    summary = {"experiment_id": args.run_id, "start": args.start, "end": args.end,
               "log_kind_counts": dict(kinds), "arm_log_counts": dict(arm_counts),
               "logical_sdk_calls": dict(calls), "reported_sdk_retries": dict(retries),
               "caveat": "No summary can be logged after a hard timeout/crash. Check Lambda Errors/Duration/REPORT separately. Excludes service poller calls, diagnostics and observer API traffic."}
    (out / "api_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()
