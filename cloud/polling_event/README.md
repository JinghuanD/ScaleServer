# Shared AWS Polling and Event-Driven Controller

This package implements the latest ALB RequestCount-based comparison using a single Lambda function with `polling` and `event` modes. Historical fixed-worker EC2 controllers remain elsewhere in the repository for prior experiments. See the [six-run English report](../../experiments/aws-requestcount-20261030/README.md) for measured results and limitations.

## Components

| File | Role |
| --- | --- |
| `lambda_function.py` | Shared capacity decisions, health checks, cooldown, lock, deduplication, scaling intent, and delayed event rechecks |
| `configure.py` | Installation, exclusive trigger selection, stopping, and status inspection of existing named AWS resources |
| `controller_permissions.json` | Lambda execution-role permissions |
| `observer_permissions.json` | Control-host observation permissions |
| `run_workload.py` | Run the existing calibration module and collect ASG/ALB health snapshots |
| `export_logs.py` | Export experiment-specific controller logs and explicit SDK call counts |
| `test_controller.py` | 27 offline tests with mocked AWS clients |

## Decision and coordination policy

Polling evaluates on an EventBridge 60-second schedule. Event mode evaluates CloudWatch alarm transitions delivered through EventBridge, with SQS rechecks delayed by 60 seconds when evaluation cannot finish. Both query ALB RequestCount sums over 60-second windows, require two consecutive completed windows to agree, and calculate `ceil(request_rate / 1.5)` bounded by the ASG minimum and maximum. Both enforce the same 300-second cooldown and ASG/target-health checks.

The first alarm boundary separately requires two 60-second points above 90 requests/minute. Alarm confirmation and controller confirmation are distinct stages, so the full architectures do not have identical notification/confirmation paths. Completed metric windows may still change as metric data arrives.

DynamoDB stores a shared execution lock, run/mode-specific duplicate records, and durable scaling intent. These reduce duplicate/conflicting operations but are not a cross-service exactly-once transaction. Lock expiry is explicitly checked. The 15-recheck limit applies to application-enqueued rechecks, not all service retries. Polling deferrals wait for the next tick and do not enqueue SQS rechecks.

## Defaults and deployment scope

Default mode is `event` with `DRY_RUN=true`. `CONTROLLER_MODE`, `DRY_RUN`, `EXPERIMENT_ID`, and `RUN_STARTED_AT` are read from Lambda environment variables. This is a sanitized public copy. Account ID `000000000000`, zero-filled ALB/target-group suffixes, and `example-alb.invalid` are placeholders, not deployable resource settings. Configure all resource names, region, ARNs, dimensions, URLs, and IAM policies for your own environment before use. Original deployment guides and raw records are retained locally and excluded from publication.

`configure.py` is an operational AWS tool, not an offline test. `install` changes Lambda code/configuration and role permissions, EventBridge rules/targets, and SQS settings; it leaves trigger sources disabled and defaults to dry-run. `select` stops sources before enabling the requested mode, and `--live` enables actual ASG capacity writes. `stop` disables triggers/queue mappings but does not cancel scaling already accepted by ASG or reset capacity. No such operations were performed during this Git update.

Use only with the existing provisioned deployment and the appropriate administrative identity. Back up deployed code/configuration before installation; the installer's saved configuration is not a backup of the old Lambda source. The implementation checks the configured account identity. It does not provision an ALB, AMI, ASG, or complete worker environment.

## Existing-deployment workflow

The following commands describe operation after the dependencies and named resources are configured; they have not been executed by this repository sync.

From an administrative AWS environment, in this package directory:

```sh
python configure.py status
python configure.py install
python configure.py select polling --run-id polling-dry-01
```

Verify one healthy application instance, settled cooldown, matching run/mode settings, and exclusive trigger state. Event runs additionally need alarms in OK before the new workload rise. For an explicitly authorized live run, use a new experiment ID:

```sh
python configure.py select polling --run-id polling-04 --live
# For a separate event run after the previous run has settled:
python configure.py select event --run-id event-04 --live
```

On the control EC2 host, from the project root:

```sh
python cloud/polling_event/run_workload.py --arm polling --run-id polling-04
# Or, for the separately configured event run:
python cloud/polling_event/run_workload.py --arm event --run-id event-04
```

The observer needs boto3 and the supplied observer policy; the existing calibration module also needs its HTTP dependencies. **`workload/workload_calibration.py` is required but absent from the supplied archives.** Export the actual deployed module before trying to reproduce a run; do not replace it with an older workload implementation. New experiment IDs prevent overwriting observations. The wrapper checks Lambda settings, trigger states, pending intent, cooldown, initial capacity, and target health before generating traffic.

After the group has returned to one healthy instance, stop and inspect the controller sources. Export logs using the exact UTC bounds from the run manifest:

```sh
python configure.py stop
python configure.py status
python export_logs.py --run-id polling-04 --start START_UTC --end END_UTC
```

Keep workload and extended readiness windows separately labelled. SDK summaries exclude observer requests, SQS service-side polling, and other service activity; they are not AWS billing totals. Hard timeouts can prevent final summary logging, so the exported records alone do not prove complete service-side accounting.

## Offline validation

```sh
python -m unittest discover -s cloud/polling_event -p test_controller.py -v
```

All 27 supplied tests passed during import, including dry-run protection, mode/source isolation, shared capacity decisions, duplicate handling, lock contention/expiry, deferred retries, cooldown, health checks, and recovery from uncertain writes or queue-send failures. They mock AWS and do not validate deployed permissions, service delivery, or provisioning performance.
