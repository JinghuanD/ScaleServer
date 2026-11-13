# Scaling response from workload change to application readiness

A scale-out is useful to the application when the added capacity can serve traffic. The experiments observe different points along that path. For scale-in, the final milestone is drain or stop completion. Request latency is the time for an individual HTTP request and is reported separately from both scaling timelines.

## Local Docker experiments

### From a workload step to a scaling decision

The matched V3 experiment isolates how a controller learns that a threshold has been confirmed. Both arms used the same monitor, consecutive-sample rules and independent action worker. Each arm ran three trials of 1 → 5 → 1 requests/s. The monitor collected 388 observations per trial. Collection took about two seconds per observation, even though the requested period was one second.

| Stage | Polling, 1-second interval | Event notification |
| --- | ---: | ---: |
| Load increase → high-CPU confirmation (derived mean) | 9.551 s | 9.541 s |
| High-CPU confirmation → scale-out decision | 294.105 ms | 0.188 ms |
| Load increase → scale-out decision | 9.845 s | 9.541 s |
| Load decrease → low-CPU confirmation (derived mean) | 19.930 s | 19.913 s |
| Low-CPU confirmation → scale-in decision | 909.658 ms | 0.205 ms |
| Load decrease → scale-in decision | 20.840 s | 19.913 s |

Confirmation required five observations above 70% CPU or ten below 30%. The derived rows subtract the direction-specific decision wait from the corresponding workload-to-decision mean within the same trials. They include the application's response to the workload, the wait for a qualifying observation and the remaining confirmation samples. The event arm cut evaluations from 780 to four per run. The later blocking-wait trials reproduced the shorter confirmation-to-decision interval (619.876 ms polling, 0.156 ms event, averaged across directions), with small overall CPU and memory differences. [V3 records](local-matched-v3-20261111/) · [Original resource measurements](local-resource-v1-20261111/) · [Blocking-wait report and direction-specific V3 timings](local-resource-blocking-20261113/)

### From decision to execution and readiness

An earlier 18-minute Docker experiment measured the later stages. Across three trials per mode, the first qualifying CPU sample to successful application health check averaged **12.77 s polling and 12.72 s event** for scale-out. The corresponding first low sample to completed stop averaged **30.76 s and 30.77 s**. In the detailed breakdown from one trial per mode, threshold confirmation took about 12 s for scale-out and 27 s for scale-in. Decision or event creation to action start took 95–101 ms. From action start, scale-out reached observed health in 0.395–0.626 s and scale-in completed in 3.505–3.534 s. Docker running state, successful application health and the first request served by the new container are distinct observations. [Timing breakdown and endpoint definitions](../README.md#scaling-response-time-breakdown)

The matched V3 runs used a 15-second artificial action delay. Adding their workload-to-decision times to the earlier execution times would mix different policies and runs. A separate 10-second delay experiment showed that the modified event controller kept sampling during every action, while the synchronous baseline recorded no samples during execution. That establishes visibility of changing load during an action. In matched V3, all 11,880 requests succeeded, with mean HTTP latency of 175.224 ms polling and 177.345 ms event. [Monitoring continuity experiment](../README.md#controller-modification-and-monitoring-continuity-experiment) · [V3 results](local-matched-v3-20261111/)

## AWS experiments

### CloudWatch RequestCount, Lambda and EC2 Auto Scaling

The 24-minute workload stepped from 0.5 to 1.8 requests/s and back to 0.5. Both modes required two completed 60-second ALB metric windows to agree on desired capacity. Polling invoked Lambda on a schedule. Event mode also waited for a CloudWatch alarm state change before EventBridge triggered Lambda. The controller then queried its own windows and checked capacity, health and cooldown before requesting an ASG change. [Policy and workload](aws-requestcount-20261030/)

| Observed interval, three-run mean | Polling-60 | Event-driven | Polling-120 |
| --- | ---: | ---: | ---: |
| Load increase → expansion request | 234.9 s | 342.3 s | 240.6 s |
| Expansion request → all ALB targets observed healthy | 45.9 s | 97.9 s | Unreported |
| Load increase → all targets observed healthy | **280.8 s** | **440.2 s** | Unreported |
| Load decrease → contraction request | 154.9 s | 221.5 s | 200.8 s |
| Explicit controller SDK calls per run | 233.7 | 106.3 | 120.0 |

The end-to-end means sum the two expansion intervals **within each run** before averaging. Health was sampled about every five seconds. The scaling request marks the observable handoff from controller decision to AWS execution. The records do not separately time internal decision creation, ASG scheduling, instance boot and application registration. The contraction figures end at the request. Polling-120 comes from a separately conducted sensitivity study with author-supplied summaries. [Six-run measurements](aws-requestcount-20261030/run_summary.csv) · [Sensitivity study](aws-requestcount-20261030/)

| Event-driven expansion stage | Event-01 | Event-02 | Event-03 |
| --- | ---: | ---: | ---: |
| Load increase → alarm state change | 292.5 s | 287.0 s | 319.5 s |
| Alarm change → controller evaluation begins | 1.23 s | 1.27 s | 1.23 s |
| Evaluation begins → expansion request | 61.6 s | 0.9 s | 61.6 s |
| Expansion request → all targets observed healthy | 198.1 s | 40.9 s | 54.7 s |

The longest interval in each event run preceded the alarm transition. Metric publication and alarm evaluation cannot be timed independently from these records. After the alarm, evaluation began in about 1.2 seconds, including delivery and Lambda startup. In runs 01 and 03 the controller's own windows initially disagreed, prompting an SQS recheck about a minute later. Run 01 also had an unusually long post-request readiness interval. The six original runs served all 9,000 requests. Event mode made fewer explicit SDK calls, while Polling-60 reached observed health sooner on average in this workload. [Alarm-stage measurements](aws-requestcount-20261030/expansion_delay.csv) · [Health observations](aws-requestcount-20261030/run_summary.csv)

### Earlier EC2 deployments

| Deployment | What the experiment establishes |
| --- | --- |
| Single EC2 host, Docker containers | Both controllers served 300/300 requests. Event mode reduced the maximum sampling gap from 13.673 s to 3.638 s while mean HTTP latency rose from 1.304 s to 1.532 s. Capacity changed by starting containers on the existing host. |
| Direct multi-EC2 worker control | In six trials, action start to application health took 13.6–17.4 s. EC2 running state, application health, backend enablement and load-balancer discovery are separate milestones. All 4,680 requests succeeded. |
| Synchronized direct EC2 runs | All 3,120 requests succeeded. Scale-out actions lasted 14.58–15.23 s across modes. Scale-in lasted 54.07–64.38 s including a configured 35-second drain. Maximum sampling gaps were 57.57–59.33 s for synchronous polling and 1.61–1.66 s for event mode. |

These deployments tested whether monitoring continued while scaling was underway. Their action durations belong to different execution paths from the ASG experiment. [Single-host results](../README.md#cloud-stage-1-single-ec2-deployment-experiment) · [Direct EC2 results](ec2-stage2-20261021/) · [Synchronized runs](ec2-sync-comparison-v1/)

## Interpretation across environments

Local notification shortened the wait **after detection**. In the AWS alarm design, much of the wait occurred **before notification**, and instance and target health added a variable delay after the request. For comparing controller decision handling, the matched local confirmation-to-decision interval is the relevant measurement. For comparing usable cloud capacity, the workload-to-observed-health interval captures both the confirmation policy and downstream provisioning.
