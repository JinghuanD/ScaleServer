import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor

URL = "http://localhost:5000/work"

# (requests per second, duration)
WORKLOAD_PATTERN = [
    (5, 10),
    (20, 10),
    (50, 10),
    (100, 10),
    (20, 10),
    (5, 10)
]

executor = ThreadPoolExecutor(max_workers=200)

completed_requests = 0
failed_requests = 0
lock = threading.Lock()


def send_request():
    global completed_requests, failed_requests

    try:
        response = requests.get(URL, timeout=10)

        with lock:
            if response.status_code == 200:
                completed_requests += 1
            else:
                failed_requests += 1

    except requests.RequestException:
        with lock:
            failed_requests += 1


def run_stage(rate, duration):
    global completed_requests, failed_requests

    print(f"\nStarting workload: {rate} requests/sec")

    start_time = time.time()

    while time.time() - start_time < duration:

        second_start = time.time()

        for _ in range(rate):
            executor.submit(send_request)

        elapsed = time.time() - second_start

        if elapsed < 1:
            time.sleep(1 - elapsed)

        with lock:
            success = completed_requests
            failed = failed_requests

        print(
            f"Target: {rate} req/s | "
            f"Completed: {success} | "
            f"Failed: {failed}"
        )


def main():
    print("Starting Variable Workload Generator")

    for rate, duration in WORKLOAD_PATTERN:
        run_stage(rate, duration)

    executor.shutdown(wait=True)

    print("\nExperiment finished.")
    print("Completed requests:", completed_requests)
    print("Failed requests:", failed_requests)


if __name__ == "__main__":
    main()