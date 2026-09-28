import subprocess
import csv
import time
import os
from datetime import datetime


CONTAINER_NAME = "app1"

OUTPUT_FILE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "results.csv"
)


def get_cpu_usage():

    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}},{{.CPUPerc}}"
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None

    cpu_values = []

    for line in result.stdout.splitlines():

        if not line.strip():
            continue

        try:
            name, cpu_text = line.split(",", 1)

            if name.startswith("app"):
                cpu = float(
                    cpu_text.replace("%", "").strip()
                )

                cpu_values.append(cpu)

        except ValueError:
            continue

    if not cpu_values:
        return None

    return sum(cpu_values) / len(cpu_values)


def get_running_container_count():
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "name=app",
            "--format",
            "{{.Names}}"
        ],
        capture_output=True,
        text=True
    )

    containers = [
        line for line in result.stdout.splitlines()
        if line.strip()
    ]

    return len(containers)


def main():

    os.makedirs(
        os.path.dirname(OUTPUT_FILE),
        exist_ok=True
    )

    file_exists = os.path.exists(OUTPUT_FILE)

    with open(
        OUTPUT_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        if not file_exists:
            writer.writerow([
                "timestamp",
                "elapsed_seconds",
                "cpu_percent",
                "container_count"
            ])

        start_time = time.time()

        print("Metrics Collector Started")
        print("Press Ctrl+C to stop.\n")

        try:
            while True:

                timestamp = datetime.now().isoformat(
                    timespec="seconds"
                )

                elapsed = round(
                    time.time() - start_time,
                    1
                )

                cpu = get_cpu_usage()
                container_count = get_running_container_count()

                writer.writerow([
                    timestamp,
                    elapsed,
                    cpu,
                    container_count
                ])

                file.flush()

                print(
                    f"Time: {elapsed:6.1f}s | "
                    f"CPU: {cpu}% | "
                    f"Containers: {container_count}"
                )

                time.sleep(1)

        except KeyboardInterrupt:
            print("\nMetrics Collector stopped.")


if __name__ == "__main__":
    main()