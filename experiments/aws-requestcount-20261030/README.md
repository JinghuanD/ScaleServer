# AWS RequestCount Comparison: Polling and Alarm-Driven Scaling

Six runs recorded on 2026-10-29 and 2026-10-30. This is a separate comparison from the historical fixed-worker experiments. All 9,000 workload requests succeeded. The supplied report was checked against request CSVs, controller API summaries, scaling-request timestamps, and alarm histories.

## AWS Deployment and Design Rationale
The deployment separated workload generation, application execution, scaling decisions, and capacity management. A dedicated control EC2 instance generated HTTP requests through an Application Load Balancer (ALB). Application instances belonged to an Auto Scaling Group (ASG), and each ran the same Dockerized Flask application deployed from a common Amazon Machine Image (AMI).

The ALB provided a fixed application endpoint while instances joined or left the group. The custom controller calculated the required capacity, while the ASG handled instance creation and termination. This separation allowed the experiment to distinguish controller decisions from infrastructure provisioning.

Both approaches used the same Lambda function and execution environment. In polling mode, an EventBridge scheduled rule triggered an evaluation every 60 seconds. In event-driven mode, CloudWatch Alarm state changes were delivered through EventBridge to trigger the controller.

DynamoDB stored duplicate-processing records, an execution lock, and scaling coordination state. SQS scheduled delayed rechecks for the event-driven controller when metrics were inconclusive, instances were not ready, or a cooldown was active. The polling baseline instead waited for its next scheduled evaluation.

Although the event-driven controller did not continuously query metrics, the underlying CloudWatch metric collection and alarm evaluation remained periodic.
## Scaling Policy and Experimental Controls
Both controllers queried the same ALB RequestCount metric using the Sum statistic over 60-second periods. Required capacity was calculated as:
```text
N_desired = ceil(request_rate / 1.5 requests/s per instance)
```

The result was constrained by the ASG minimum and maximum capacities. Both controllers required the latest two consecutive, completed metric windows to indicate the same desired capacity before proceeding.

The implementation also applied a 300-second cooldown, checked ASG instance and ALB target health, and used a shared execution lock. Capacity adjustments were deferred while existing instances were transitioning or not fully healthy. This conservative policy reduced overlapping operations but did not implement a separate fault-recovery strategy.

The event-driven architecture introduced an additional confirmation stage before controller evaluation. Its first capacity-boundary alarm required two 60-second data points above 90 requests per minute, equivalent to an average rate above 1.5 requests per second. Consequently, the experiment compared two complete AWS implementations rather than isolating only the notification mechanism.

The same application, AMI, instance type, load balancer, workload, and controller policy were retained across runs. Competing automatic scaling policies and earlier worker-control programs were excluded.
## Workload and Procedure
Each controller was tested three times using the following workload:
| Stage | Request rate | Duration |
| --- | --- | --- |
| Initial low load | 0.5 req/s | 240 seconds |
| High load | 1.8 req/s | 600 seconds |
| Recovery | 0.5 req/s | 600 seconds |

Each run lasted approximately 24 minutes and generated 1,500 requests. Runs were interleaved in the following order:

Polling-01 → Event-01 → Polling-02 → Event-02 → Polling-03 → Event-03

Each run began with one healthy application instance. Unique experiment identifiers separated logs and processing records. Request-level results, experiment timestamps, controller logs, and ASG and target-health snapshots were collected. Health observations were taken approximately every five seconds.

Earlier CPU-threshold and fixed-worker experiments were treated as development-stage tests and were not combined with this RequestCount-based comparison.
## Results
All six runs completed with 1,500 successful requests and no request failures. Health snapshots recorded the expected capacity transition from one instance to two and back to one.
| Metric (average across three runs) | Polling | Event-driven |
| --- | --- | --- |
| Successful requests per run | 1,500 | 1,500 |
| Mean response time | 493 ms | 509 ms |
| Average of per-run P95 response times | 539 ms | 688 ms |
| Metric queries per run | 24 | 10.7 |
| Explicit SDK API calls per run | 233.7 | 106.3 |
| Load increase to expansion request | 234.9 s | 342.3 s |
| Load decrease to contraction request | 154.9 s | 221.5 s |

