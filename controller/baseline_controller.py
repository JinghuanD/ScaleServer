import json
import math
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests


CONTAINERS = ["app1", "app2", "app3"]

BACKEND_URLS = {
    "app1": "http://127.0.0.1:5000",
    "app2": "http://127.0.0.1:5001",
    "app3": "http://127.0.0.1:5002",
}

SCALE_OUT_THRESHOLD = 70.0
SCALE_IN_THRESHOLD = 30.0

# Consecutive sample counts, not seconds.
SCALE_OUT_DURATION = 5
SCALE_IN_DURATION = 10

COOLDOWN = 15
CHECK_INTERVAL = 1

# Automatic sampling limit: 20 minutes.
CONTROLLER_SECONDS = float(
    os.environ.get("CONTROLLER_SECONDS", "1200")
)

SIMULATED_ACTION_DELAY_SECONDS = float(
    os.environ.get("SIMULATED_ACTION_DELAY_SECONDS", "0")
)

if not math.isfinite(CONTROLLER_SECONDS) or CONTROLLER_SECONDS <= 0:
    raise ValueError("CONTROLLER_SECONDS must be positive and finite")

if (
    not math.isfinite(SIMULATED_ACTION_DELAY_SECONDS)
    or not 0 <= SIMULATED_ACTION_DELAY_SECONDS <= 60
):
    raise ValueError(
        "SIMULATED_ACTION_DELAY_SECONDS must be between 0 and 60"
    )


def is_running(container):
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def get_running_containers():
    return [name for name in CONTAINERS if is_running(name)]


