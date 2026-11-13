# Local Docker controller resource measurements

[Resource-v1 source](../../local_resource_comparison/) | [Per-run resource measurements](run_summary.csv) | [Matched V3 experiment](../local-matched-v3-20261111/)

## Purpose and main findings

The experiments compared polling and event-driven scaling controllers under matched local Docker conditions. Event notification substantially reduced repeated controller evaluations and the waiting time after a scaling condition was confirmed. However, the resource measurements did not show a consistent reduction in CPU consumption or memory usage.

This report covers the completed V3 step-workload comparison and the subsequent resource-v1 measurements. It excludes the newer blocking-wait implementation, for which no experimental results are included. The two datasets are reported separately because resource instrumentation changes the execution environment.

## Experimental method

Both controllers used the same monitor, capacity policy and action worker. Monitoring and scaling execution ran in independent threads in both arms. Polling evaluated the latest shared state every second; the event-driven arm evaluated it after a notification of a condition change. Notifications could coalesce, and each evaluation used the current state.

| Setting | Configuration |
| --- | --- |
| Application | Flask HTTP application in Docker |
| Containers | app1 initially running; app2 and app3 stopped |
| Scaling policy | Scale out above 70% CPU for 5 samples; scale in below 30% for 10 samples |
| Timing | 15 s action delay; 15 s cooldown after action completion |
| Stable workload | 1 req/s for 900 s |
| Step workload | 1 req/s for 180 s; 5 req/s for 300 s; 1 req/s for 300 s |
| Replication | Three runs per arm in each formal comparison |

The CPU monitor requested a one-second sample period, but Docker CLI collection took approximately two seconds per observation. Consecutive confirmation therefore used fresh observations rather than a fixed one-second sampling assumption. Scaling decisions were suppressed during actions and cooldown, while monitoring continued.

## Matched V3 step workload results

The formal V3 comparison includes step-p1-01-v3 through step-p1-03-v3 and step-e-01-v3 through step-e-03-v3. Each run submitted 1,980 requests. All 11,880 requests succeeded, and each run completed one scale-out and one scale-in with no recorded action failures.

| Arm | Confirmation to decision ms | Increase to decision s | Decrease to decision s |
| --- | --- | --- | --- |
| Polling | 601.881 | 9.845 | 20.840 |
| Event | 0.196 | 9.541 | 19.913 |

The confirmation-to-decision metric isolates controller waiting after the shared detector confirms a condition. Workload-transition-to-decision also includes sampling and confirmation. Neither metric includes action execution or application readiness. The configured 15-second action delay is artificial and must not be interpreted as container startup time.

| Run | Mean response ms | P95 response ms | Decision delay ms out / in |
| --- | --- | --- | --- |
| step-e-01-v3 | 180.983 | 212.615 | 0.175 / 0.230 |
| step-e-02-v3 | 175.611 | 192.939 | 0.188 / 0.196 |
| step-e-03-v3 | 175.442 | 192.554 | 0.201 / 0.189 |
| step-p1-01-v3 | 173.364 | 193.666 | 275.374 / 889.676 |
| step-p1-02-v3 | 176.093 | 193.120 | 310.555 / 942.684 |
| step-p1-03-v3 | 176.216 | 195.474 | 296.385 / 896.614 |

Across the three runs, mean request response time was 175.224 ms for polling and 177.345 ms for event-driven control. The averages of per-run P95 values were 194.087 ms and 199.369 ms, respectively. These are not pooled P95 values. No application-latency improvement was demonstrated.

Each run recorded 388 monitoring samples. Polling performed 780 evaluations, compared with 4 for the event-driven arm, a reduction of 99.49%. The four notifications included condition clearing as well as actionable transitions; they did not represent four scaling actions. Two samples per run had no valid CPU value and overlapped scaling activity; no sample-collection exception was recorded in those entries.

The extra polling retry is retained as supplementary data. The earlier V2 step run, which contained one failed request before the drain fix, is diagnostic and is excluded from the formal V3 results.

## Resource measurement results

