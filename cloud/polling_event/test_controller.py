"""Offline behavioral tests; no AWS credentials/network/boto3 installation needed."""
import copy
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone


class ClientError(Exception):
    def __init__(self, response, operation="test"):
        self.response = response
        super().__init__(response["Error"]["Code"])


class Table:
    def __init__(self):
        self.data = {}
    def get_item(self, Key, **kw):
        item = self.data.get(Key["state_key"])
        return {"Item": copy.deepcopy(item)} if item else {}
    def put_item(self, Item, **kw):
        old = self.data.get(Item["state_key"])
        if kw.get("ConditionExpression") and old and old["expires_at"] >= kw["ExpressionAttributeValues"][":now"]:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}})
        self.data[Item["state_key"]] = copy.deepcopy(Item)
        return {}
    def delete_item(self, Key, **kw):
        old = self.data.get(Key["state_key"])
        if kw.get("ConditionExpression") and (not old or old["owner"] != kw["ExpressionAttributeValues"][":o"]):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}})
        self.data.pop(Key["state_key"], None)
        return {}


class AWS:
    def __init__(self):
        self.table = Table()
        self.query_count = 0
        self.writes = []
        self.messages = []
        self.rate = 1.8
        self.mixed = False
        self.stale = False
        self.fail_send = False
        self.healthy = True
        self.group = {"DesiredCapacity": 1, "MinSize": 1, "MaxSize": 3,
                      "Instances": [{"InstanceId": "i-one", "LifecycleState": "InService", "HealthStatus": "Healthy"}]}
    def get_metric_statistics(self, **kw):
        self.query_count += 1
        end = kw["EndTime"] - (timedelta(minutes=10) if self.stale else timedelta())
        return {"Datapoints": [{"Timestamp": end - timedelta(minutes=2), "Sum": self.rate * 60},
                               {"Timestamp": end - timedelta(minutes=1), "Sum": (0.5 if self.mixed else self.rate) * 60}]}
    def describe_auto_scaling_groups(self, **kw):
        return {"AutoScalingGroups": [copy.deepcopy(self.group)]}
    def describe_target_health(self, **kw):
        return {"TargetHealthDescriptions": [{"Target": {"Id": "i-one"}, "TargetHealth": {"State": "healthy" if self.healthy else "initial"}}]}
    def set_desired_capacity(self, **kw):
        self.writes.append(kw)
        self.group["DesiredCapacity"] = kw["DesiredCapacity"]
        return {}
    def send_message(self, **kw):
        if self.fail_send:
            raise RuntimeError("simulated send failure")
        self.messages.append(kw)
        return {"MessageId": f"message-{len(self.messages)}"}


class Context:
    aws_request_id = "offline-test"
    def get_remaining_time_in_millis(self):
        return 60000