def get_average_cpu():
    running = get_running_containers()

    if not running:
        return None

    result = subprocess.run(
        [
            "docker", "stats", "--no-stream",
            "--format", "{{.Name}},{{.CPUPerc}}",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None

    values = {}

    for line in result.stdout.splitlines():
        try:
            name, cpu_text = line.split(",", 1)
            if name in running:
                values[name] = float(cpu_text.replace("%", "").strip())
        except ValueError:
            continue

    if any(name not in values for name in running):
        return None

    return sum(values[name] for name in running) / len(running)


def scale_out():
    running = get_running_containers()

    for container in CONTAINERS:
        if container not in running:
            print(f">>> SCALE OUT: Starting {container}", flush=True)
            result = subprocess.run(["docker", "start", container])
            return result.returncode == 0

    return False


def scale_in():
    running = get_running_containers()

    if len(running) <= 1:
        return False

    container = running[-1]
    print(f">>> SCALE IN: Stopping {container}", flush=True)
    result = subprocess.run(["docker", "stop", container])
    return result.returncode == 0


def verify_change(action, reported_success, before, after):
    if not reported_success:
        return False

    if action == "scale_out":
        return (
            len(after) == len(before) + 1
            and set(before).issubset(after)
        )

    if action == "scale_in":
        return (
            len(after) == len(before) - 1
            and set(after).issubset(before)
        )

    return False


def simulate_action_delay(log_fn, action_id, action):
    if SIMULATED_ACTION_DELAY_SECONDS == 0:
        return

    log_fn(
        "simulated_delay_started",
        action_id=action_id,
        action=action,
        delay_seconds=SIMULATED_ACTION_DELAY_SECONDS,
    )

    time.sleep(SIMULATED_ACTION_DELAY_SECONDS)

    log_fn(
        "simulated_delay_finished",
        action_id=action_id,
        action=action,
    )


def record_application_readiness(
    containers,
    log_fn,
    action_id,
    action_started_timestamp,
    action_started_monotonic,
):
    def probe(container):
        deadline = time.perf_counter() + 30
        attempts = 0
        last_error = None

        with requests.Session() as session:
            session.trust_env = False

            while time.perf_counter() < deadline:
                attempts += 1

                try:
                    response = session.get(
                        BACKEND_URLS[container] + "/health",
                        timeout=(0.3, 1.0),
                    )

                    if response.status_code == 200:
                        body = response.json()

                        if (
                            isinstance(body, dict)
                            and body.get("status") == "healthy"
                        ):
                            log_fn(
                                "application_ready",
                                action_id=action_id,
                                container=container,
                                backend=BACKEND_URLS[container],
                                action_started_timestamp=(
                                    action_started_timestamp
                                ),
                                ready_observed_timestamp=time.time(),
                                action_start_to_ready_seconds=(
                                    time.perf_counter()
                                    - action_started_monotonic
                                ),
                                attempts=attempts,
                            )
                            return

                        last_error = "Health status was not healthy"
                    else:
                        last_error = f"HTTP {response.status_code}"

                except (requests.RequestException, ValueError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"

                time.sleep(0.2)

        log_fn(
            "application_ready_timeout",
            action_id=action_id,
            container=container,
            attempts=attempts,
            error=last_error,
        )

    workers = []

    for container in containers:
        worker = threading.Thread(
            target=probe,
            args=(container,),
            daemon=True,
        )
        worker.start()
        workers.append(worker)

    return workers


def execute_action(event, log_fn, readiness_workers):
    """Shared action execution. Returns success."""
    action = event["action"]
    action_id = event["action_id"]
    before = get_running_containers()

    if len(before) != event["container_count"]:
        log_fn(
            "action_skipped",
            action_id=action_id,
            action=action,
            reason="container_count_changed",
            expected_count=event["container_count"],
            before=before,
        )
        return False

    if (
        action == "scale_out" and len(before) >= len(CONTAINERS)
    ) or (
        action == "scale_in" and len(before) <= 1
    ):
        log_fn(
            "action_skipped",
            action_id=action_id,
            action=action,
            reason="container_limit",
            before=before,
        )
        return False

    action_started_timestamp = time.time()
    action_started_monotonic = time.perf_counter()

    log_fn(
        "action_started",
        action_id=action_id,
        action=action,
        before=before,
        action_started_timestamp=action_started_timestamp,
    )

    simulate_action_delay(log_fn, action_id, action)

    # Docker command timing excludes the simulated delay.
    command_started = time.perf_counter()
    reported_success = (
        scale_out() if action == "scale_out" else scale_in()
    )
    command_seconds = time.perf_counter() - command_started

    after = get_running_containers()
    success = verify_change(action, reported_success, before, after)
    finished_time = time.time()
    finished_monotonic = time.perf_counter()

    log_fn(
        "action_finished",
        action_id=action_id,
        action=action,
        success=success,
        before=before,
        after=after,
        command_seconds=command_seconds,
        action_start_to_finish_seconds=(
            finished_monotonic - action_started_monotonic
        ),
        first_threshold_to_finish_seconds=(
            finished_time - event["first_threshold_timestamp"]
        ),
        decision_to_finish_seconds=(
            finished_time - event["created_timestamp"]
        ),
    )

    if success and action == "scale_out":
        added = [name for name in after if name not in before]
        readiness_workers.extend(
            record_application_readiness(
                added,
                log_fn,
                action_id,
                action_started_timestamp,
                action_started_monotonic,
            )
        )

    return success


def main():
    high_count = 0
    low_count = 0
    first_threshold_time = None
    last_scaling_monotonic = float("-inf")
    previous_sample_monotonic = None
    sample_number = 0
    action_number = 0
    readiness_workers = []
    stop_reason = "time_limit"

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / f"baseline_{run_id}.jsonl"
    log_lock = threading.Lock()

    with log_path.open("w", encoding="utf-8") as log_file:
        def log(kind, **details):
            with log_lock:
                record = {
                    "timestamp": time.time(),
                    "run_id": run_id,
                    "kind": kind,
                    **details,
                }
                line = json.dumps(record, ensure_ascii=False)
                log_file.write(line + "\n")
                log_file.flush()
                print(line, flush=True)

        deadline = time.monotonic() + CONTROLLER_SECONDS

        print(f"Baseline log: {log_path}", flush=True)

        log(
            "controller_started",
            controller_version="slow_action_v3_autostop",
            monitor_during_action=False,
            controller_seconds=CONTROLLER_SECONDS,
            scale_out_threshold=SCALE_OUT_THRESHOLD,
            scale_in_threshold=SCALE_IN_THRESHOLD,
            scale_out_samples=SCALE_OUT_DURATION,
            scale_in_samples=SCALE_IN_DURATION,
            cooldown_seconds=COOLDOWN,
            check_interval_seconds=CHECK_INTERVAL,
            simulated_action_delay_seconds=(
                SIMULATED_ACTION_DELAY_SECONDS
            ),
        )

        try:
            while time.monotonic() < deadline:
                sample_started = time.perf_counter()
                cpu = get_average_cpu()
                running = get_running_containers()

                # Do not record samples or create decisions past the limit.
                if time.monotonic() >= deadline:
                    break

                now = time.time()
                sample_monotonic = time.perf_counter()
                sample_number += 1

                log(
                    "sample",
                    cpu=cpu,
                    container_count=len(running),
                    sample_number=sample_number,
                    sample_observed_timestamp=now,
                    sampling_seconds=sample_monotonic - sample_started,
                    sample_interval_seconds=(
                        None
                        if previous_sample_monotonic is None
                        else sample_monotonic - previous_sample_monotonic
                    ),
                    action_pending=False,
                )
                previous_sample_monotonic = sample_monotonic

                if (
                    cpu is None
                    or sample_monotonic - last_scaling_monotonic < COOLDOWN
                ):
                    high_count = low_count = 0
                    first_threshold_time = None

                elif cpu > SCALE_OUT_THRESHOLD:
                    if high_count == 0:
                        first_threshold_time = now
                    high_count += 1
                    low_count = 0

                elif cpu < SCALE_IN_THRESHOLD:
                    if low_count == 0:
                        first_threshold_time = now
                    low_count += 1
                    high_count = 0

                else:
                    high_count = low_count = 0
                    first_threshold_time = None

                action = None

                if high_count >= SCALE_OUT_DURATION:
                    if len(running) < len(CONTAINERS):
                        action = "scale_out"
                    high_count = 0

                elif low_count >= SCALE_IN_DURATION:
                    if len(running) > 1:
                        action = "scale_in"
                    low_count = 0

                if action and time.monotonic() < deadline:
                    action_number += 1
                    created_time = time.time()

                    event = {
                        "action_id": f"{run_id}_{action_number}",
                        "action": action,
                        "cpu": cpu,
                        "container_count": len(running),
                        "first_threshold_timestamp": first_threshold_time,
                        "created_timestamp": created_time,
                        "threshold_confirmation_seconds": (
                            created_time - first_threshold_time
                        ),
                    }

                    log("decision_created", **event)

                    if execute_action(event, log, readiness_workers):
                        last_scaling_monotonic = time.perf_counter()

                    high_count = low_count = 0
                    first_threshold_time = None

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(CHECK_INTERVAL, remaining))

        except KeyboardInterrupt:
            stop_reason = "keyboard_interrupt"

        except Exception as exc:
            stop_reason = "controller_error"
            log(
                "controller_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        finally:
            log("sampling_stopped", reason=stop_reason)

            for worker in readiness_workers:
                worker.join()

            log("controller_stopped", reason=stop_reason)


if __name__ == "__main__":
    main()