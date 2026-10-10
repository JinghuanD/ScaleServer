# ScaleServer

**Event-Driven Auto Scaling Controller for AWS EC2**

ScaleServer is an experimental project investigating how event-driven auto scaling compares with a periodic polling baseline under identical, reproducible HTTP workloads. The goal is to measure scaling responsiveness, application performance, resource efficiency, and controller overhead rather than assume that one architecture performs better.

The project direction follows Jinghuan Ding's proposal, *Event-Driven Auto Scaling Controller for AWS EC2*. This repository currently contains a local Docker polling baseline, a local event controller, and experiment tooling; the AWS event-driven architecture is planned work.

## Baseline experiment: local Docker auto-scaling

Completed an 18-minute polling-based experiment with three Docker containers.

- **Workload:** `1 -> 2 -> 3 -> 5 -> 8 -> 10 -> 5 -> 2 -> 1 req/s`, with 120 seconds per stage.
- **Scaling policy:** scale out after 5 consecutive CPU samples above 70%; scale in after 10 consecutive samples below 30%. Cooldown: 15 seconds; capacity: 1-3 containers.
- **Results:** all **4,440 requests** succeeded and were recorded. Mean response time: **184 ms**; P95: **203 ms** (nearest-rank percentile of successful request response times).
- **Scaling behavior:** container count changed from **1 -> 2 -> 3 -> 2 -> 1**. All three containers served requests.
- **Fixes:** added backend health checks, corrected concurrent retry handling, and introduced per-request logging with completion tracking.

This run provides a local baseline for comparison with the upcoming event-driven controller, not evidence of event-driven performance improvements. Scaling analysis uses monitoring samples between the first scheduled request and the last completed request, excluding idle monitoring data after the workload ended.

### Results and experiment history

The successful run is recorded in [summary.json](data/run_20261006_155231_781230/summary.json) and [requests.csv](data/run_20261006_155231_781230/requests.csv). Use the archived monitoring data for this baseline; the live `data/results.csv` changes with subsequent experiments. The [Version 3 archive](Baseline_Experiment_3.zip) preserves request records, summary, monitoring data, and controller/workload logs together.

| Version | Issue or outcome | Resolution |
| --- | --- | --- |
| **1 — Request routing issues** | Requests attempted stopped containers, adding connection delays. A shared round-robin iterator could make concurrent retries miss an available backend. Statistics lacked individual latency records and could omit outstanding requests at shutdown. | Switched to `127.0.0.1`, used a per-request backend snapshot and retry order, added health checks, and logged individual requests while waiting for outstanding work to finish. |
| **2 — Load balancer unreachable** | All 4,440 requests failed while application CPU remained near idle. Request logs showed connection timeouts to port 8000, so this run could not measure auto-scaling behavior. | Restarted the load balancer and verified both `/health` and `/work` before rerunning. |
| **3 — Successful baseline** | All 4,440 requests succeeded with complete records and the expected 1 -> 2 -> 3 -> 2 -> 1 scaling sequence. | Retained as the successful local baseline for future comparison. |

Earlier evidence is retained in the [Version 1 archive](Baseline_Experiment_1.zip) and [Version 2 request logs](data/run_20261006_150905_148956/requests.csv). Other timestamped run directories contain connectivity checks or an interrupted run; they are not part of the successful 18-minute baseline.

## First comparison round: local event-driven experiment

**The event-driven auto-scaling run completed successfully:** the full workload executed, every request succeeded, and container capacity increased and decreased with demand.

| Metric | Result |
| --- | --- |
| Workload | 1 -> 2 -> 3 -> 5 -> 8 -> 10 -> 5 -> 2 -> 1 req/s |
| Duration | 120 seconds per stage; 18 minutes total |
| Total requests | 4,440 |
| Success / failure rate | 100% / 0% |
| Mean response time | 185.52 ms |
| P95 response time | 205.43 ms |
| Maximum request queue delay | 3.28 ms |
| Container count | 1 -> 2 -> 3 -> 2 -> 1 |
| Scaling actions | 2 scale-outs and 2 scale-ins; all successful |

