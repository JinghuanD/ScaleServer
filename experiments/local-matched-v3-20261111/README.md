# Local Docker experiment: matched polling and event notification (V3)

Event notification reduced controller evaluations from **780 to 4 per run** while the shared monitoring design produced **388 samples per run** in both modes. This comparison isolates how a controller learns of confirmed state changes: polling checks the current state every second; event mode receives notifications. Both designs monitor independently of scaling execution. Earlier experiments with a blocking baseline answered a different question.

## Workload and selection

Each formal run sent 1 request/s for 180 seconds, 5 requests/s for 300 seconds, then 1 request/s for 300 seconds: **780 seconds and 1,980 requests** per run. Both modes used the same sampler, CPU thresholds (above 70% for five consecutive samples, below 30% for ten), 15-second cooldown, scaling worker, and 15-second simulated action delay.

The formal set is `step-p1-01-v3` through `step-p1-03-v3` and `step-e-01-v3` through `step-e-03-v3`. The additional polling retry is supplementary; it does not replace the original second polling run. The earlier V2 step trial is diagnostic and the stable-load runs are outside this step-workload comparison.

| Metric | Polling (1-second interval) | Event-driven |
| --- | ---: | ---: |
| Total requests across three runs | 5,940 | 5,940 |
| Request success rate | 100% | 100% |
| Controller evaluations per run | 780 | 4 |
| Monitoring samples per run | 388 | 388 |
| Scaling operations per run | 1 scale-out, 1 scale-in | 1 scale-out, 1 scale-in |
| Mean confirmation-to-decision latency | 601.881 ms | 0.196 ms |
| Mean workload increase to scale-out decision | 9.845 s | 9.541 s |
| Mean workload decrease to scale-in decision | 20.840 s | 19.913 s |
| Mean request response time | 175.224 ms | 177.345 ms |
| Average of per-run P95 response times | 194.087 ms | 199.369 ms |

## Measurement and interpretation

Counts use the workload window in each manifest. Confirmation-to-decision uses the controller's elapsed time from confirmed condition to decision creation. Workload transitions are measured from the scheduled stage boundaries (manifest workload start plus 180 or 480 seconds) to the corresponding decision log timestamp. Both latency measures end at decision creation and exclude scaling execution and application readiness. Per-run P95 uses nearest rank; the table averages the three run-level values.

Event mode reduced controller evaluations by **99.49%**. Both modes still took the same number of CPU samples, so this percentage describes controller activity, not total CPU usage or monitoring cost. The workload-to-decision improvement was smaller because both modes still had to sample and confirm the threshold crossing. All 11,880 requests succeeded, all expected actions completed, and no controller thread errors were recorded. Event mode had slightly higher mean and P95 request latency in these runs.

## Relationship to the AWS experiments

The local comparison isolates notification and decision handling. The [AWS RequestCount comparison](../aws-requestcount-20261030/README.md) measures a longer path through metric publication, CloudWatch alarm evaluation, EventBridge delivery, and controller execution; EC2 readiness is observed separately after the capacity request. Faster notification after confirmation does not guarantee faster end-to-end scaling when metric availability, alarm confirmation, and provisioning contribute substantial delays.

[Per-run aggregates](run_summary.csv) were recalculated from the six original request CSVs, manifests, and controller logs. Raw logs and deployment metadata are not published. The [V3 implementation and tests](../../local_comparison/) are separate from the historical controllers.
