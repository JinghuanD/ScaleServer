# Local Docker autoscaling with blocking waits

[Blocking-wait implementation](../../local_resource_comparison_v2/) | [Per-run measurements](run_summary.csv) | [Original resource measurements](../local-resource-v1-20261111/) | [Matched V3 comparison](../local-matched-v3-20261111/)

The experiments compared polling and event-driven scaling controllers under matched local Docker conditions. Event notification substantially reduced repeated controller evaluations and the waiting time after a scaling condition was confirmed. The blocking-wait version reduced observable idle-thread CPU activity, but overall process CPU and memory differences between arms remained small.

This report covers three datasets: the matched V3 step comparison, the original resource-v1 measurements, and the completed blocking-wait resource comparison. Versions are reported separately. Instrumentation and waiting behavior differ, so results are not pooled across versions.

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

## Original resource measurement results

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

## Blocking wait implementation and results

The local-resource-v2-blocking implementation uses indefinite event waiting in the event decision thread and a blocking action queue in both arms. Shutdown explicitly wakes the decision thread, stops decision production, and sends a FIFO stop marker to the worker. Accepted actions finish before normal worker exit. The main thread also waits until the remaining experiment deadline instead of waking every 0.2 seconds.

Thresholds, confirmation rules, cooldown, workload, HTTP transport and the artificial action delay were retained. Each manifest records the controller version, code hashes and software environment. The controller source hash was identical across all 12 blocking-version runs; all runs recorded normal controller shutdown.

| Profile and arm | CPU time s | CPU % | Mean RSS MiB | Control thread CPU s |
| --- | --- | --- | --- | --- |
| Stable polling | 3.562 | 0.396 | 35.098 | 0.151 |
| Stable event | 3.417 | 0.380 | 35.242 | 0.000 |
| Step polling | 2.906 | 0.373 | 35.123 | 0.104 |
| Step event | 2.896 | 0.371 | 34.859 | 0.000 |

All values are means of three runs. A thread CPU value of 0.000 means no increase was recorded at the operating system accounting resolution; it is not proof of zero executed work. Stable event runs had no controller evaluations, while step event runs had four each. Polling retained 900 and 780 evaluations, respectively. Both step arms completed one scale-out and one scale-in per run.

| Profile and arm | Run 1 CPU s | Run 2 CPU s | Run 3 CPU s |
| --- | --- | --- | --- |
| Stable polling | 3.469 | 3.391 | 3.828 |
| Stable event | 3.219 | 3.297 | 3.734 |
| Step polling | 2.891 | 2.938 | 2.891 |
| Step event | 2.734 | 3.078 | 2.875 |

In the blocking-version step runs, mean confirmation-to-decision latency was 619.876 ms for polling and 0.156 ms for event-driven control. The notification response advantage remained, but this metric excludes detection, action execution and readiness.

One client request failed in resource-v2-step-p1-01-blocking01: request 1117, during the 5 req/s stage, encountered WinError 10048 while connecting to the local load balancer on port 8000. There was no scaling action record within 25 seconds of its start. The error alone does not establish port exhaustion or a scaling defect. It is retained in the results: 17,279 of 17,280 requests succeeded, or 99.994%. No action failure or controller thread error was recorded.

## Interpretation and measurement limits

| Profile and arm | Original CPU s | Blocking CPU s | Blocking Docker CLI CPU s |
| --- | --- | --- | --- |
| Stable polling | 4.120 | 3.562 | 39.438 |
| Stable event | 4.964 | 3.417 | 39.333 |
| Step polling | 3.531 | 2.906 | 33.953 |
| Step event | 3.411 | 2.896 | 34.891 |

Both arms used less controller-process CPU in the blocking-version runs. However, action-worker and main-thread waiting were also changed in both arms, and the versions were measured at different times. The full before-and-after difference cannot be attributed solely to indefinite event waiting. The new thread-level observations are consistent with removing idle timeout wakeups.

Within the blocking version, event-driven control used about 0.146 fewer process CPU seconds than polling over the stable workload window, and about 0.010 fewer seconds over the step workload window. Differences were small relative to run-to-run variation and shared monitoring costs. Memory usage was similar, with no consistent event-driven reduction.

Docker CLI CPU remained much higher than controller-process CPU in all conditions. This was a substantial observable collection cost, but CLI accounting does not include Docker Desktop backend or application-container work. Only calls fully contained in the workload window were summed; two calls crossed boundaries in every run and were listed separately. These measurements exclude the load balancer, workload generator and observer process.

RSS is shared process memory; separate thread memory is not reported. The peak is a sampled maximum, not a guaranteed instantaneous maximum. CPU accounting has finite resolution. Polling also generates more log records, so these values describe the implementation including logging and synchronization, rather than pure decision logic. Three runs per condition support descriptive comparison, not a strong claim about very small differences.

## Conclusions and relationship to AWS

Event notification substantially reduced repeated evaluations and confirmation-to-decision waiting. Blocking waits removed observable idle decision-thread CPU growth at the measurement resolution. Nevertheless, the experiments do not demonstrate a large overall CPU or memory benefit, and the original matched V3 runs did not demonstrate improved application response latency.

The local design still uses periodic metric collection. In the AWS architecture, metric availability and alarm confirmation precede notification delivery, while provisioning and health checks follow the scaling decision. Fast local notification therefore does not guarantee faster end-to-end AWS scaling. Lambda is invoked by events and does not need an idle blocking-wait thread. This report does not infer cloud cost savings from local resource measurements.

Sources: uploaded local comparison archive 20261111_142536, original resource archive 20261111_223802, and blocking archive 20261113_142520. All preselected runs and the isolated failed request are retained.
