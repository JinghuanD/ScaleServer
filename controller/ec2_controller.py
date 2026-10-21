import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import boto3
import requests
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
DRAIN = ROOT / "data" / "draining.json"

WORKERS = [
    ("worker-1", "i-03235d48b9bf84eca", "172.31.31.51"),
    ("worker-2", "i-05a0a0812d4eb1b71", "172.31.16.38"),
    ("worker-3", "i-04b959c555e519d17", "172.31.16.59"),
]
IDS = [w[1] for w in WORKERS]

ec2 = boto3.client(
    "ec2",
    region_name="us-east-2",
    config=Config(
        connect_timeout=3,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    ),
)


def get_json(url):
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(url, timeout=(0.5, 1))
        response.raise_for_status()
        return response.json()


def states():
    result = ec2.describe_instances(InstanceIds=IDS)
    return {
        i["InstanceId"]: i["State"]["Name"]
        for r in result["Reservations"]
        for i in r["Instances"]
    }


def backend(worker):
    return f"http://{worker[2]}:5000"


def set_drain(worker, disabled):
    current = set(json.loads(DRAIN.read_text())) if DRAIN.exists() else set()
    if disabled:
        current.add(backend(worker))
    else:
        current.discard(backend(worker))
    temporary = DRAIN.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(current)))
    temporary.replace(DRAIN)


def metrics(worker):
    try:
        body = get_json(f"http://{worker[2]}:9100/metrics")
        value = float(body["cpu_percent"])
        age = time.time() - float(body["sample_timestamp"])
        window = float(body["sample_window_seconds"])
        if (
            not math.isfinite(value)
            or not 0 <= value <= 100
            or not -2 <= age <= 3
            or not 0.5 <= window <= 3
        ):
            raise ValueError("Invalid or stale CPU sample")
        return {"cpu": value, "timestamp": body["sample_timestamp"]}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return {"cpu": None, "error": str(exc)}


