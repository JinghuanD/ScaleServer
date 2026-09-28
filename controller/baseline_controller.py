import subprocess
import time
from datetime import datetime

CONTAINERS = ["app1", "app2", "app3"]

SCALE_OUT_THRESHOLD = 70.0
SCALE_IN_THRESHOLD = 30.0

SCALE_OUT_DURATION = 5
SCALE_IN_DURATION = 10

COOLDOWN = 15
CHECK_INTERVAL = 1


def is_running(container):
    result = subprocess.run(
        [
            "docker", "inspect",
            "-f", "{{.State.Running}}",
            container
        ],
        capture_output=True,
        text=True
    )

    return result.stdout.strip() == "true"


def get_running_containers():
    return [
        container
        for container in CONTAINERS
        if is_running(container)
    ]


def get_average_cpu():

    running = get_running_containers()

    if not running:
        return None

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

            if name in running:
                cpu = float(
                    cpu_text.replace("%", "").strip()
                )

                cpu_values.append(cpu)

        except ValueError:
            continue

    if not cpu_values:
        return None

    return sum(cpu_values) / len(cpu_values)


def scale_out():

    running = get_running_containers()

    if len(running) >= len(CONTAINERS):
        return False

    for container in CONTAINERS:

        if container not in running:

            print(f"\n>>> SCALE OUT: Starting {container}")

            subprocess.run(
                ["docker", "start", container]
            )

            return True

    return False


def scale_in():

    running = get_running_containers()

    if len(running) <= 1:
        return False

    # Stop the highest-numbered running container
    container = running[-1]

    print(f"\n>>> SCALE IN: Stopping {container}")

    subprocess.run(
        ["docker", "stop", container]
    )

    return True


def main():

    high_cpu_count = 0
    low_cpu_count = 0

    last_scaling_time = 0

    print("Baseline Auto-Scaling Controller Started")
    print("----------------------------------------")

    while True:

        try:

            avg_cpu = get_average_cpu()

            if avg_cpu is None:
                print("Unable to read CPU.")
                time.sleep(CHECK_INTERVAL)
                continue

            running = get_running_containers()

            now = time.time()

            print(
                f"{datetime.now().strftime('%H:%M:%S')} | "
                f"CPU: {avg_cpu:6.2f}% | "
                f"Containers: {len(running)}"
            )

            # Cooldown period
            if now - last_scaling_time < COOLDOWN:

                high_cpu_count = 0
                low_cpu_count = 0

                time.sleep(CHECK_INTERVAL)
                continue

            # High CPU
            if avg_cpu > SCALE_OUT_THRESHOLD:

                high_cpu_count += 1
                low_cpu_count = 0

            # Low CPU
            elif avg_cpu < SCALE_IN_THRESHOLD:

                low_cpu_count += 1
                high_cpu_count = 0

            # Normal CPU
            else:

                high_cpu_count = 0
                low_cpu_count = 0

            # Scale out
            if high_cpu_count >= SCALE_OUT_DURATION:

                if scale_out():
                    last_scaling_time = time.time()

                high_cpu_count = 0

            # Scale in
            elif low_cpu_count >= SCALE_IN_DURATION:

                if scale_in():
                    last_scaling_time = time.time()

                low_cpu_count = 0

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:

            print("\nController stopped.")
            break


if __name__ == "__main__":
    main()