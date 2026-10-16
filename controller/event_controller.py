import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import baseline_controller as baseline


events = queue.Queue(maxsize=1)
stop_event = threading.Event()

# Set means no action is pending or executing.
action_finished = threading.Event()
action_finished.set()

log_lock = threading.Lock()
state_lock = threading.Lock()

last_scaling_monotonic = float("-inf")
action_generation = 0
stop_reason = "time_limit"

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


def monitor(deadline):
    global action_generation, stop_reason

    high_count = 0
    low_count = 0
    first_threshold_time = None
    previous_sample_monotonic = None
    sample_number = 0
    action_number = 0

    try:
        while not stop_event.is_set():
            if time.monotonic() >= deadline:
                break

            with state_lock:
                generation_before = action_generation
                pending_before = not action_finished.is_set()

            sample_started = time.perf_counter()
            cpu = baseline.get_average_cpu()
            running = baseline.get_running_containers()

            if stop_event.is_set() or time.monotonic() >= deadline:
                break

            now = time.time()
            sample_monotonic = time.perf_counter()
            sample_number += 1

            with state_lock:
                pending_after = not action_finished.is_set()
                generation_after = action_generation
                last_completed = last_scaling_monotonic

            overlaps_action = (
                pending_before
                or pending_after
                or generation_before != generation_after
            )

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
                action_pending=pending_after,
                overlaps_action=overlaps_action,
            )
            previous_sample_monotonic = sample_monotonic

            # Keep sampling while actions execute, but suppress decisions.
            if (
                overlaps_action
                or cpu is None
                or sample_monotonic - last_completed < baseline.COOLDOWN
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

            if (
                action
                and not stop_event.is_set()
                and time.monotonic() < deadline
            ):
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

                with state_lock:
                    action_finished.clear()
                    action_generation += 1

                log("event_created", **event)
                events.put_nowait(event)

                high_count = low_count = 0
                first_threshold_time = None

            remaining = deadline - time.monotonic()
            if remaining > 0:
                stop_event.wait(min(baseline.CHECK_INTERVAL, remaining))

    except Exception as exc:
        stop_reason = "monitor_error"
        log(
            "monitor_error",
            error=f"{type(exc).__name__}: {exc}",
        )

    finally:
        stop_event.set()
        log("sampling_stopped", reason=stop_reason)


def main():
    global last_scaling_monotonic, stop_reason

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    readiness_workers = []
    deadline = time.monotonic() + baseline.CONTROLLER_SECONDS

    print(f"Event log: {LOG_PATH}", flush=True)

    log(
        "controller_started",
        controller_version="slow_action_v3_autostop",
        monitor_during_action=True,
        controller_seconds=baseline.CONTROLLER_SECONDS,
        scale_out_threshold=baseline.SCALE_OUT_THRESHOLD,
        scale_in_threshold=baseline.SCALE_IN_THRESHOLD,
        scale_out_samples=baseline.SCALE_OUT_DURATION,
        scale_in_samples=baseline.SCALE_IN_DURATION,
        cooldown_seconds=baseline.COOLDOWN,
        check_interval_seconds=baseline.CHECK_INTERVAL,
        simulated_action_delay_seconds=(
            baseline.SIMULATED_ACTION_DELAY_SECONDS
        ),
    )

    worker = threading.Thread(
        target=monitor,
        args=(deadline,),
        daemon=True,
    )
    worker.start()

    try:
        while not stop_event.is_set():
            if time.monotonic() >= deadline:
                stop_event.set()
                break

            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                # Do not start a new action after the time limit.
                if stop_event.is_set() or time.monotonic() >= deadline:
                    log(
                        "action_skipped",
                        action_id=event["action_id"],
                        action=event["action"],
                        reason="controller_stopping",
                    )
                    continue

                received_time = time.time()

                log(
                    "event_received",
                    action_id=event["action_id"],
                    action=event["action"],
                    delivery_seconds=(
                        received_time - event["created_timestamp"]
                    ),
                )

                success = baseline.execute_action(
                    event,
                    log,
                    readiness_workers,
                )

                if success:
                    with state_lock:
                        last_scaling_monotonic = time.perf_counter()

            except Exception as exc:
                log(
                    "action_error",
                    action_id=event["action_id"],
                    action=event["action"],
                    error=f"{type(exc).__name__}: {exc}",
                )

            finally:
                with state_lock:
                    action_finished.set()
                events.task_done()

    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"

    finally:
        stop_event.set()
        worker.join()

        # Record any queued action that was not executed at shutdown.
        while True:
            try:
                pending = events.get_nowait()
            except queue.Empty:
                break

            log(
                "action_skipped",
                action_id=pending["action_id"],
                action=pending["action"],
                reason="controller_stopping",
            )
            events.task_done()

        action_finished.set()

        for readiness_worker in readiness_workers:
            readiness_worker.join()

        log("controller_stopped", reason=stop_reason)


if __name__ == "__main__":
    main()