class Tests(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()
        boto = types.ModuleType("boto3")
        boto.client = lambda *a, **k: self.aws
        boto.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda name: self.aws.table)
        cfg = types.ModuleType("botocore.config")
        cfg.Config = lambda **kw: kw
        exc = types.ModuleType("botocore.exceptions")
        exc.ClientError = ClientError
        sys.modules.update({"boto3": boto, "botocore": types.ModuleType("botocore"),
                            "botocore.config": cfg, "botocore.exceptions": exc})
        os.environ["DRY_RUN"] = "true"
        os.environ["CONTROLLER_MODE"] = "event"
        os.environ["RUN_STARTED_AT"] = "0"
        spec = importlib.util.spec_from_file_location("controller_under_test", Path(__file__).with_name("lambda_function.py"))
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.event = {"id": "test-event", "source": "aws.cloudwatch",
                      "time": datetime.now(timezone.utc).isoformat(),
                      "detail": {"alarmName": "scaleserver-capacity-boundary-1", "state": {"value": "ALARM"}}}
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
    def tearDown(self):
        self.capture.__exit__(None, None, None)
    def run_event(self, attempt=0):
        self.m.process(self.event, attempt, Context())
    def live(self):
        self.m.DRY_RUN = False
        self.m.MODE = "live"
        self.m.PREFIX = "test#live"
    def test_dryrun_never_writes_asg_or_cooldown(self):
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertNotIn(self.m.CONTROL_KEY, self.aws.table.data)
        self.assertIn('"proposed_desired": 2', self.output.getvalue())
    def test_duplicate_only_queries_once(self):
        self.run_event(); self.run_event()
        self.assertEqual(self.aws.query_count, 1)
        self.assertIn("duplicate_skipped", self.output.getvalue())
    def test_deferred_retry_is_not_a_duplicate(self):
        self.aws.mixed = True
        self.run_event()
        self.aws.mixed = False
        self.run_event(1)
        self.assertEqual(self.aws.query_count, 2)
        self.assertIn("dry_run_plan", self.output.getvalue())
    def test_send_failure_keeps_attempt_retryable(self):
        self.aws.mixed = True
        self.aws.fail_send = True
        with self.assertRaises(RuntimeError): self.run_event()
        self.aws.fail_send = False
        self.run_event()
        self.assertEqual(len(self.aws.messages), 1)
        self.assertNotIn(self.m.LOCK_KEY, self.aws.table.data)
    def test_lock_contention_blocks_processing(self):
        calls = self.m.Calls()
        self.m.acquire(calls, "first", Context())
        with self.assertRaises(RuntimeError): self.run_event()
        self.assertEqual(self.aws.query_count, 0)
        self.m.release(calls, "first")
    def test_expired_lock_can_be_reclaimed(self):
        self.aws.table.data[self.m.LOCK_KEY] = {"owner": "dead", "expires_at": 0}
        self.run_event()
        self.assertEqual(self.aws.query_count, 1)
    def test_cooldown_defers_live_write(self):
        self.live()
        self.aws.table.data[self.m.CONTROL_KEY] = {"last_change_at": int(self.m.time.time())}
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertIn("cooldown_active", self.output.getvalue())
    def test_unhealthy_target_defers(self):
        self.live(); self.aws.healthy = False
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertIn("targets_not_ready", self.output.getvalue())
    def test_pending_asg_instance_defers(self):
        self.live()
        self.aws.group["Instances"][0]["LifecycleState"] = "Pending"
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertIn("asg_transition_in_progress", self.output.getvalue())
    def test_live_write_intent_and_no_duplicate_write(self):
        self.live()
        self.run_event()
        self.assertEqual(len(self.aws.writes), 1)
        self.assertEqual(self.aws.table.data[self.m.CONTROL_KEY]["pending_target"], 2)
        self.run_event(1)
        self.assertEqual(len(self.aws.writes), 1)
        self.assertIn("asg_transition_in_progress", self.output.getvalue())
    def test_post_write_send_failure_does_not_repeat_asg_write(self):
        self.live(); self.aws.fail_send = True
        with self.assertRaises(RuntimeError): self.run_event()
        self.aws.fail_send = False
        self.run_event()
        self.assertEqual(len(self.aws.writes), 1)
    def test_unconfirmed_intent_defers(self):
        self.live()
        self.aws.table.data[self.m.CONTROL_KEY] = {
            "pending_target": 2, "intent_at": int(self.m.time.time()),
            "last_change_at": int(self.m.time.time())}
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertIn("previous_write_unconfirmed", self.output.getvalue())
    def test_old_intent_uses_new_demand(self):
        self.live(); self.aws.rate = 0.5
        self.aws.table.data[self.m.CONTROL_KEY] = {"pending_target": 2, "intent_at": 0, "last_change_at": 0}
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertIn("decision_noop", self.output.getvalue())
    def test_stale_metric_defers(self):
        self.aws.stale = True
        self.run_event()
        self.assertIn("missing_or_stale_windows", self.output.getvalue())
    def test_recheck_limit_stops_enqueuing(self):
        self.aws.mixed = True
        self.run_event(self.m.MAX_RECHECKS)
        self.assertEqual(self.aws.messages, [])
        self.assertIn("recheck_exhausted", self.output.getvalue())
    def test_expired_event_skips_all_queries(self):
        self.event["time"] = "2000-01-01T00:00:00Z"
        self.run_event()
        self.assertEqual(self.aws.query_count, 0)
    def test_legacy_queue_message_is_skipped(self):
        self.m.lambda_handler({"Records": [{"eventSource": "aws:sqs", "body": '{"event":{},"attempt":1}'}]}, Context())
        self.assertEqual(self.aws.query_count, 0)
        self.assertIn("old_or_untagged_recheck", self.output.getvalue())
    def test_api_summary_includes_state_and_metric_calls(self):
        self.run_event()
        text = self.output.getvalue()
        self.assertIn("api_call_summary", text)
        self.assertIn("dynamodb.PutItem", text)
        self.assertIn("cloudwatch.GetMetricStatistics", text)

    def polling(self):
        self.m.ARM = "polling"
        self.m.PREFIX = "test#polling#dryrun"
        self.event = {"id": "poll-tick-1", "source": "aws.events",
                      "detail-type": "Scheduled Event", "resources": [self.m.POLLING_RULE_ARN],
                      "time": datetime.now(timezone.utc).isoformat(), "detail": {}}

    def test_polling_uses_same_capacity_plan(self):
        self.polling(); self.run_event()
        self.assertIn('"proposed_desired": 2', self.output.getvalue())
        self.assertEqual(len(self.aws.messages), 0)
    def test_polling_deferral_never_enqueues_sqs(self):
        self.polling(); self.aws.mixed = True
        self.run_event()
        self.assertIn("waiting_for_next_poll", self.output.getvalue())
        self.assertEqual(self.aws.messages, [])
    def test_polling_evaluates_next_tick_after_noop(self):
        self.polling(); self.aws.rate = 0.5
        self.run_event()
        self.event["id"] = "poll-tick-2"
        self.aws.rate = 1.8
        self.run_event()
        self.assertEqual(self.aws.query_count, 2)
        self.assertIn('"proposed_desired": 2', self.output.getvalue())
    def test_polling_rejects_alarm_source(self):
        self.m.ARM = "polling"
        self.run_event()
        self.assertEqual(self.aws.query_count, 0)
    def test_event_rejects_poll_tick(self):
        self.polling(); self.m.ARM = "event"
        self.run_event()
        self.assertEqual(self.aws.query_count, 0)
    def test_polling_live_writes_same_asg_api(self):
        self.polling(); self.live(); self.m.ARM = "polling"
        self.run_event()
        self.assertEqual(len(self.aws.writes), 1)
        self.assertEqual(self.aws.writes[0]["DesiredCapacity"], 2)
        self.assertEqual(self.aws.messages, [])
    def test_polling_obeys_shared_cooldown(self):
        self.polling(); self.live(); self.m.ARM = "polling"
        self.aws.table.data[self.m.CONTROL_KEY] = {"last_change_at": int(self.m.time.time())}
        self.run_event()
        self.assertEqual(self.aws.writes, [])
        self.assertEqual(self.aws.messages, [])
    def test_polling_late_tick_is_ignored(self):
        self.polling()
        self.event["time"] = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
        self.run_event()
        self.assertEqual(self.aws.query_count, 0)
    def test_previous_run_alarm_is_ignored(self):
        self.m.RUN_STARTED_AT = int(self.m.time.time()) + 1
        self.run_event()
        self.assertEqual(self.aws.query_count, 0)
        self.assertIn("event_from_previous_run", self.output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