The event-driven implementation reduced metric queries by approximately 55.6% and explicit SDK API calls by 54.5%. However, it submitted expansion requests approximately 107 seconds later and contraction requests approximately 67 seconds later on average.

Both approaches maintained successful application responses throughout the workload. The event-driven runs had higher observed mean and P95 response times, although these differences cannot be attributed solely to the triggering mechanism.
## Delay Analysis
CloudWatch alarm history was aligned with workload timestamps and controller logs to separate the main expansion-delay components.
| Event-driven run | Load increase to ALARM | ALARM to evaluation start | Evaluation start to expansion request |
| --- | --- | --- | --- |
| Event-01 | 292.5 s | 1.23 s | 61.6 s |
| Event-02 | 287.0 s | 1.27 s | 0.9 s |
| Event-03 | 319.5 s | 1.23 s | 61.6 s |

Most of the delay occurred before CloudWatch changed the alarm state. Once the alarm changed, controller evaluation began approximately 1.2 seconds later. This interval includes event delivery, Lambda startup, and processing before evaluation; it is not a measurement of pure EventBridge transmission time.

In Event-01 and Event-03, the controller’s newly queried windows did not initially agree on the higher capacity. It therefore scheduled one additional SQS recheck, adding approximately 60 seconds. These windows differed from the earlier windows that CloudWatch had used to establish the alarm state.

The logs also showed that values returned for an already-ended metric window could change in later queries. Therefore, a completed time interval did not necessarily mean that its metric value was fully available at the first query. The available records do not precisely separate metric-publication delay from alarm-evaluation delay.

Instance readiness introduced further variation. In Event-01, all targets were first observed healthy approximately 198 seconds after the expansion request, compared with approximately 41–55 seconds in the other five runs. This infrastructure variation is relevant when interpreting application response times.
## Interpretation and Limitations
The results show a trade-off in the tested configuration: the event-driven implementation reduced controller query overhead but responded more slowly to workload changes. The additional alarm-confirmation stage and, in some runs, a subsequent controller recheck contributed to the delay.

Several limits affect interpretation:
- Three runs per controller provide descriptive evidence but do not establish statistical significance.
- Explicit SDK counts include controller calls to DynamoDB, CloudWatch, ASG, ALB health APIs, and SQS sending. They exclude SQS service-side polling, independent observation traffic, and other service activity, so they do not represent total AWS cost.
- Health timestamps are sampled observations rather than exact instance-readiness times.
- T3 CPU-credit records were not included in the supplied data, so their possible effect could not be ruled out.
- The single-instance application already handled the selected workload without failures. These runs therefore provide limited evidence about performance under severe overload.
- Shared decision code did not make the complete confirmation paths identical: the event-driven approach passed through CloudWatch Alarm evaluation before reaching the controller.
The coordination mechanisms also have implementation boundaries. DynamoDB locking, duplicate records, and a write-ahead scaling intent reduce conflicting or repeated operations, but they do not provide a cross-service exactly-once transaction. The limit of 15 delayed rechecks applies to application-scheduled rechecks, not all AWS delivery retries. Lock expiry is checked explicitly and does not depend on immediate TTL deletion.
## Polling Interval Sensitivity Analysis

An additional experiment examined whether the event-driven controller's overhead advantage depended on the polling interval selected for the baseline. The polling interval was increased from 60 to 120 seconds, while the application, workload, capacity formula, 60-second metric windows, confirmation rule, cooldown, and health checks remained unchanged.

**Evidence status:** The additional results below were supplied as an experiment summary. Polling-120 request records, controller logs, and the new Polling-60 reference-run records were not supplied with this update and have not been independently recalculated. The original six-run results remain backed by the archived evidence described below. The imported controller configuration still defines a 60-second polling schedule; this documentation update does not change that default.

