import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import baseline_controller as baseline


events = queue.Queue(maxsize=1)
stop_event = threading.Event()
action_finished = threading.Event()
log_lock = threading.Lock()

last_scaling_time = 0.0

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
LOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / f"event_{RUN_ID}.jsonl"
)


def log(kind, **details):
    with log_lock:
        record = {
            "timestamp": time.time(),
            "run_id": RUN_ID,
            "kind": kind,
            **details,
        }
        line = json.dumps(record, ensure_ascii=False)

        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

        print(line, flush=True)


def monitor():
    high_count = 0
    low_count = 0
    first_threshold_time = None
    sample_number = 0
    action_number = 0

    try:
        while not stop_event.is_set():
            cpu = baseline.get_average_cpu()
            running = baseline.get_running_containers()
            now = time.time()
            sample_number += 1

            log(
                "sample",
                cpu=cpu,
                container_count=len(running),
                sample_number=sample_number,
            )

            if (
                cpu is None
                or now - last_scaling_time < baseline.COOLDOWN
            ):
                high_count = low_count = 0
                first_threshold_time = None

            elif cpu > baseline.SCALE_OUT_THRESHOLD:
                if high_count == 0:
                    first_threshold_time = now
                high_count += 1
                low_count = 0

            elif cpu < baseline.SCALE_IN_THRESHOLD:
                if low_count == 0:
                    first_threshold_time = now
                low_count += 1
                high_count = 0

            else:
                high_count = low_count = 0
                first_threshold_time = None

            action = None

            if high_count >= baseline.SCALE_OUT_DURATION:
                if len(running) < len(baseline.CONTAINERS):
                    action = "scale_out"
                high_count = 0

            elif low_count >= baseline.SCALE_IN_DURATION:
                if len(running) > 1:
                    action = "scale_in"
                low_count = 0

            if action:
                action_number += 1
                created_time = time.time()

                event = {
                    "action_id": f"{RUN_ID}_{action_number}",
                    "action": action,
                    "cpu": cpu,
                    "container_count": len(running),
                    "first_threshold_timestamp": first_threshold_time,
                    "created_timestamp": created_time,
                    "threshold_confirmation_seconds": (
                        created_time - first_threshold_time
                    ),
                }

                action_finished.clear()
                log("event_created", **event)
                events.put(event)

                # Only one scaling action may be pending.
                while not action_finished.wait(0.1):
                    if stop_event.is_set():
                        return

                high_count = low_count = 0
                first_threshold_time = None

            stop_event.wait(baseline.CHECK_INTERVAL)

    except Exception as exc:
        log(
            "monitor_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        stop_event.set()


def main():
    global last_scaling_time

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    readiness_workers = []

    print(f"Event log: {LOG_PATH}", flush=True)

    log(
        "controller_started",
        scale_out_threshold=baseline.SCALE_OUT_THRESHOLD,
        scale_in_threshold=baseline.SCALE_IN_THRESHOLD,
        scale_out_samples=baseline.SCALE_OUT_DURATION,
        scale_in_samples=baseline.SCALE_IN_DURATION,
        cooldown_seconds=baseline.COOLDOWN,
        check_interval_seconds=baseline.CHECK_INTERVAL,
    )

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()

    try:
        while not stop_event.is_set():
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                continue

            received_time = time.time()
            action = event["action"]
            action_id = event["action_id"]

            try:
                log(
                    "event_received",
                    action_id=action_id,
                    action=action,
                    delivery_seconds=(
                        received_time - event["created_timestamp"]
                    ),
                )

                before = baseline.get_running_containers()
                action_started_timestamp = time.time()

                log(
                    "action_started",
                    action_id=action_id,
                    action=action,
                    before=before,
                    action_started_timestamp=action_started_timestamp,
                )

                command_started = time.perf_counter()
                reported_success = (
                    baseline.scale_out()
                    if action == "scale_out"
                    else baseline.scale_in()
                )
                command_seconds = (
                    time.perf_counter() - command_started
                )

                after = baseline.get_running_containers()
                success = baseline.verify_change(
                    action, reported_success, before, after
                )
                finished_time = time.time()

                if success:
                    last_scaling_time = finished_time

                log(
                    "action_finished",
                    action_id=action_id,
                    action=action,
                    success=success,
                    before=before,
                    after=after,
                    command_seconds=command_seconds,
                    first_threshold_to_finish_seconds=(
                        finished_time
                        - event["first_threshold_timestamp"]
                    ),
                    decision_to_finish_seconds=(
                        finished_time - event["created_timestamp"]
                    ),
                )

                if success and action == "scale_out":
                    added = [
                        name for name in after if name not in before
                    ]
                    readiness_workers.extend(
                        baseline.record_application_readiness(
                            added,
                            log,
                            action_id,
                            action_started_timestamp,
                            command_started,
                        )
                    )

            except Exception as exc:
                log(
                    "action_error",
                    action_id=action_id,
                    action=action,
                    error=f"{type(exc).__name__}: {exc}",
                )

            finally:
                action_finished.set()
                events.task_done()

    except KeyboardInterrupt:
        log("controller_stopped", reason="keyboard_interrupt")

    finally:
        stop_event.set()
        action_finished.set()
        worker.join()

        for readiness_worker in readiness_workers:
            readiness_worker.join()


if __name__ == "__main__":
    main()