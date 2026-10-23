# Synchronized EC2 comparison: request diagnostics

Version: `ec2-sync-comparison-v1`. This snapshot was downloaded read-only from the existing control server on 2026-10-23. The four runs occurred on 2026-10-22 and 2026-10-23 according to their filenames. No experiment or cloud scaling action was executed during synchronization.

## Changes

- Added request tracing between the workload generator and load balancer using `X-Request-ID`.
- Recorded application processing time, backend forwarding time, forwarding attempts, and per-request diagnostic logs.
- Added an experiment runner with initial-state checks, an approximately five-second controller-to-workload launch interval, and saved process logs.
- Kept the scaling thresholds, cooldown, drain period, and workload unchanged.

## Experimental results

All runs used the same workload: **1 rps for 30 s -> 2 rps for 90 s -> 4 rps for 120 s -> 0.5 rps for 180 s**. Each submitted 780 requests over 420 seconds. Controllers ran for 480 seconds, with the workload launched approximately five seconds later. Recorded launch intervals were 5.008-5.031 seconds.

| Run | Successful requests | Mean latency (ms) | P95 latency (ms) | Samples | Maximum sample interval (s) | Samples during pending actions |
| --- | --- | --- | --- | --- | --- | --- |
| baseline_sync_1 | 780/780 | 536.23 | 1061.43 | 317 | 57.57 | 0 |
| event_sync_1 | 780/780 | 468.98 | 499.31 | 444 | 1.61 | 125 |
| event_sync_2 | 780/780 | 487.70 | 566.41 | 443 | 1.66 | 134 |
| baseline_sync_2 | 780/780 | 467.67 | 495.22 | 314 | 59.33 | 0 |

| Scaling metric | Baseline | Event-driven |
| --- | --- | --- |
| Successful scaling actions | 8/8 | 8/8 |
| Scale-out action duration | 14.58-15.11 s | 14.60-15.23 s |
| Scale-in action duration | 54.13-58.23 s | 54.07-64.38 s |

Scale-in durations include the configured **35-second drain period**. Action durations measure controller execution through completion, rather than AWS API response time alone. Sampling statistics cover each controller log, including time outside the request workload. P95 values are calculated per trial using the nearest-rank method.

## Validation

The four synchronized experiments ran in the order **Baseline -> Event -> Event -> Baseline**. All **3,120 requests succeeded**, and each run completed two scale-outs and two scale-ins, totaling **16 successful actions**. All request trace IDs matched the returned trace IDs and entries in the downloaded load-balancer diagnostics.

The baseline recorded 314-317 samples per run, with maximum gaps of 57.57-59.33 seconds. Event mode recorded 443-444 samples, with maximum gaps of 1.61-1.66 seconds and 125-134 samples while actions were pending. Scale-out durations were similar, approximately 14.6-15.2 seconds.

## Findings

Event mode maintained monitoring during scaling operations, while baseline paused sampling during synchronous execution. Request latency did not show a consistent winner: Event had lower mean and P95 latency in the first comparison, while Baseline had lower latency in the second. These runs do not establish a consistent latency advantage or faster instance startup for either mode.

The event controller continues sampling during actions but suppresses new scaling decisions until the pending action finishes. The experiment validates monitoring continuity rather than adaptive action cancellation or concurrent scaling decisions.

## Limitations and next step

There were two runs per mode, and the workload generator shared the controller EC2 instance. Treat these results as descriptive rather than evidence of statistical significance. The planned next experiment introduces a load rebound during scale-in draining to measure how each controller observes and responds to changing load; it has not been run as part of this update.

## Evidence

`run_summary.csv` maps each label to its experiment and request directories. `data/experiment_*/run.json` records completion status and launch timing; adjacent stdout logs preserve both processes. `data/latency_*/` contains request diagnostics and summaries. Controller JSONL files preserve sampling and actions. Load-balancer logs span longer sessions and include preflight and other requests: join on the formal runs' `trace_id` values rather than counting every diagnostic line as an experimental request.

`controller/`, `loadbalancer/`, and `workload/` preserve exact downloaded source bytes. The same files are installed at the repository's operational paths. `SHA256SUMS` checks this snapshot. The deployment-specific worker IDs and addresses remain in the source. Worker application/metrics services and full infrastructure configuration are still not included, as documented for Stage 2.

## Running on the existing deployment

The runner is intended for the existing Linux control host with its IAM role, dependencies, and worker services. It checks that Worker 1 is running, Workers 2/3 are stopped, no EC2 controller process is found, and the load balancer exposes only Worker 1 and echoes request IDs. Its preflight sends one `/work` request, which is separate from the 780 measured requests. It does not reset instance states automatically.

Start the updated load balancer in its usual service/terminal. Then, from the repository root, run one experiment at a time:

```bash
python workload/run_cloud_experiment.py baseline --label baseline_sync_3
# For a separate event-mode run, after restoring the required initial state:
python workload/run_cloud_experiment.py event --label event_sync_3
```

The runner starts both controller and workload, so do not launch a second controller manually. Wait for completion before another run. On interruption it signals the children and waits for an already-started controller action to finish. New process logs are saved under `data/experiment_<timestamp>/`, request data under `data/latency_<timestamp>/`, and load-balancer diagnostics under `data/lb_diagnostics_<timestamp>.jsonl`.