Three Polling-120 runs were conducted using the same 24-minute workload. All runs completed with 1,500 successful requests, no request failures, and the expected capacity transition from one instance to two and back to one. The supplied summary reports controller evaluation intervals of approximately 119.4-120.5 seconds.

| Metric (average across three runs) | Polling-60 | Polling-120 | Event-driven |
| --- | --- | --- | --- |
| Mean response time | 493 ms | 506 ms | 509 ms |
| Average of per-run P95 response times | 539 ms | 582 ms | 688 ms |
| Metric queries per run | 24 | 12 | 10.7 |
| Explicit SDK API calls per run | 233.7 | 120.0 | 106.3 |
| Load increase to expansion request | 234.9 s | 240.6 s | 342.3 s |
| Load decrease to contraction request | 154.9 s | 200.8 s | 221.5 s |

Increasing the polling interval halved metric queries and reduced explicit SDK API calls by approximately **48.6%**. This narrowed the event-driven controller's API-call advantage from approximately 54.5% against Polling-60 to **11.4% against Polling-120**.

Polling-120 submitted expansion requests approximately 101.7 seconds earlier than the event-driven controller on average. Its expansion delay was only 5.7 seconds greater than the original Polling-60 average, but this small difference should not be interpreted as evidence that longer polling intervals have negligible responsiveness costs. Scheduling phase, metric availability, and run-to-run variation affected the observations. The average contraction delay increased by approximately 45.9 seconds compared with Polling-60.

A further Polling-60 reference run was performed during the new experimental period. It completed all 1,500 requests successfully, with a mean response time of 494 ms and a P95 of 531 ms. These values were close to the original Polling-60 results, providing a limited consistency check rather than ruling out environmental differences. This single reference run is separate from the original three-run Polling-60 averages in the table.

The additional results show that reducing polling frequency can achieve much of the observed query-overhead reduction without introducing the full alarm-triggering delay of the tested event-driven architecture. The event-driven controller retained the lowest explicit API-call count, but Polling-120 provided a competitive balance between monitoring overhead and scaling responsiveness.

These findings remain specific to the tested configuration and small sample. The Polling-120 runs were conducted separately from the original comparison, and explicit SDK counts exclude service-side SQS polling and independent observation traffic. Consequently, they should not be interpreted as complete AWS cost measurements or as a universal ranking of polling and event-driven systems.

## Conclusion and Next Steps
Under the tested workload and AWS configuration, both controllers completed the expected scaling cycle without request failures. The event-driven controller required substantially fewer metric queries and explicit SDK calls, while the polling baseline submitted scaling requests sooner.

The next stage should preserve these six runs as the original comparison and investigate the confirmation path in a separately labelled experiment. Any change should define comparable decision conditions for both approaches rather than simply weakening the event-driven alarm to obtain a faster result. The additional Polling-120 results above extend this comparison; archiving their raw records and reproducing the calculations is the next verification step.

## Public evidence and local verification

This public report includes aggregate results in `run_summary.csv` and `expansion_delay.csv`. Raw request logs, health snapshots, Lambda logs, alarm histories, deployment manifests, and original archives are intentionally not published. The six-run aggregates were recalculated locally before sanitization; additional Polling-120 results remain author-supplied summary data pending raw-record verification.

`analyze.py` preserves the offline calculation method, but requires the withheld local evidence directories (`runs/`, `controller_logs/`, and `alarm_history/`). It cannot reproduce the aggregates from this public checkout alone. `SHA256SUMS` covers only the public files in this report directory.

The [sanitized controller package](../../cloud/polling_event/README.md) uses placeholder deployment identifiers. Its 27 offline tests use mocked AWS clients and can be run without the private records:

```sh
python -m unittest discover -s cloud/polling_event -p test_controller.py -v
```

No AWS configuration script or cloud experiment was executed during this repository update. The required deployed `workload/workload_calibration.py`, complete application/AMI build, and infrastructure definitions were not supplied; this is not a fresh-deployment kit.
