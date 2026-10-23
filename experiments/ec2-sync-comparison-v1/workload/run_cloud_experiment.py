import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["baseline", "event"])
parser.add_argument("--label", required=True)
args = parser.parse_args()

WORKERS = {
    "i-03235d48b9bf84eca": "running",
    "i-05a0a0812d4eb1b71": "stopped",
    "i-04b959c555e519d17": "stopped",
}

# Preflight only reads state. It does not start or stop instances.
import boto3

ec2 = boto3.client("ec2", region_name="us-east-2")
response = ec2.describe_instances(InstanceIds=list(WORKERS))

actual = {
    instance["InstanceId"]: instance["State"]["Name"]
    for reservation in response["Reservations"]
    for instance in reservation["Instances"]
}

if actual != WORKERS:
    raise SystemExit(
        f"初始实例状态不符合要求，未开始实验：{actual}"
    )

existing = subprocess.run(
    ["pgrep", "-af", "ec2_controller.py"],
    capture_output=True,
    text=True,
)

if existing.returncode == 0:
    raise SystemExit(
        "已有 EC2 控制器运行，未开始实验：\n"
        + existing.stdout
    )

expected_backend = "http://172.31.31.51:5000"

with requests.Session() as session:
    session.trust_env = False

    health = session.get(
        "http://127.0.0.1:8000/health",
        timeout=5,
    )
    health.raise_for_status()

    if health.json().get("backends") != [expected_backend]:
        raise SystemExit(
            "LB 初始后端不是仅 Worker 1，未开始实验："
            + health.text
        )

    probe = session.get(
        "http://127.0.0.1:8000/work",
        headers={"X-Request-ID": "preflight-check"},
        timeout=30,
    )
    probe.raise_for_status()

    if probe.json().get("hostname") != "worker-1":
        raise SystemExit("Worker 1 验证失败")

    if probe.headers.get("X-Request-ID") != "preflight-check":
        raise SystemExit("负载均衡器诊断版本验证失败")

run_dir = (
    ROOT / "data"
    / f"experiment_{datetime.now():%Y%m%d_%H%M%S_%f}"
)
run_dir.mkdir(parents=True)

metadata = {
    "label": args.label,
    "mode": args.mode,
    "controller_seconds": 480,
    "startup_delay_seconds": 5,
    "initial_states": actual,
    "status": "starting",
}

controller = None
workload = None


def save_metadata():
    (run_dir / "run.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )


print(f"Experiment output: {run_dir}", flush=True)

with (
    (run_dir / "controller.stdout.log").open("w") as controller_log,
    (run_dir / "workload.stdout.log").open("w") as workload_log,
):
    try:
        metadata["controller_launch_timestamp"] = time.time()
        controller_started = time.monotonic()

        controller = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "controller/ec2_controller.py",
                args.mode,
                "--seconds",
                "480",
            ],
            cwd=ROOT,
            stdout=controller_log,
            stderr=subprocess.STDOUT,
        )
        save_metadata()

        while time.monotonic() - controller_started < 5:
            if controller.poll() is not None:
                raise RuntimeError(
                    "控制器提前退出，请查看 controller.stdout.log"
                )
            time.sleep(0.1)

        if controller.poll() is not None:
            raise RuntimeError("控制器已退出，未启动负载")

        metadata["workload_launch_timestamp"] = time.time()
        metadata["observed_launch_gap_seconds"] = (
            time.monotonic() - controller_started
        )
        metadata["status"] = "running"
        save_metadata()

        workload = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "workload/cloud_latency_test.py",
                "--label",
                args.label,
            ],
            cwd=ROOT,
            stdout=workload_log,
            stderr=subprocess.STDOUT,
        )

        print(
            "控制器和负载均已启动。请保持本窗口打开。",
            flush=True,
        )

        while workload.poll() is None:
            if controller.poll() is not None:
                raise RuntimeError(
                    "负载结束前控制器提前退出，本轮需要检查"
                )
            time.sleep(1)

        metadata["workload_returncode"] = workload.returncode

        if workload.returncode != 0:
            raise RuntimeError("负载脚本运行失败")

        print(
            "负载已完成，等待控制器结束……",
            flush=True,
        )

        while controller.poll() is None:
            time.sleep(1)

        metadata["controller_returncode"] = controller.returncode

        if controller.returncode != 0:
            raise RuntimeError("控制器运行失败")

        metadata["status"] = "completed"

    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        print("实验已中断，正在结束子进程……", flush=True)

    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        print(metadata["error"], flush=True)

    finally:
        # Allow the controller to finish any already-started AWS action.
        for process in (workload, controller):
            if process is not None and process.poll() is None:
                process.send_signal(2)

        for process in (workload, controller):
            if process is not None:
                process.wait()

        metadata["finished_timestamp"] = time.time()
        save_metadata()

print("\n--- Workload output ---", flush=True)
print(
    (run_dir / "workload.stdout.log").read_text(),
    flush=True,
)
print(f"Status: {metadata['status']}", flush=True)
print(f"Saved: {run_dir}", flush=True)

if metadata["status"] != "completed":
    raise SystemExit(1)
