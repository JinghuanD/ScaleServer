"""Shared AWS polling/event controller. Default: event + dry-run. Python 3.12."""
import json
import math
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = os.getenv("AWS_REGION", "us-east-2")
_dry_run_value = os.getenv("DRY_RUN", "true").strip().lower()
if _dry_run_value not in {"true", "false"}:
    raise ValueError("DRY_RUN must be true or false")
DRY_RUN = _dry_run_value == "true"
RUN_ID = os.getenv("EXPERIMENT_ID", "commissioning")
RUN_STARTED_AT = int(os.getenv("RUN_STARTED_AT", "0"))
ARM = os.getenv("CONTROLLER_MODE", "event").strip().lower()
if ARM not in {"event", "polling"}:
    raise ValueError("CONTROLLER_MODE must be event or polling")
POLLING_RULE_ARN = "arn:aws:events:us-east-2:000000000000:rule/scaleserver-polling-tick"
ASG_NAME = "scaleserver-asg"
LOAD_BALANCER = "app/scaleserver-alb/0000000000000000"
TARGET_GROUP_ARN = (
    "arn:aws:elasticloadbalancing:us-east-2:000000000000:"
    "targetgroup/scaleserver-tg/0000000000000000"
)
QUEUE_URL = "https://sqs.us-east-2.amazonaws.com/000000000000/scaleserver-recheck"
TABLE_NAME = "scaleserver-controller-state"
CAPACITY_PER_INSTANCE = 1.5
COOLDOWN_SECONDS = 300
RECHECK_SECONDS = 60
MAX_RECHECKS = 15
MAX_EVENT_AGE_SECONDS = 1800
MAX_WINDOW_AGE_SECONDS = 120
ALARM_NAMES = {"scaleserver-capacity-boundary-1", "scaleserver-capacity-boundary-2"}
MODE = "dryrun" if DRY_RUN else "live"
PREFIX = f"{RUN_ID}#{ARM}#{MODE}"
# The lock is shared even across experiment IDs and dry-run/live versions.
LOCK_KEY = f"lock#{ASG_NAME}"
# Live cooldown is shared across run IDs so changing a label cannot bypass it.
CONTROL_KEY = f"control#{ASG_NAME}#live"
CONFIG = Config(connect_timeout=2, read_timeout=4,
                retries={"mode": "standard", "total_max_attempts": 2})
cw = boto3.client("cloudwatch", region_name=REGION, config=CONFIG)
asg = boto3.client("autoscaling", region_name=REGION, config=CONFIG)
sqs = boto3.client("sqs", region_name=REGION, config=CONFIG)
elb = boto3.client("elbv2", region_name=REGION, config=CONFIG)
table = boto3.resource("dynamodb", region_name=REGION, config=CONFIG).Table(TABLE_NAME)


def log(kind, **fields):
    print(json.dumps({"kind": kind, "experiment_id": RUN_ID, "controller_mode": ARM, "mode": MODE,
                      "logged_at": datetime.now(timezone.utc).isoformat(),
                      **fields}, default=str))


class Calls:
    """Counts explicit SDK calls, including failures; not SQS poller requests."""
    def __init__(self):
        self.counts = Counter()
        self.retries = Counter()

    def call(self, name, fn, **kwargs):
        self.counts[name] += 1
        try:
            response = fn(**kwargs)
        except ClientError as exc:
            self.retries[name] += exc.response.get("ResponseMetadata", {}).get("RetryAttempts", 0)
            raise
        self.retries[name] += response.get("ResponseMetadata", {}).get("RetryAttempts", 0)
        return response


def read_state(calls, key):
    return calls.call("dynamodb.GetItem", table.get_item,
                      Key={"state_key": key}, ConsistentRead=True).get("Item", {})


def write_state(calls, key, **fields):
    return calls.call("dynamodb.PutItem", table.put_item,
                      Item={"state_key": key, **fields})


def is_condition_error(exc):
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