### Scaling behavior

- The two scale-outs took **12.88 seconds** and **12.80 seconds**, measured from the first qualifying above-threshold CPU sample to the new container passing its application health check.
- The two scale-ins took **30.82 seconds** and **30.70 seconds**, measured from the first qualifying below-threshold CPU sample to completion and verification of the container stop.
- The three containers served **2,568**, **1,467**, and **405** requests, confirming that the added containers actually served traffic.
- No additional repeated scale-in/scale-out cycles occurred during the workload window.

The scale-out readiness measurements join `event_created.first_threshold_timestamp` to `application_ready.ready_observed_timestamp` using `action_id`. Scale-in measurements use `action_finished.first_threshold_to_finish_seconds`. These endpoints distinguish application readiness from Docker running-state verification.

This run validates the local event controller's monitoring, event delivery, scaling execution, and load distribution, with zero request failures even during the highest **10 req/s** stage. **Whether it outperforms the baseline remains to be established through repeated experiments and consistent comparison metrics.** Monitoring still polls Docker CPU metrics; this is a local queue-based event controller, not the proposed AWS CloudWatch/EventBridge deployment.

The preserved [experiment directory](data/Event_Experiment_20261008_210823/) contains the [request summary](data/Event_Experiment_20261008_210823/summary.json), [per-request records](data/Event_Experiment_20261008_210823/requests.csv), [controller events](data/Event_Experiment_20261008_210823/event_20261008_165641_861697.jsonl), and [resource metrics](data/Event_Experiment_20261008_210823/results.csv). A [ZIP archive](data/Event_Experiment_20261008_210823.zip) is also available. Restrict resource and controller analysis to the workload window; the preserved logs include monitoring outside that window.

## Event-driven experiment: three repeated trials

The event-driven autoscaling controller was evaluated across **three repeated trials**, each using the same 18-minute workload: **1 -> 2 -> 3 -> 5 -> 8 -> 10 -> 5 -> 2 -> 1 requests/s**, with each stage lasting 120 seconds. Trial 1 is the run described above, not an additional experiment.

| Metric | Trial 1 | Trial 2 | Trial 3 |
| --- | --- | --- | --- |
| Total requests | 4,440 | 4,440 | 4,440 |
| Failed requests | 0 | 0 | 0 |
| Mean response time | 185.52 ms | 183.74 ms | 184.54 ms |
| P95 response time | 205.43 ms | 203.32 ms | 205.26 ms |
| First scale-out latency [1] | 12.88 s | 12.76 s | 12.75 s |
| Second scale-out latency [1] | 12.80 s | 12.56 s | 12.55 s |
| First scale-in latency [2] | 30.82 s | 30.80 s | 30.78 s |
| Second scale-in latency [2] | 30.70 s | 30.76 s | 30.73 s |
| Estimated container-seconds [3] | 1,716.45 | 1,716.66 | 1,716.65 |

1. From the first above-threshold CPU sample to successful health verification of the new container.
2. From the first below-threshold CPU sample to completion and verification of the container stop operation.
3. Estimated by integrating the running-container count using scaling completion timestamps over the fixed 1,080-second interval beginning with the first scheduled request. Each run starts with one container; idle monitoring outside this interval is excluded. This measures container occupancy, not CPU consumption or application-ready time.

All three trials followed the same container sequence, **1 -> 2 -> 3 -> 2 -> 1**, with two successful scale-out and two successful scale-in operations. Newly started containers actively served requests, and no additional scaling oscillation, controller errors, or application readiness timeouts were recorded.

Across the three trials, **all 13,320 requests succeeded**. The average of the trial mean response times was **184.60 ms**, with a sample standard deviation of **0.89 ms**. The average of the three trial P95 values was **204.67 ms**; this is an average of per-trial percentiles, not a pooled-request P95. Aggregate statistics use the unrounded per-trial values.

