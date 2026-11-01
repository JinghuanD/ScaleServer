"""Recompute the supplied six-run comparison locally; no AWS calls."""
import csv
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main():
    alarms = json.loads((ROOT / "alarm_history/scaleserver-capacity-boundary-1.json").read_text())
    rows, delays = [], []
    for run in sorted((ROOT / "runs").iterdir()):
        if not run.is_dir():
            continue
        requests_path = next(run.rglob("requests.csv"))
        with requests_path.open(newline="") as file:
            requests = list(csv.DictReader(file))
        successful = [r for r in requests if r["success"] == "1"]
        latencies = sorted(float(r["response_time_seconds"]) * 1000 for r in successful)
        assert len(requests) == len(successful) == 1500
        assert len({r["request_id"] for r in requests}) == 1500
        high = min(float(r["scheduled_timestamp"]) for r in requests if r["stage"] == "2")
        low = min(float(r["scheduled_timestamp"]) for r in requests if r["stage"] == "3")
        logs_dir = ROOT / "controller_logs" / ("logs_" + run.name)
        events = [json.loads(line) for line in (logs_dir / "controller.jsonl").read_text().splitlines()]
        calls = Counter()
        for event in events:
            if event["kind"] == "api_call_summary":
                calls.update(event["logical_sdk_calls"])
        exported = json.loads((logs_dir / "api_summary.json").read_text())
        assert dict(calls) == exported["logical_sdk_calls"]
        writes = [e for e in events if e["kind"] == "scaling_requested"]
        assert [e["requested_desired"] for e in writes] == [2, 1]
        out_time, in_time = [stamp(e["requested_timestamp"]) for e in writes]
        snapshots = [json.loads(line) for line in (run / "metadata/health_snapshots.jsonl").read_text().splitlines()]
        sequence = []
        for snapshot in snapshots:
            if "desired" in snapshot and (not sequence or sequence[-1] != snapshot["desired"]):
                sequence.append(snapshot["desired"])
        assert sequence == [1, 2, 1]
        ready = next(s for s in snapshots if s["sample_completed"] >= out_time
                     and s.get("desired") == 2 and len(s.get("instances", [])) == 2
                     and all(i["LifecycleState"] == "InService" and i["HealthStatus"] == "Healthy" for i in s["instances"])
                     and {i["InstanceId"] for i in s["instances"]}.issubset(
                         {t["Target"]["Id"] for t in s.get("targets", []) if t["TargetHealth"]["State"] == "healthy"}))
        rows.append(dict(run=run.name, successful_requests=len(successful), failed_requests=len(requests)-len(successful),
                         mean_ms=mean(latencies), p95_ms=latencies[math.ceil(.95*len(latencies))-1],
                         metric_queries=calls["cloudwatch.GetMetricStatistics"], explicit_sdk_calls=sum(calls.values()),
                         high_to_scale_out_s=out_time-high, low_to_scale_in_s=in_time-low,
                         scale_out_to_observed_healthy_s=ready["sample_completed"]-out_time))
        if run.name.startswith("event"):
            alarm = next(a for a in alarms if high <= stamp(a["Timestamp"]) <= out_time
                         and json.loads(a["HistoryData"])["newState"]["stateValue"] == "ALARM")
            alarm_time = stamp(alarm["Timestamp"])
            evaluation = next(e for e in events if e["kind"] == "evaluation_started"
                              and e["event_id"] == writes[0]["event_id"] and e["attempt"] == 0)
            evaluation_time = stamp(evaluation["received_timestamp"])
            delays.append(dict(run=run.name, high_to_alarm_s=alarm_time-high,
                               alarm_to_evaluation_s=evaluation_time-alarm_time,
                               evaluation_to_scale_out_s=out_time-evaluation_time))
    for name, data in [("run_summary.csv", rows), ("expansion_delay.csv", delays)]:
        with (ROOT / name).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    for arm in ("polling", "event"):
        group = [r for r in rows if r["run"].startswith(arm)]
        print(arm, {k: round(mean(r[k] for r in group), 3) for k in group[0] if k != "run"})
    for row in delays:
        print(row)


if __name__ == "__main__":
    main()