def acquire(calls, owner, context):
    lease = math.ceil(context.get_remaining_time_in_millis() / 1000) + 60
    try:
        calls.call("dynamodb.PutItem", table.put_item,
                   Item={"state_key": LOCK_KEY, "owner": owner,
                         "expires_at": int(time.time()) + lease},
                   ConditionExpression="attribute_not_exists(state_key) OR expires_at < :now",
                   ExpressionAttributeValues={":now": int(time.time())})
    except ClientError as exc:
        if is_condition_error(exc):
            log("controller_busy", owner=owner)
            raise RuntimeError("Controller busy; invocation must be retried") from exc
        raise
    log("lock_acquired", owner=owner)


def release(calls, owner):
    try:
        calls.call("dynamodb.DeleteItem", table.delete_item,
                   Key={"state_key": LOCK_KEY},
                   ConditionExpression="#o = :o",
                   ExpressionAttributeNames={"#o": "owner"},
                   ExpressionAttributeValues={":o": owner})
    except ClientError as exc:
        if not is_condition_error(exc):
            raise
        log("lock_release_skipped", reason="owner_changed")
        return
    log("lock_released", owner=owner)


def validate(event, attempt):
    detail = event.get("detail", {})
    supported_event = (event.get("source") == "aws.cloudwatch"
                       and detail.get("alarmName") in ALARM_NAMES
                       and detail.get("state", {}).get("value") in {"ALARM", "OK"})
    supported_poll = (event.get("source") == "aws.events"
                      and event.get("detail-type") == "Scheduled Event"
                      and POLLING_RULE_ARN in event.get("resources", []))
    if not ((ARM == "event" and supported_event) or (ARM == "polling" and supported_poll)):
        log("decision_skipped", reason="unsupported_event")
        return False
    if not isinstance(event.get("id"), str) or not event["id"]:
        raise ValueError("Missing event id")
    if type(attempt) is not int or not 0 <= attempt <= MAX_RECHECKS:
        raise ValueError("Invalid attempt")
    stamp = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Event time must include timezone")
    if stamp.timestamp() < RUN_STARTED_AT:
        log("decision_skipped", reason="event_from_previous_run", event_id=event["id"])
        return False
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    max_age = MAX_EVENT_AGE_SECONDS if ARM == "event" else 120
    if not 0 <= age <= max_age:
        log("decision_skipped", reason="expired_event", event_id=event["id"],
            event_age_seconds=age)
        return False
    return True


def defer(calls, event, attempt, reason):
    log("decision_deferred", event_id=event["id"], attempt=attempt, reason=reason)
    if ARM == "polling":
        # The next scheduled tick re-evaluates; no additional queue polling arm.
        log("waiting_for_next_poll", event_id=event["id"], reason=reason)
        return "poll_wait"
    if attempt >= MAX_RECHECKS:
        log("recheck_exhausted", event_id=event["id"], reason=reason)
        return "exhausted"
    # Persist completion only AFTER successful enqueue. An uncertain enqueue may
    # be repeated; downstream attempt deduplication handles repeated messages.
    response = calls.call("sqs.SendMessage", sqs.send_message,
                          QueueUrl=QUEUE_URL, DelaySeconds=RECHECK_SECONDS,
                          MessageBody=json.dumps({"event": event, "attempt": attempt + 1,
                                                  "experiment_id": RUN_ID, "mode": MODE,
                                                  "controller_mode": ARM}))
    log("recheck_scheduled", event_id=event["id"], next_attempt=attempt + 1,
        delay_seconds=RECHECK_SECONDS, message_id=response["MessageId"])
    return "deferred"