These results demonstrate consistent operation and repeatable performance under the tested workload. The three-trial Baseline comparison below uses consistent measurement endpoints to evaluate the observed differences.

Preserved request records, summaries, resource metrics, and controller logs are available for [Trial 1](data/Event_Experiment_20261008_210823/), [Trial 2](data/Event_Experiment_20261009_154150/), and [Trial 3](data/Event_Experiment_20261009_162652/).

## Comparison of Baseline and Event-Driven Autoscaling

Three trials were conducted for each controller using the same 18-minute workload. Request rates followed nine stages: **1, 2, 3, 5, 8, 10, 5, 2, and 1 requests per second**, with each stage lasting 120 seconds. Each trial generated 4,440 requests. The Baseline trials in this comparison are separate from the earlier historical baseline experiment above.

| Controller | Trial | Successful requests | Failed requests | Mean response time (ms) | P95 response time (ms) |
| --- | --- | --- | --- | --- | --- |
| Baseline | 1 | 4,440 | 0 | 184.61 | 207.98 |
| Baseline | 2 | 4,440 | 0 | 184.05 | 203.07 |
| Baseline | 3 | 4,440 | 0 | 184.12 | 202.12 |
| Event-driven | 1 | 4,440 | 0 | 185.52 | 205.43 |
| Event-driven | 2 | 4,440 | 0 | 183.74 | 203.32 |
| Event-driven | 3 | 4,440 | 0 | 184.54 | 205.26 |

Both controllers completed two scale-out and two scale-in actions successfully in every trial. The container count followed the same sequence: **1 -> 2 -> 3 -> 2 -> 1**. All trials used identical scaling thresholds, consecutive-sample requirements, and cooldown settings.

| Metric | Baseline | Event-driven |
| --- | --- | --- |
| Total requests across three trials | 13,320 | 13,320 |
| Request success rate | 100% | 100% |
| Average trial mean response time | 184.26 ms | 184.60 ms |
| Average trial P95 response time | 204.39 ms | 204.67 ms |
| Mean scale-out response time | 12.77 s | 12.72 s |
| Mean scale-in response time | 30.76 s | 30.77 s |
| Estimated container-seconds per trial | 1,716.55 | 1,716.59 |

Scale-out response time was measured from the first qualifying high-CPU sample to successful application readiness. Scale-in response time was measured from the first qualifying low-CPU sample to action completion. Each scaling mean includes six actions of that type across three trials. Container-seconds were estimated from container-count changes recorded at action completion, within the fixed 1,080-second workload interval starting at the first scheduled request. Idle periods were excluded. The reported average P95 is the average of the three trial-level P95 values, rather than a pooled percentile. All aggregate values were calculated before rounding.

The results show **comparable performance under the tested workload**. The event-driven controller's average scale-out response was approximately **0.05 seconds faster**, while its mean request response time was approximately **0.34 milliseconds higher**. Estimated container occupancy was nearly identical. These small observed differences do not establish a clear performance advantage for either controller. The experiments demonstrate reliable operation of both implementations under the tested conditions. This comparison evaluates the local implementations; the event controller still polls Docker metrics and delivers events through an in-process queue.

### Scaling Response Time Breakdown

To investigate the sources of scaling latency, response time was divided into three components: threshold confirmation, controller dispatch, and action execution. Threshold confirmation was measured from the first qualifying CPU sample to decision or event creation. Dispatch latency was measured from decision or event creation to action start. Execution time was measured from action start to observed application readiness for scale-out, or action completion for scale-in.

The table below presents a preliminary breakdown from one baseline trial and one event-driven trial. **It does not represent the three-trial averages.**

| Controller | Action | Threshold confirmation (s) | Dispatch latency (ms) | Execution time (s) |
| --- | --- | --- | --- | --- |
| Baseline | Scale-out 1 | 12.020 | 95.78 | 0.626 |
| Baseline | Scale-out 2 | 12.059 | 98.77 | 0.396 |
| Baseline | Scale-in 1 | 27.157 | 99.81 | 3.522 |
| Baseline | Scale-in 2 | 27.114 | 95.43 | 3.529 |
| Event-driven | Scale-out 1 | 12.027 | 99.27 | 0.623 |
| Event-driven | Scale-out 2 | 12.054 | 100.89 | 0.395 |
| Event-driven | Scale-in 1 | 27.147 | 98.16 | 3.534 |
| Event-driven | Scale-in 2 | 27.123 | 97.70 | 3.505 |