The resource-v1 dataset contains six stable runs and six step runs. All 17,280 requests succeeded. Stable runs performed no scaling actions; every step run completed one scale-out and one scale-in. No controller thread error or failed action was recorded.

An independent observer sampled the controller process approximately once per second and took snapshots at the workload boundaries. Process CPU time is the difference in cumulative user and system CPU seconds. Average CPU percentage uses one core as 100%. Mean RSS is time weighted; peak RSS is the largest sampled value.

| Profile and arm | CPU time s | CPU % | Mean RSS MiB | Control thread CPU s |
| --- | --- | --- | --- | --- |
| Stable polling | 4.120 | 0.458 | 34.681 | 0.161 |
| Stable event | 4.964 | 0.551 | 32.655 | 0.245 |
| Step polling | 3.531 | 0.453 | 34.829 | 0.151 |
| Step event | 3.411 | 0.437 | 34.651 | 0.193 |

Values above are means of three runs per condition. Per-run process CPU times are shown below so that variation is visible.

| Profile and arm | Run 1 CPU s | Run 2 CPU s | Run 3 CPU s |
| --- | --- | --- | --- |
| Stable polling | 3.953 | 4.562 | 3.844 |
| Stable event | 4.781 | 6.344 | 3.766 |
| Step polling | 3.453 | 3.578 | 3.562 |
| Step event | 3.141 | 3.672 | 3.422 |

Step-workload CPU usage was close between arms: 0.453% for polling and 0.437% for event-driven control. In stable runs, event-driven control used more process CPU on average, at 0.551% versus 0.458%. These small, variable results do not establish a consistent resource advantage.

Stable event RSS averaged 32.65 MiB, but the first run averaged 28.26 MiB while the other two averaged approximately 35 MiB. Therefore, the lower group mean is not evidence of a repeatable memory saving. Step-workload mean RSS was nearly identical, at 34.83 MiB for polling and 34.65 MiB for event-driven control.

Decision-thread CPU was not lower in the event arm. The measured times are small and subject to operating-system accounting granularity. Reducing evaluation counts must therefore be distinguished from reducing measured CPU time.

## Sources of overhead and limitations

| Profile and arm | Monitor thread CPU s | Docker CLI CPU s |
| --- | --- | --- |
| Stable polling | 2.896 | 41.208 |
| Stable event | 3.646 | 54.698 |
| Step polling | 2.453 | 33.719 |
| Step event | 2.427 | 33.526 |

Docker CLI CPU is reported separately from controller-process CPU. It includes only calls fully contained in the workload window; two calls crossed a boundary in each run and were not included in that sum. Windows accounting succeeded for all fully contained calls. The measurements exclude Docker Desktop backend work, the application containers, load balancer, workload generator and observer process.

The observed Docker CLI CPU time exceeded controller-process CPU time in every condition. This indicates that metric collection through repeated CLI calls was a substantial measured cost. It does not establish total machine overhead or allow backend CPU costs to be assigned to individual calls.

The measured event implementation used a 0.2-second timeout on event waiting. Its action worker, shared by both arms, also used a 0.2-second queue timeout, and the main thread checked elapsed time periodically. These operations could wake threads without a decision being required. They are implementation costs; the existing measurements do not isolate their causal contribution.

Logging was retained in both arms, with more records generated by polling. The reported CPU values therefore describe the implemented controller, including logging and synchronization, rather than the decision algorithm alone. Threads share process memory, so separate thread RSS is not reported. Three runs per condition provide descriptive evidence but limited support for small differences or broad generalization.

## Conclusions and relationship to AWS

The local comparison supports reduced evaluation activity and shorter decision waiting after confirmation. It does not demonstrate improved request latency or consistent CPU and memory savings. Shared periodic monitoring remains necessary in this design, even when decisions are notification driven.

This distinguishes the local mechanism from the complete AWS path. CloudWatch metric availability and alarm confirmation occur before notification delivery, while instance provisioning and health checks occur after the scaling decision. A fast local notification mechanism therefore does not imply faster end-to-end AWS scaling. This report does not estimate cloud cost savings from local process measurements.

Blocking-wait results should be reported separately. Existing runs remain valid evidence for the measured implementation.