def evaluate(calls, event, attempt, context):
    now = datetime.now(timezone.utc)
    log("evaluation_started", event_id=event["id"], attempt=attempt,
        original_state=event.get("detail", {}).get("state", {}).get("value", "POLL"),
        alarm_name=event.get("detail", {}).get("alarmName"), received_timestamp=now,
        trigger_timestamp=event["time"])
    end = now.replace(second=0, microsecond=0)
    response = calls.call("cloudwatch.GetMetricStatistics", cw.get_metric_statistics,
                          Namespace="AWS/ApplicationELB", MetricName="RequestCount",
                          Dimensions=[{"Name": "LoadBalancer", "Value": LOAD_BALANCER}],
                          StartTime=end - timedelta(minutes=6), EndTime=end,
                          Period=60, Statistics=["Sum"])
    points = sorted(response["Datapoints"], key=lambda p: p["Timestamp"])
    log("metric_windows", event_id=event["id"], attempt=attempt,
        windows=[{"timestamp": p["Timestamp"], "request_count": p["Sum"],
                  "request_rate": p["Sum"] / 60} for p in points])
    if len(points) < 2:
        return defer(calls, event, attempt, "insufficient_data")
    previous, latest = points[-2:]
    gap = (latest["Timestamp"] - previous["Timestamp"]).total_seconds()
    age = (now - latest["Timestamp"] - timedelta(seconds=60)).total_seconds()
    if gap != 60 or not 0 <= age <= MAX_WINDOW_AGE_SECONDS:
        return defer(calls, event, attempt, "missing_or_stale_windows")
    groups = calls.call("autoscaling.DescribeAutoScalingGroups", asg.describe_auto_scaling_groups,
                        AutoScalingGroupNames=[ASG_NAME])["AutoScalingGroups"]
    if len(groups) != 1:
        raise RuntimeError(f"ASG not found: {ASG_NAME}")
    group = groups[0]
    capacities = [max(group["MinSize"], min(group["MaxSize"],
                   math.ceil(p["Sum"] / 60 / CAPACITY_PER_INSTANCE)))
                  for p in (previous, latest)]
    if capacities[0] != capacities[1]:
        log("capacity_unconfirmed", event_id=event["id"], window_capacities=capacities)
        return defer(calls, event, attempt, "capacity_not_confirmed")
    desired, target = group["DesiredCapacity"], capacities[-1]
    log("capacity_plan", event_id=event["id"], attempt=attempt, dry_run=DRY_RUN,
        current_desired=desired, proposed_desired=target,
        request_rates=[p["Sum"] / 60 for p in (previous, latest)],
        window_timestamps=[p["Timestamp"] for p in (previous, latest)])

    # A durable write-ahead intent protects the crash/API-timeout window.
    control = read_state(calls, CONTROL_KEY)
    pending = control.get("pending_target")
    if pending is not None:
        if desired != int(pending):
            # Do not blindly replay the old target: wait, then recalculate.
            if time.time() - int(control["intent_at"]) < COOLDOWN_SECONDS:
                return defer(calls, event, attempt, "previous_write_unconfirmed")
            if not DRY_RUN:
                control.pop("pending_target", None)
                control.pop("state_key", None)
                write_state(calls, CONTROL_KEY, **control)
        elif not DRY_RUN:
            control.pop("pending_target", None)
            control.pop("state_key", None)
            write_state(calls, CONTROL_KEY, **control)

    instances = group.get("Instances", [])
    active = [i for i in instances if i["LifecycleState"] == "InService"
              and i.get("HealthStatus") == "Healthy"]
    if len(instances) != desired or len(active) != desired:
        return defer(calls, event, attempt, "asg_transition_in_progress")
    health = calls.call("elasticloadbalancing.DescribeTargetHealth", elb.describe_target_health,
                        TargetGroupArn=TARGET_GROUP_ARN)["TargetHealthDescriptions"]
    healthy_ids = {h["Target"]["Id"] for h in health
                   if h["TargetHealth"]["State"] == "healthy"}
    if not {i["InstanceId"] for i in active}.issubset(healthy_ids):
        return defer(calls, event, attempt, "targets_not_ready")
    if desired == target:
        log("decision_noop", event_id=event["id"], desired=desired)
        return "noop"
    elapsed = time.time() - int(control.get("last_change_at", 0))
    if elapsed < COOLDOWN_SECONDS:
        return defer(calls, event, attempt, "cooldown_active")
    if DRY_RUN:
        log("scaling_would_execute", event_id=event["id"], current_desired=desired,
            proposed_desired=target)
        return "dry_run_plan"
    if context.get_remaining_time_in_millis() < 15000:
        raise RuntimeError("Insufficient remaining time for scaling write")
    intent_at = int(time.time())
    write_state(calls, CONTROL_KEY, pending_target=target, intent_at=intent_at,
                last_change_at=intent_at, event_id=event["id"], experiment_id=RUN_ID)
    calls.call("autoscaling.SetDesiredCapacity", asg.set_desired_capacity,
               AutoScalingGroupName=ASG_NAME, DesiredCapacity=target, HonorCooldown=False)
    log("scaling_requested", event_id=event["id"], previous_desired=desired,
        requested_desired=target, requested_timestamp=datetime.now(timezone.utc))
    # Keep monitoring readiness and re-evaluating current demand after the write.
    return defer(calls, event, attempt, "scaling_requested_wait_for_readiness")