def wait_state(instance_id, target, log):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        observed = states()[instance_id]
        if observed == target:
            return
        if observed in ("terminated", "shutting-down"):
            raise RuntimeError(f"Unexpected instance state: {observed}")
        time.sleep(2)
    raise TimeoutError(f"Instance did not reach {target}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["baseline", "event"])
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--observe-only", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive and finite")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = ROOT / "data" / f"ec2_{args.mode}_{run_id}.jsonl"
    lock = threading.Lock()
    pending = threading.Event()
    stop = threading.Event()
    last_completed = float("-inf")
    action_thread = None
    last_metric = {}
    failed_action = threading.Event()

    with path.open("w") as file:
        def log(kind, **details):
            with lock:
                record = {"timestamp": time.time(), "kind": kind, **details}
                line = json.dumps(record)
                file.write(line + "\n")
                file.flush()
                print(line, flush=True)

        def execute(action, target, first_threshold, decision_time):
            nonlocal last_completed
            started = time.monotonic()
            success = False
            log("action_started", action=action, worker=target[0])
            try:
                current = states()
                if action == "scale_out":
                    if current[target[1]] != "stopped":
                        raise RuntimeError("Scale-out target is not stopped")
                    # Keep it excluded until health and metrics are ready.
                    set_drain(target, True)
                    api_start = time.monotonic()
                    ec2.start_instances(InstanceIds=[target[1]])
                    log("api_returned", action=action,
                        seconds=time.monotonic() - api_start)
                    wait_state(target[1], "running", log)
                    log("instance_running", worker=target[0],
                        action_elapsed=time.monotonic() - started)

                    deadline = time.monotonic() + 180
                    app_ready = False
                    while time.monotonic() < deadline:
                        try:
                            body = get_json(backend(target) + "/health")
                            app_ready = (
                                body.get("status") == "healthy"
                                and body.get("hostname") == target[0]
                            )
                        except (requests.RequestException, ValueError,
                                AttributeError):
                            app_ready = False

                        if app_ready:
                            log("application_ready", worker=target[0],
                                action_elapsed=time.monotonic() - started)
                            break
                        time.sleep(1)

                    if not app_ready:
                        raise TimeoutError("Application health timeout")

                    deadline = time.monotonic() + 30
                    while metrics(target)["cpu"] is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Metrics readiness timeout")
                        time.sleep(1)

                    set_drain(target, False)
                    log("backend_enabled", worker=target[0],
                        action_elapsed=time.monotonic() - started)

                else:
                    if target == WORKERS[0]:
                        raise RuntimeError("Worker 1 cannot be stopped")
                    if current[target[1]] != "running":
                        raise RuntimeError("Scale-in target is not running")
                    if current[WORKERS[0][1]] != "running":
                        raise RuntimeError("Worker 1 is not running")

                    set_drain(target, True)
                    log("backend_draining", worker=target[0])
                    # Existing LB requests have bounded backend timeouts.
                    time.sleep(35)
                    api_start = time.monotonic()
                    ec2.stop_instances(InstanceIds=[target[1]])
                    log("api_returned", action=action,
                        seconds=time.monotonic() - api_start)
                    wait_state(target[1], "stopped", log)
                    log("instance_stopped", worker=target[0])

                success = True
            except Exception as exc:
                failed_action.set()
                log("action_error", action=action, worker=target[0],
                    error=f"{type(exc).__name__}: {exc}")
            finally:
                last_completed = time.monotonic()
                log(
                    "action_finished", action=action, worker=target[0],
                    success=success,
                    action_seconds=last_completed - started,
                    decision_to_finish_seconds=time.time() - decision_time,
                    threshold_to_finish_seconds=time.time() - first_threshold,
                )
                pending.clear()

        log("controller_started", mode=args.mode,
            observe_only=args.observe_only, seconds=args.seconds,
            scale_out_threshold=35, scale_in_threshold=12,
            scale_out_samples=5, scale_in_samples=10,
            cooldown_seconds=30, drain_seconds=35)

        deadline = time.monotonic() + args.seconds
        previous_sample = None
        high = low = 0
        first_threshold = None

        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                while time.monotonic() < deadline and not stop.is_set():
                    sample_start = time.monotonic()
                    pending_before = pending.is_set()
                    generation_before = last_completed
                    try:
                        current = states()
                        running = [
                            w for w in WORKERS if current[w[1]] == "running"
                        ]
                        readings = list(pool.map(metrics, running))
                        values = {
                            w[0]: reading for w, reading in zip(running, readings)
                        }
                        overlap = (
                            pending_before or pending.is_set()
                            or generation_before != last_completed
                        )
                        valid = bool(running) and all(
                            r["cpu"] is not None for r in readings
                        )
                        fresh = valid and all(
                            r["timestamp"] != last_metric.get(w[0])
                            for w, r in zip(running, readings)
                        )
                        if valid:
                            last_metric.update({
                                w[0]: r["timestamp"]
                                for w, r in zip(running, readings)
                            })
                        cpu = (
                            sum(r["cpu"] for r in readings) / len(readings)
                            if valid else None
                        )
                        now = time.monotonic()
                        log(
                            "sample", cpu=cpu, states=current,
                            workers=values, fresh=fresh,
                            action_pending=pending.is_set(),
                            overlaps_action=overlap,
                            sampling_seconds=now - sample_start,
                            sample_interval_seconds=(
                                None if previous_sample is None
                                else now - previous_sample
                            ),
                        )
                        previous_sample = now

                        transition = any(
                            state not in ("running", "stopped")
                            for state in current.values()
                        )
                        if (
                            overlap or transition or not fresh
                            or failed_action.is_set()
                            or now - last_completed < 30
                            or current[IDS[0]] != "running"
                        ):
                            high = low = 0
                            first_threshold = None
                        elif cpu > 35:
                            if high == 0:
                                first_threshold = time.time()
                            high += 1
                            low = 0
                        elif cpu < 12:
                            if low == 0:
                                first_threshold = time.time()
                            low += 1
                            high = 0
                        else:
                            high = low = 0
                            first_threshold = None

                        action = target = None
                        if high >= 5:
                            target = next(
                                (w for w in WORKERS[1:]
                                 if current[w[1]] == "stopped"), None
                            )
                            action = "scale_out"
                        elif low >= 10:
                            target = next(
                                (w for w in reversed(WORKERS[1:])
                                 if current[w[1]] == "running"), None
                            )
                            action = "scale_in"

                        if target and time.monotonic() < deadline:
                            created = time.time()
                            log("decision_created", action=action,
                                worker=target[0], cpu=cpu,
                                first_threshold_timestamp=first_threshold,
                                observe_only=args.observe_only)
                            if not args.observe_only:
                                pending.set()
                                arguments = (
                                    action, target, first_threshold, created
                                )
                                if args.mode == "baseline":
                                    execute(*arguments)
                                else:
                                    action_thread = threading.Thread(
                                        target=execute, args=arguments
                                    )
                                    action_thread.start()
                            high = low = 0
                            first_threshold = None

                    except Exception as exc:
                        high = low = 0
                        first_threshold = None
                        log("sample_error",
                            error=f"{type(exc).__name__}: {exc}")

                    stop.wait(1)

        except KeyboardInterrupt:
            log("interrupt_received")
        finally:
            log("sampling_stopped")
            if action_thread is not None:
                action_thread.join()
            log("controller_stopped")


if __name__ == "__main__":
    main()
