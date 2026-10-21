# Cloud Stage 2: Multi-EC2 Autoscaling Experiment

This snapshot contains the deployed controller code and six formal trials recorded on 2026-10-21. Dates follow the source log filenames; log timestamps are Unix timestamps. Unlike Stage 1, this experiment starts and stops existing EC2 worker instances. It does not create new instances or implement CloudWatch/EventBridge integration.

## Deployment and policy

A control EC2 host runs the controller, Flask load balancer, and workload generator in us-east-2. Worker 1 remains running; Workers 2 and 3 are started and stopped on demand. Application, metrics, and load-balancer ports are 5000, 9100, and 8000. The recorded environment used Python 3.11.16 and an IAM role on the control host. Instance IDs and private addresses are retained in the deployed source snapshot and must be configured for another deployment.

Scale out after five fresh, valid CPU samples above 35%; scale in after ten below 12%. Cooldown is 30 seconds. The loop waits one second in addition to collection time. Scale-in disables the backend, waits 35 seconds for draining, then stops the instance. Both modes use the same action code: baseline executes synchronously, while event mode keeps sampling during actions and suppresses additional scaling decisions.

The workload is **1 rps for 30 seconds -> 2 rps for 90 seconds -> 4 rps for 120 seconds -> 0.5 rps for 180 seconds**, totaling **420 seconds and 780 requests per trial**. Controllers run for 480 seconds. Traffic originates on the control EC2 host and targets `127.0.0.1:8000`; this is not an external-client end-to-end test.

## Six-trial results

| Trial | Successful / total requests | Mean latency (ms) | P95 (ms) | Maximum sample interval (s) | Samples while an action was pending |
| --- | --- | --- | --- | --- | --- |
| baseline_step_1 | 780 / 780 | 462.72 | 489.26 | 57.32 | 0 |
| baseline_step_2 | 780 / 780 | 468.90 | 478.94 | 65.60 | 0 |
| baseline_step_3 | 780 / 780 | 486.60 | 515.44 | 57.32 | 0 |
| event_step_1 | 780 / 780 | 478.08 | 515.25 | 1.63 | 125 |
| event_step_2 | 780 / 780 | 649.95 | 2153.29 | 1.65 | 128 |
| event_step_3 | 780 / 780 | 586.33 | 1381.54 | 1.62 | 122 |

All **4,680 requests succeeded**. Each trial completed two successful scale-outs and two successful scale-ins, and Worker 3 served requests. Sampling statistics cover the matched controller logs, including their pre/post-workload portions; only the six logs identified in `results/run_summary.csv` enter this comparison.

Event mode preserved monitoring continuity, with maximum sample gaps of approximately **1.62-1.65 seconds**, compared with **57.32-65.60 seconds** for baseline. Baseline gaps include the 35-second drain wait and EC2 stop wait.

The results show **no request-latency advantage for event mode**. Event trials 2 and 3 had 54 and 53 requests above one second; 49 and 51 occurred during the 4 rps stage. In trial 2, 52 slow requests came from Worker 2; in trial 3, 34 came from Worker 2 and 18 from Worker 1. The most pronounced slow-request period followed the increase to 4 rps before Worker 3 was ready, with some variability remaining afterward. CPU-credit evidence was not collected, so credit exhaustion or throttling cannot be established as the cause.

Action start to application health took approximately **13.6-17.4 seconds** for scale-out. There is no evidence here that event mode materially accelerated instance startup. EC2 `running`, application health, and `backend_enabled` are separate milestones; backend enablement does not confirm that the load balancer has already discovered the worker.

Three trials per mode are a limited sample. The controller and load generator share a host, and manual controller-to-workload startup offsets were approximately 3.6-9.5 seconds. These observations do not establish causality or statistical significance. Report trial P95 values individually; their average is not a pooled-request percentile.

## Evidence and reproducibility

- `src/` preserves the three supplied Python files byte-for-byte. Their operational copies are merged into the repository's `controller/`, `loadbalancer/`, and `workload/` directories.
- `experiments/20261021/<label>/` contains request CSVs and summaries.
- `experiments/20261021/controller_logs/` also includes earlier debugging and low-load logs. Use the explicit mapping in `results/run_summary.csv` to select formal trials.
- `results/run_summary.csv` and `stage_summary.csv` contain derived statistics; `results/action_timeline.json` records the formal action timelines, relative to the first scheduled request.
- `SOURCE_MANIFEST.json` retains the supplied provenance and code hashes. `SOURCE_SHA256SUMS` preserves the original package checksums; `SHA256SUMS` checks the translated snapshot. Only documentation/checksum files were changed within this snapshot.

The supplied package does not include the worker application, Dockerfile, worker metrics agent, systemd units, image archive, complete dependency lockfile, or AWS resource export. It cannot independently recreate the deployment. Historical local Docker components elsewhere in this repository are not substitutes for the missing deployed worker files. Export these from the existing servers before attempting a clean rebuild. See [RUNBOOK.md](RUNBOOK.md) for the existing-deployment workflow.