def process(event, attempt, context):
    if not validate(event, attempt):
        return
    calls, owner = Calls(), str(uuid.uuid4())
    started = time.perf_counter()
    held = False
    event_key = f"event#{PREFIX}#{event['id']}"
    attempt_key = f"attempt#{PREFIX}#{event['id']}#{attempt}"
    try:
        acquire(calls, owner, context)
        held = True
        if read_state(calls, event_key) or read_state(calls, attempt_key):
            log("duplicate_skipped", event_id=event["id"], attempt=attempt)
            return
        outcome = evaluate(calls, event, attempt, context)
        write_state(calls, attempt_key, outcome=outcome, completed_at=int(time.time()))
        if outcome != "deferred":
            write_state(calls, event_key, outcome=outcome, completed_at=int(time.time()))
        log("attempt_completed", event_id=event["id"], attempt=attempt, outcome=outcome)
    except Exception as exc:
        log("processing_failed", event_id=event["id"], attempt=attempt,
            error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        try:
            if held:
                release(calls, owner)
        finally:
            log("api_call_summary", event_id=event["id"], attempt=attempt,
                logical_sdk_calls=dict(calls.counts),
                reported_sdk_retries=dict(calls.retries),
                duration_seconds=time.perf_counter() - started)


def diagnostic(name, context):
    if not DRY_RUN:
        raise ValueError("Diagnostics require DRY_RUN=true")
    if name == "state_store":
        calls = Calls()
        key = f"diagnostic#{uuid.uuid4()}"
        write_state(calls, key, result="ok")
        if read_state(calls, key).get("result") != "ok":
            raise RuntimeError("State readback failed")
        calls.call("dynamodb.DeleteItem", table.delete_item, Key={"state_key": key})
        log("state_store_test_passed")
    elif name == "lock_check":
        calls, owner = Calls(), str(uuid.uuid4())
        acquire(calls, owner, context)
        try:
            try:
                acquire(calls, "contender-" + owner, context)
            except RuntimeError:
                log("lock_contention_test_passed")
            else:
                raise AssertionError("Second owner unexpectedly acquired lock")
        finally:
            release(calls, owner)
    elif name == "dedup_check":
        event = {"id": f"manual-dedup-{uuid.uuid4()}", "source": "aws.cloudwatch",
                 "time": datetime.now(timezone.utc).isoformat(),
                 "detail": {"alarmName": "scaleserver-capacity-boundary-1",
                            "state": {"value": "ALARM"}}}
        if ARM == "polling":
            event.update(source="aws.events", resources=[POLLING_RULE_ARN],
                         detail={}, **{"detail-type": "Scheduled Event"})
        process(event, 0, context)
        process(event, 0, context)
    else:
        raise ValueError("Unknown diagnostic")
    return {"status": "diagnostic_completed", "diagnostic": name, "dry_run": True}


def lambda_handler(event, context):
    log("invocation_started", aws_request_id=context.aws_request_id,
        source="sqs" if "Records" in event else event.get("source", "manual"))
    if "diagnostic" in event:
        return diagnostic(event["diagnostic"], context)
    if "Records" in event:
        if ARM != "event":
            log("decision_skipped", reason="sqs_disabled_in_polling_mode")
            return {"status": "ignored", "controller_mode": ARM}
        for record in event["Records"]:
            if record.get("eventSource") != "aws:sqs":
                raise ValueError("Unsupported record source")
            body = json.loads(record["body"])
            # Discard commissioning/old-run messages after switching modes.
            if (body.get("experiment_id") != RUN_ID or body.get("mode") != MODE
                    or body.get("controller_mode") != ARM):
                log("decision_skipped", reason="old_or_untagged_recheck",
                    message_id=record.get("messageId"))
                continue
            process(body["event"], body["attempt"], context)
    else:
        process(event, 0, context)
    return {"status": "evaluated", "dry_run": DRY_RUN, "controller_mode": ARM}
