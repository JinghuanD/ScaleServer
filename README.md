# ScaleServer

**Event-Driven Auto Scaling Controller for AWS EC2**

ScaleServer is an experimental project investigating how event-driven auto scaling compares with a periodic polling baseline under identical, reproducible HTTP workloads. The goal is to measure scaling responsiveness, application performance, resource efficiency, and controller overhead rather than assume that one architecture performs better.

The project direction follows Jinghuan Ding's proposal, *Event-Driven Auto Scaling Controller for AWS EC2*. This repository currently contains a local Docker polling baseline and experiment tooling; the AWS event-driven architecture is planned work.

## Baseline experiment: local Docker auto-scaling

Completed an 18-minute polling-based experiment with three Docker containers.

- **Workload:** `1 -> 2 -> 3 -> 5 -> 8 -> 10 -> 5 -> 2 -> 1 req/s`, with 120 seconds per stage.
- **Scaling policy:** scale out after 5 consecutive CPU samples above 70%; scale in after 10 consecutive samples below 30%. Cooldown: 15 seconds; capacity: 1-3 containers.
- **Results:** all **4,440 requests** succeeded and were recorded. Mean response time: **184 ms**; P95: **203 ms** (nearest-rank percentile of successful request response times).
- **Scaling behavior:** container count changed from **1 -> 2 -> 3 -> 2 -> 1**. All three containers served requests.
- **Fixes:** added backend health checks, corrected concurrent retry handling, and introduced per-request logging with completion tracking.

This run provides a local baseline for comparison with the upcoming event-driven controller, not evidence of event-driven performance improvements. Scaling analysis uses monitoring samples between the first scheduled request and the last completed request, excluding idle monitoring data after the workload ended.

### Results and experiment history

The successful run is recorded in [summary.json](data/run_20261006_155231_781230/summary.json) and [requests.csv](data/run_20261006_155231_781230/requests.csv). Its monitoring data is in [data/results.csv](data/results.csv). The [Version 3 archive](Baseline_Experiment_3.zip) preserves request records, summary, monitoring data, and controller/workload logs together.

| Version | Issue or outcome | Resolution |
| --- | --- | --- |
| **1 — Request routing issues** | Requests attempted stopped containers, adding connection delays. A shared round-robin iterator could make concurrent retries miss an available backend. Statistics lacked individual latency records and could omit outstanding requests at shutdown. | Switched to `127.0.0.1`, used a per-request backend snapshot and retry order, added health checks, and logged individual requests while waiting for outstanding work to finish. |
| **2 — Load balancer unreachable** | All 4,440 requests failed while application CPU remained near idle. Request logs showed connection timeouts to port 8000, so this run could not measure auto-scaling behavior. | Restarted the load balancer and verified both `/health` and `/work` before rerunning. |
| **3 — Successful baseline** | All 4,440 requests succeeded with complete records and the expected 1 -> 2 -> 3 -> 2 -> 1 scaling sequence. | Retained as the successful local baseline for future comparison. |

Earlier evidence is retained in the [Version 1 archive](Baseline_Experiment_1.zip) and [Version 2 request logs](data/run_20261006_150905_148956/requests.csv). Other timestamped run directories contain connectivity checks or an interrupted run; they are not part of the successful 18-minute baseline.

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
| `loadbalancer/load_balancer.py` | Flask proxy on port 8000 with concurrent backend health checks, locked round-robin selection, and per-request fallback across healthy backends on ports 5000-5002. |
| `workload/workload_generator.py` | Runs the nine-stage, 18-minute workload; records each request, latency, backend identity, and completion status, then writes a run summary including mean and P95 latency. |
| `app/` | Docker build definition and current application entry-point file; see the limitation below. |
| `data/` | Existing local experiment CSV files; these are not a validated AWS comparison. |

The baseline scales out after five consecutive CPU samples above 70% and scales in after ten consecutive samples below 30%, with a 15-second cooldown and at least one running container. Each loop includes a one-second sleep plus Docker command execution time; sample counts are not precise elapsed-second thresholds.

## Running the local tools

Prerequisites: Python, Docker with an accessible daemon, and the Python packages `Flask` and `requests`.

**Current application limitation:** `app/app.py` contains an HTTP workload client rather than a Flask `/work` server. The Docker requirements file currently lists only Flask, although that entry point imports `requests`. The successful baseline used running backend containers, but this checkout does not yet provide their working Flask backend source/image definition for reproducing the end-to-end experiment. Restore or implement the intended Flask backend and its dependencies before running the complete system.

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

Before launching the workload, check `http://127.0.0.1:8000/health` and confirm that its `backends` list is nonempty, then verify that `http://127.0.0.1:8000/work` returns HTTP 200. The proxy health endpoint alone does not guarantee that a backend is available. Backend applications must expose `/health` and `/work`; a JSON `container` field in the work response enables backend attribution.

The baseline starts and stops existing containers; it does not create them. The metrics collector appends to `data/results.csv`; preserve or rotate that file before repeating an experiment. The workload generator creates a new `data/run_<timestamp>/` directory containing `requests.csv` and `summary.json`, and waits for submitted requests to complete before closing the CSV. `data/workload_results.csv` is retained as an earlier-format result file.

## Planned evaluation

- Scaling responsiveness: detection latency, scaling reaction time, and instance provisioning time.
- Application performance: average and P95 response latency.
- Resource efficiency: instance count, scaling operations, and EC2 instance-hours.
- Controller overhead: metric queries, delivered events, and AWS API calls.

The local scripts currently collect only a subset of these measurements. AWS integration, the event controller, duplicate-event handling, and the full comparative evaluation remain to be implemented. A simplified experiment may first deliver workload-change notifications directly to the event controller before integrating CloudWatch Alarm and EventBridge.

## 项目简介

本项目研究面向 AWS EC2 的事件驱动自动扩缩容，将其与周期轮询方案在相同应用和可重复 HTTP 负载下进行对比，评估响应速度、应用性能、资源效率及控制器开销。目前仓库包含本地 Docker 轮询基线、负载均衡器、负载生成器和指标采集脚本；已完成 18 分钟本地基线实验，4,440 次请求全部成功，平均响应时间 184 ms、P95 为 203 ms，容器数量按 1 → 2 → 3 → 2 → 1 变化。AWS 事件驱动控制器及完整对比实验仍处于规划阶段。当前应用入口的运行限制见上文。
