import csv
import subprocess
import time
from datetime import datetime
from pathlib import Path


CONTAINERS = {"app1", "app2", "app3"}

EXPERIMENT_SECONDS = 18 * 60
CHECK_INTERVAL = 1

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_metrics():
    """Collect CPU and container count from the same Docker snapshot."""
    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}},{{.CPUPerc}}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    if result.returncode != 0:
        print(f"Docker stats failed: {result.stderr.strip()}")
        return None, None

    cpu_values = []

    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        try:
            name, cpu_text = line.split(",", 1)

            if name.strip() in CONTAINERS:
                cpu_values.append(
                    float(cpu_text.replace("%", "").strip())
                )
        except ValueError:
            continue

    if not cpu_values:
        return None, 0

    return sum(cpu_values) / len(cpu_values), len(cpu_values)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_file = DATA_DIR / f"metrics_{run_id}.csv"

    print(f"Output: {output_file}")
    print(f"Automatically stops after {EXPERIMENT_SECONDS} seconds.")
    print("Press Ctrl+C to stop early.\n")

    start_time = time.monotonic()
    deadline = start_time + EXPERIMENT_SECONDS

    with output_file.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.writer(file)
        writer.writerow([
            "timestamp",
            "elapsed_seconds",
            "cpu_percent",
            "container_count",
        ])

        try:
            while time.monotonic() < deadline:
                try:
                    cpu, container_count = get_metrics()
                except subprocess.TimeoutExpired:
                    print("Docker stats timed out.")
                    cpu, container_count = None, None

                observed_time = time.monotonic()

                # Discard a collection that finishes after the deadline.
                if observed_time >= deadline:
                    break

                elapsed = round(observed_time - start_time, 3)
                timestamp = datetime.now().isoformat(
                    timespec="milliseconds"
                )

                writer.writerow([
                    timestamp,
                    elapsed,
                    cpu,
                    container_count,
                ])
                file.flush()

                cpu_text = (
                    "N/A" if cpu is None else f"{cpu:.2f}%"
                )
                print(
                    f"Time: {elapsed:8.3f}s | "
                    f"CPU: {cpu_text} | "
                    f"Containers: {container_count}"
                )

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(CHECK_INTERVAL, remaining))

            print("\nMetrics Collector finished.")

        except KeyboardInterrupt:
            print("\nMetrics Collector stopped manually.")

    print(f"Saved: {output_file}")


if __name__ == "__main__":
    main()