Threshold confirmation accounted for most of the observed scaling response time. Dispatch latency was approximately 100 ms for both controllers in these trials. Event delivery took approximately 0.20-0.31 ms, but this measured only the interval between event creation and receipt, rather than the complete dispatch interval. These observations help explain why rapid event delivery did not produce a clear reduction in overall scaling response time.

### Baseline comparison data

Download the [three-run request archive](data/Baseline_Three_Runs_Requests.zip), containing `requests.csv` and `summary.json` for each trial. Corresponding controller logs provide scaling and readiness timestamps:

| Trial | Request directory within the ZIP | Controller log |
| --- | --- | --- |
| 1 | `run_20261009_165615_763818/` | [Trial 1 log](data/baseline_20261009_165440_012234.jsonl) |
| 2 | `run_20261009_200153_334091/` | [Trial 2 log](data/baseline_20261009_200128_096106.jsonl) |
| 3 | `run_20261010_145556_305558/` | [Trial 3 log](data/baseline_20261010_145429_577829.jsonl) |

Event-driven source data is linked in the three-trial section above.

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
| `controller/event_controller.py` | Local event controller: polls CPU in a monitoring thread, queues scaling events for execution, and records structured JSONL logs. |
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

For an event-controller experiment, replace `python controller/baseline_controller.py` with `python controller/event_controller.py`; run only one scaling controller at a time. Readiness probes expect HTTP 200 with a JSON `status` of `healthy` from each backend's `/health` endpoint.

## Logging system

The logging system has three parts: **request logs, resource metrics, and controller action logs**. They are correlated using timestamps.

| File | Generated by | Contents | Purpose |
| --- | --- | --- | --- |
| `data/run_<timestamp>/requests.csv` | Workload generator | Request ID, stage, target rate, timestamps, queue delay, latency, status code, container ID, and errors | Analyze success rate, response time, and request distribution |
| `summary.json` in the same run directory | Workload generator | Total requests, successful and failed requests, recording completeness, mean and P95 response time, and maximum queue delay | Summarize the experiment |
| `data/results.csv` | Metrics collector | Sampling time, elapsed time, average CPU utilization, and running container count | Track resource utilization and capacity changes |
| `data/event_<timestamp>.jsonl` | Event controller | CPU samples, event creation and receipt, scaling results, and timing measurements | Analyze scaling triggers and execution |
| `data/baseline_<timestamp>.jsonl` | Current baseline controller | Structured samples, scaling decisions/actions, readiness observations, and timing measurements | Analyze baseline behavior with controller run/action IDs |
| Manually saved `baseline_*_controller.log` | Baseline controller console output | CPU utilization, container count, and start/stop actions | Review earlier baseline behavior; older console logs have limited timing detail |
| Manually saved `*_workload.log` | Workload generator console output | Stage transitions, progress, and final summary | Support troubleshooting; CSV files remain the primary analysis source |

### Event controller entries

The local event controller records the following lifecycle entries. Its monitoring thread still polls Docker metrics; events are delivered through an in-process queue, rather than through AWS EventBridge.

