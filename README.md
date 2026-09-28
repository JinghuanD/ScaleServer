# ScaleServer

**Event-Driven Auto Scaling Controller for AWS EC2**

ScaleServer is an experimental project investigating how event-driven auto scaling compares with a periodic polling baseline under identical, reproducible HTTP workloads. The goal is to measure scaling responsiveness, application performance, resource efficiency, and controller overhead rather than assume that one architecture performs better.

The project direction follows Jinghuan Ding's proposal, *Event-Driven Auto Scaling Controller for AWS EC2*. This repository currently contains a local Docker polling baseline and experiment tooling; the AWS event-driven architecture is planned work.

## Research design

The proposed AWS flow is:

```text
Workload generator -> Load balancer -> Dockerized Flask application on EC2
                                            |
                                       CloudWatch
                                            |
                                     CloudWatch Alarm
                                            |
                                       EventBridge
                                            |
                                      Event Controller
                                            |
                                EC2 Auto Scaling Group
```

After receiving an event, the planned controller queries the current metric state, calculates required capacity, and updates the Auto Scaling Group's desired capacity. The proposal calls for a simple capacity-based policy and state-aware handling to avoid duplicate scaling operations.

Both the polling baseline and event-driven design are intended to run the same application and workload profiles: stable traffic, a 50 -> 300 -> 50 requests/second step workload, and an optional 500 requests/second spike. These are proposed experiment settings, not the current generator defaults.

## Current implementation

| Path | Purpose |
| --- | --- |
| `controller/baseline_controller.py` | Polls Docker CPU usage and starts or stops pre-existing containers named `app1`, `app2`, and `app3`. |
| `controller/metrics_collector.py` | Records average CPU usage and running container count to `data/results.csv`. |
| `loadbalancer/load_balancer.py` | Flask round-robin proxy on port 8000, forwarding `/work` to local ports 5000, 5001, and 5002, with fallback attempts. |
| `workload/workload_generator.py` | Sends concurrent HTTP requests to the proxy and records throughput, mean response time, and failures. Currently configured for 10 requests/second for 30 seconds. |
| `app/` | Docker build definition and current application entry-point file; see the limitation below. |
| `data/` | Existing local experiment CSV files; these are not a validated AWS comparison. |

The baseline scales out after five consecutive CPU samples above 70% and scales in after ten consecutive samples below 30%, with a 15-second cooldown and at least one running container. Each loop includes a one-second sleep plus Docker command execution time; sample counts are not precise elapsed-second thresholds.

## Running the local tools

Prerequisites: Python, Docker with an accessible daemon, and the Python packages `Flask` and `requests`.

**Current application limitation:** `app/app.py` contains an HTTP workload client rather than a Flask `/work` server. The Docker requirements file currently lists only Flask, although that entry point imports `requests`. Consequently, this checkout does not yet provide a working backend image for an end-to-end experiment. Restore or implement the intended Flask backend and its dependencies before running the complete system.

Once working backend containers named `app1`, `app2`, and `app3` have been created with host ports 5000, 5001, and 5002 respectively, start at least one container. Install tooling dependencies:

```sh
python -m pip install -r app/requirements.txt requests
```

Run these commands from the repository root in separate terminals:

```sh
python loadbalancer/load_balancer.py
python controller/baseline_controller.py
python controller/metrics_collector.py
python workload/workload_generator.py
```

The baseline starts and stops existing containers; it does not create them. The metrics collector appends to `data/results.csv`, while the workload generator overwrites `data/workload_results.csv` on each run. Preserve previous results before repeating an experiment.

## Planned evaluation

- Scaling responsiveness: detection latency, scaling reaction time, and instance provisioning time.
- Application performance: average and P95 response latency.
- Resource efficiency: instance count, scaling operations, and EC2 instance-hours.
- Controller overhead: metric queries, delivered events, and AWS API calls.

The local scripts currently collect only a subset of these measurements. AWS integration, the event controller, duplicate-event handling, and the full comparative evaluation remain to be implemented. A simplified experiment may first deliver workload-change notifications directly to the event controller before integrating CloudWatch Alarm and EventBridge.

## 项目简介

本项目研究面向 AWS EC2 的事件驱动自动扩缩容，将其与周期轮询方案在相同应用和可重复 HTTP 负载下进行对比，评估响应速度、应用性能、资源效率及控制器开销。目前仓库包含本地 Docker 轮询基线、负载均衡器、负载生成器和指标采集脚本；AWS 事件驱动控制器及完整对比实验仍处于规划阶段。当前应用入口的运行限制见上文。
