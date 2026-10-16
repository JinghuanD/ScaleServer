import csv
import json
import math
import time
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import requests

URL = "http://127.0.0.1:8000/work"


WORKLOAD_PATTERN = [
    (1, 120),
    (2, 120),
    (3, 120),
    (5, 120),
    (8, 120),
    (10, 120),
    (5, 120),
    (2, 120),
    (1, 120),
    # (1, 60)
]

MAX_WORKERS = 100

FIELDS = [
    "request_id", "stage", "target_rate",
    "scheduled_timestamp", "started_timestamp",
    "completed_timestamp", "queue_delay_seconds",
    "response_time_seconds", "total_time_seconds",
    "success", "status_code", "container", "error",
]


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main():
    run_name = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    output_dir = Path(__file__).resolve().parent.parent / "data" / run_name
    output_dir.mkdir(parents=True)

    rows = []
    lock = threading.Lock()
    request_id = 0
    interrupted = False

    with (output_dir / "requests.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        file.flush()

        def send_request(req_id, stage, rate, scheduled_wall, scheduled_mono):
            started_wall = time.time()
            started_mono = time.perf_counter()
            status_code = None
            container = ""
            error = ""
            success = False

            try:
                response = requests.get(URL, timeout=(1, 10))
                status_code = response.status_code
                success = status_code == 200

                if success:
                    try:
                        container = response.json().get("container", "")
                    except ValueError:
                        pass
                else:
                    error = f"HTTP {status_code}: {response.text[:200]}"

            except requests.RequestException as exc:
                error = f"{type(exc).__name__}: {exc}"

            completed_mono = time.perf_counter()
            row = {
                "request_id": req_id,
                "stage": stage,
                "target_rate": rate,
                "scheduled_timestamp": scheduled_wall,
                "started_timestamp": started_wall,
                "completed_timestamp": time.time(),
                "queue_delay_seconds": started_mono - scheduled_mono,
                "response_time_seconds": completed_mono - started_mono,
                "total_time_seconds": completed_mono - scheduled_mono,
                "success": int(success),
                "status_code": status_code,
                "container": container,
                "error": error,
            }

            with lock:
                rows.append(row)
                writer.writerow(row)
                file.flush()

        print(f"Results directory: {output_dir}", flush=True)
        print("Variable Workload Experiment Started", flush=True)


        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            try:
                for stage, (rate, duration) in enumerate(
                    WORKLOAD_PATTERN, start=1
                ):
                    if rate <= 0 or duration <= 0:
                        raise ValueError("Rate and duration must be positive.")

                    stage_mono = time.perf_counter()
                    stage_wall = time.time()
                    count = int(rate * duration)

                    print(
                        f"\nStage {stage}: {rate} req/s, {duration}s",
                        flush=True,
                    )

                    for index in range(count):
                        scheduled_mono = stage_mono + index / rate
                        scheduled_wall = stage_wall + index / rate
                        remaining = scheduled_mono - time.perf_counter()

                        if remaining > 0:
                            time.sleep(remaining)

                        request_id += 1
                        executor.submit(
                            send_request,
                            request_id,
                            stage,
                            rate,
                            scheduled_wall,
                            scheduled_mono,
                        )

                        if (index + 1) % rate == 0:
                            with lock:
                                recorded = len(rows)
                                failed = sum(
                                    1 for row in rows if not row["success"]
                                )
                            print(
                                f"Target: {rate} req/s | "
                                f"Submitted: {request_id} | "
                                f"Recorded: {recorded} | "
                                f"Failed: {failed}",
                                flush=True,
                            )

                    remaining = stage_mono + duration - time.perf_counter()
                    if remaining > 0:
                        time.sleep(remaining)

            except KeyboardInterrupt:
                interrupted = True
                print("\nStopping submissions.", flush=True)

            print("Waiting for outstanding requests...", flush=True)

    successful = [row for row in rows if row["success"]]
    response_times = [row["response_time_seconds"] for row in successful]

    summary = {
        "url": URL,
        "workload_pattern": WORKLOAD_PATTERN,
        "interrupted": interrupted,
        "submitted_requests": request_id,
        "recorded_requests": len(rows),
        "successful_requests": len(successful),
        "failed_requests": len(rows) - len(successful),
        "all_requests_recorded": request_id == len(rows),
        "mean_success_response_seconds": (
            sum(response_times) / len(response_times)
            if response_times else None
        ),
        "p95_success_response_seconds": percentile(response_times, 0.95),
        "max_queue_delay_seconds": max(
            (row["queue_delay_seconds"] for row in rows), default=0
        ),
    }

    with (output_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)

    print("\nExperiment finished.", flush=True)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