| `kind` | Meaning | Relationship and timing fields |
| --- | --- | --- |
| `sample` | A CPU and container-count observation | Consecutive samples satisfying the threshold condition trigger an event, subject to cooldown and capacity limits. |
| `event_created` | Creation of a `scale_out` or `scale_in` event | `first_threshold_timestamp` records the first threshold-crossing observation in the qualifying sequence; `created_timestamp` records event creation. |
| `event_received` | Receipt of the event by the execution thread | `delivery_seconds` measures the time from event creation to receipt. |
| `action_started` | Start of scaling execution | Records `action_id`, the container list before execution, and `action_started_timestamp`. |
| `action_finished` | Completion of the scaling action and container-state verification | Records `success`, container lists `before` and `after`, `command_seconds`, and `first_threshold_to_finish_seconds`. |
| `application_ready` | A newly started container passes its HTTP health check | Records `action_id`, container, `ready_observed_timestamp`, and `action_start_to_ready_seconds`. |
| `application_ready_timeout` | Application readiness was not confirmed within the timeout | Records the affected container and action for troubleshooting. |
| `controller_started` / `controller_stopped` | Controller lifecycle | Records policy settings on startup or a stop reason. |

Exceptions are recorded separately as `monitor_error` or `action_error` entries. `command_seconds` measures the scaling function call; `first_threshold_to_finish_seconds` extends from the first qualifying observation through the post-action Docker-state check.

### Tracing a scale-out operation

For example, a scale-out operation can be correlated as follows:

1. `requests.csv` shows the workload entering the **5 req/s** stage.
2. `sample` entries show CPU utilization remaining above **70%** for the required number of consecutive samples.
3. `event_created` records the scale-out event.
4. `event_received` records its receipt by the execution thread.
5. `action_finished` shows the running containers changing from `app1` to `app1` + `app2`.
6. Subsequent entries in `results.csv` show the container count increasing to **2**.
7. Responses in `requests.csv` include app2's container ID, confirming that it is serving requests. Match the returned ID to the Docker container name when correlating the files.

This is an illustrative trace; the workload stage at which scaling occurs depends on the observed CPU samples.

### Correlation and analysis limitations

- Current controller logs include `run_id` and per-action `action_id`, allowing scaling and readiness entries to be joined directly. **There is still no shared experiment ID across request, resource, and controller logs**, so correlation between those files uses timestamps. Older controller logs may lack these IDs.
- Request and event logs use Unix timestamps, while `results.csv` uses local date-time strings without a time-zone offset. Interpret those strings in the recording host's time zone and convert all timestamps to a consistent time zone before analysis.
- Resource sampling occurs approximately every three seconds in the observed runs. The collector sleeps for one second in addition to Docker command execution time, so the interval is not fixed. The observed container-count change may appear later than the scaling action's completion.
- `action_finished` verifies Docker state; current controllers separately record application health readiness as `application_ready` after scale-out. Older logs lack this measurement. Docker action completion and application readiness must be compared as distinct endpoints.
- `results.csv` appends new records. Archive it before each experiment to avoid mixing runs, and restrict analysis to the workload window so that idle monitoring records after the workload ends are excluded.

## Planned evaluation

- Scaling responsiveness: detection latency, scaling reaction time, and instance provisioning time.
- Application performance: average and P95 response latency.
- Resource efficiency: instance count, scaling operations, and EC2 instance-hours.
- Controller overhead: metric queries, delivered events, and AWS API calls.

The local scripts currently collect only a subset of these measurements. A local event controller now separates polling-based monitoring from scaling execution through an in-process queue and waits for each action to finish before producing another event. AWS integration, robust duplicate-event handling for external events, and the full comparative evaluation remain future work, including integration with CloudWatch Alarm and EventBridge.

## Project summary

This project investigates event-driven autoscaling for AWS EC2 by comparing it with periodic polling under the same application and reproducible HTTP workloads. Evaluation covers scaling responsiveness, application performance, resource efficiency, and controller overhead. The repository includes a local Docker polling baseline, a local event controller, a load balancer, a workload generator, and metrics collection scripts. The completed 18-minute baseline processed all 4,440 requests successfully, with a mean response time of 184 ms, a P95 of 203 ms, and a container-count sequence of 1 -> 2 -> 3 -> 2 -> 1. The local event-driven experiment also completed successfully, as detailed above. AWS integration and the full comparative evaluation remain future work. See the application entry-point limitation above before attempting to reproduce the experiments.

Resource usage analysis for the event-driven run must exclude the several hours of idle monitoring recorded after the workload ended.
