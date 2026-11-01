"""Run on controller EC2 in ~/ScaleServer, not CloudShell. No capacity writes."""
import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import threading
import time

import boto3
from botocore.config import Config

PATTERN = [(0.5, 240), (1.8, 600), (0.5, 600)]
ASG = "scaleserver-asg"
FUNCTION = "scaleserver-event-controller"
TG = "arn:aws:elasticloadbalancing:us-east-2:000000000000:targetgroup/scaleserver-tg/0000000000000000"
URL = "http://example-alb.invalid/work"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["event", "polling"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true", help="Expect Lambda DRY_RUN=true")
    args = parser.parse_args()
    root = args.project.resolve()
    module_path = root / "workload" / "workload_calibration.py"
    if not module_path.exists(): raise FileNotFoundError(module_path)
    out = root / "data" / f"comparison_{args.run_id}"
    # A run ID must never silently overwrite an earlier measurement.
    if out.exists(): raise FileExistsError(f"Use a NEW run ID: {out}")
    session = boto3.Session(region_name="us-east-2")
    cfg = Config(connect_timeout=2, read_timeout=4,
                 retries={"mode": "standard", "total_max_attempts": 2})
    lam = session.client("lambda", config=cfg)
    events = session.client("events", config=cfg)
    asg = session.client("autoscaling", config=cfg)
    elb = session.client("elbv2", config=cfg)
    table = session.resource("dynamodb", config=cfg).Table("scaleserver-controller-state")
    env = lam.get_function_configuration(FunctionName=FUNCTION).get("Environment", {}).get("Variables", {})
    expected = {"CONTROLLER_MODE": args.arm, "EXPERIMENT_ID": args.run_id,
                "DRY_RUN": "true" if args.dry_run else "false"}
    if any(env.get(k) != v for k, v in expected.items()):
        raise RuntimeError(f"Lambda settings do not match. Expected {expected}; got "
                           f"{ {k:env.get(k) for k in expected} }")
    event_state = events.describe_rule(Name="scaleserver-alarm-events")["State"]
    poll_state = events.describe_rule(Name="scaleserver-polling-tick")["State"]
    if ((event_state == "ENABLED") != (args.arm == "event")
            or (poll_state == "ENABLED") != (args.arm == "polling")):
        raise RuntimeError("Trigger states do not match selected arm; run configure.py select first")
    control = table.get_item(Key={"state_key": "control#scaleserver-asg#live"},
                             ConsistentRead=True).get("Item", {})
    if control.get("pending_target") is not None:
        raise RuntimeError("Previous live intent still pending. Let the selected controller reconcile it first")
    if time.time() - int(control.get("last_change_at", 0)) < 300:
        raise RuntimeError("Previous live cooldown is still active; wait before starting a comparison run")
    def snapshot():
        start = time.time()
        group = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG])["AutoScalingGroups"][0]
        targets = elb.describe_target_health(TargetGroupArn=TG)["TargetHealthDescriptions"]
        return {"sample_started": start, "sample_completed": time.time(),
                "desired": group["DesiredCapacity"],
                "instances": group.get("Instances", []), "targets": targets}
    initial = snapshot()
    instances = initial["instances"]
    healthy = {t["Target"]["Id"] for t in initial["targets"] if t["TargetHealth"]["State"] == "healthy"}
    if (initial["desired"] != 1 or len(instances) != 1
            or any(i["LifecycleState"] != "InService" or i.get("HealthStatus") != "Healthy"
                   or i["InstanceId"] not in healthy for i in instances)):
        raise RuntimeError("Start condition must be desired=1, one InService/Healthy instance and Healthy ALB target")
    out.mkdir(parents=True)
    manifest = {"experiment_id": args.run_id, "controller_mode": args.arm,
                "dry_run": args.dry_run, "url": URL, "workload_pattern": PATTERN,
                "observer_interval_seconds": 5, "initial_snapshot": initial,
                "measurement_note": "Observer API requests are separate from controller requests; health times are sampled bounds."}
    stop = threading.Event()
    def observe():
        with (out / "health_snapshots.jsonl").open("w", encoding="utf-8") as f:
            while not stop.is_set():
                try: row = snapshot()
                except Exception as exc:
                    row = {"sample_completed": time.time(), "error": str(exc)}
                f.write(json.dumps(row, default=str) + "\n")
                f.flush()
                stop.wait(5)
    worker = threading.Thread(target=observe, daemon=True)
    # Import only the existing workload generator, never the old controllers.
    sys.path.insert(0, str(module_path.parent))
    workload = importlib.import_module("workload_calibration")
    workload.URL = URL
    workload.WORKLOAD_PATTERN = PATTERN
    before = set((root / "data").glob("run_*"))
    manifest["started_timestamp"] = time.time()
    manifest["started_utc"] = datetime.now(timezone.utc).isoformat()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    worker.start()
    try:
        workload.main()
    finally:
        manifest["finished_timestamp"] = time.time()
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        stop.set(); worker.join(timeout=30)
        manifest["workload_output_candidates"] = [str(p) for p in sorted(set((root / "data").glob("run_*")) - before)]
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
        print(f"Comparison metadata and health snapshots: {out}", flush=True)
        print("Keep each candidate run directory containing requests.csv and summary.json.", flush=True)
        print("Stop the selected arm with configure.py stop after the run is stable.", flush=True)


if __name__ == "__main__": main()
