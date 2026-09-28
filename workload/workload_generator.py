import requests
import time
import threading
import csv
import os
from concurrent.futures import ThreadPoolExecutor

URL = "http://localhost:8000/work"

WORKLOAD_PATTERN = [
    # (1, 30),
    # (2, 30),
    # (3, 30),
    # (5, 30),
    # (8, 30),
    # (10, 30),
    # (5, 30),
    # (2, 30),
    # (1, 30)
    (10, 30)
]

OUTPUT_FILE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "workload_results.csv"
)

executor = ThreadPoolExecutor(max_workers=100)

lock = threading.Lock()

# Statistics for the current one-second interval
completed = 0
failed = 0
response_times = []


def send_request():

    global completed, failed, response_times

    start = time.perf_counter()

    try:

        response = requests.get(
            URL,
            timeout=10
        )

        response_time = time.perf_counter() - start

        with lock:

            if response.status_code == 200:
                completed += 1
                response_times.append(response_time)
            else:
                failed += 1

    except requests.RequestException:

        with lock:
            failed += 1


def run_stage(rate, duration, writer, file):

    global completed, failed, response_times

    print(f"\nStarting workload: {rate} req/s")

    for second in range(duration):

        # Reset statistics for this interval
        with lock:
            completed = 0
            failed = 0
            response_times = []

        second_start = time.time()

        # Submit requests
        for _ in range(rate):
            executor.submit(send_request)

        # Wait until the end of this 1-second interval
        elapsed = time.time() - second_start

        if elapsed < 1:
            time.sleep(1 - elapsed)

        # Read statistics
        with lock:
            current_completed = completed
            current_failed = failed
            times = response_times.copy()

        if times:
            avg_response_time = sum(times) / len(times)
        else:
            avg_response_time = 0

        actual_throughput = current_completed

        writer.writerow([
            time.time(),
            rate,
            actual_throughput,
            round(avg_response_time, 4),
            current_failed
        ])

        file.flush()

        print(
            f"Target: {rate:2d} req/s | "
            f"Throughput: {actual_throughput:2d} req/s | "
            f"Avg RT: {avg_response_time:.3f}s | "
            f"Failed: {current_failed}"
        )


def main():

    os.makedirs(
        os.path.dirname(OUTPUT_FILE),
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "timestamp",
            "target_rate",
            "actual_throughput",
            "avg_response_time",
            "failed_requests"
        ])

        print("Variable Workload Experiment Started")

        for rate, duration in WORKLOAD_PATTERN:
            run_stage(
                rate,
                duration,
                writer,
                file
            )

    executor.shutdown(wait=True)

    print("\nExperiment finished.")
    print(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()