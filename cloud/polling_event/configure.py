"""Run in AWS CloudShell (us-east-2). Changes only the named experiment resources."""
import argparse
import io
import json
from pathlib import Path
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-2"
ACCOUNT = "000000000000"
FUNCTION = "scaleserver-event-controller"
EVENT_RULE = "scaleserver-alarm-events"
POLL_RULE = "scaleserver-polling-tick"
QUEUE_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:scaleserver-recheck"
QUEUE_URL = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/scaleserver-recheck"
ROOT = Path(__file__).resolve().parent


def missing(exc):
    return exc.response["Error"]["Code"] in {"ResourceNotFoundException", "ResourceNotFound"}


class Setup:
    def __init__(self):
        session = boto3.Session(region_name=REGION)
        actual = session.client("sts").get_caller_identity()["Account"]
        if actual != ACCOUNT:
            raise RuntimeError(f"Wrong AWS account: {actual}; expected {ACCOUNT}")
        self.lam = session.client("lambda")
        self.events = session.client("events")
        self.sqs = session.client("sqs")
        self.iam = session.client("iam")
        self.asg = session.client("autoscaling")
        self.elb = session.client("elbv2")

    def config(self):
        return self.lam.get_function_configuration(FunctionName=FUNCTION)

    def mappings(self):
        pager = self.lam.get_paginator("list_event_source_mappings")
        return [m for page in pager.paginate(FunctionName=FUNCTION)
                for m in page["EventSourceMappings"] if m.get("EventSourceArn") == QUEUE_ARN]

    def wait_update(self):
        self.lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)

    def set_mapping(self, mapping, enabled):
        deadline = time.monotonic() + 180
        while True:
            state = self.lam.get_event_source_mapping(UUID=mapping["UUID"])["State"]
            if state in {"Enabled", "Disabled"}:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("SQS mapping did not settle")
            time.sleep(2)
        target = "Enabled" if enabled else "Disabled"
        if state != target:
            self.lam.update_event_source_mapping(UUID=mapping["UUID"], Enabled=enabled)
        while self.lam.get_event_source_mapping(UUID=mapping["UUID"])["State"] != target:
            if time.monotonic() > deadline:
                raise TimeoutError("SQS mapping change timed out")
            time.sleep(2)

    def stop(self):
        for name in (EVENT_RULE, POLL_RULE):
            try:
                self.events.disable_rule(Name=name)
            except ClientError as exc:
                if not missing(exc): raise
        for m in self.mappings(): self.set_mapping(m, False)
        print("Both rules and recheck consumption disabled. Waiting 65s for existing calls...", flush=True)
        # EventBridge disabling can propagate asynchronously. Mode gates and
        # experiment IDs additionally reject late messages after the change.
        for left in (65, 50, 35, 20, 5):
            print(f"  remaining: {left}s", flush=True)
            time.sleep(min(15, left))

    def install(self):
        self.events.describe_rule(Name=EVENT_RULE)  # fail before modifying if missing
        original = self.config()
        backup = ROOT / f"lambda_configuration_backup_{int(time.time())}.json"
        # Does not contain access keys; the environment may contain user settings.
        backup.write_text(json.dumps(original, default=str, indent=2))
        self.stop()
        self.sqs.set_queue_attributes(QueueUrl=QUEUE_URL, Attributes={"VisibilityTimeout": "360"})
        role_name = original["Role"].rsplit("/", 1)[-1]
        self.iam.put_role_policy(RoleName=role_name, PolicyName="ScaleServerComparisonAccess",
                                 PolicyDocument=(ROOT / "controller_permissions.json").read_text())
        env = original.get("Environment", {}).get("Variables", {}).copy()
        env.update(DRY_RUN="true", CONTROLLER_MODE="event", EXPERIMENT_ID="comparison-setup",
                   RUN_STARTED_AT=str(int(time.time())))
        self.lam.update_function_configuration(FunctionName=FUNCTION, Timeout=60,
                                                Handler="lambda_function.lambda_handler",
                                                Environment={"Variables": env})
        self.wait_update()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(ROOT / "lambda_function.py", "lambda_function.py")
        self.lam.update_function_code(FunctionName=FUNCTION, ZipFile=buf.getvalue())
        self.wait_update()
        arn = self.config()["FunctionArn"]
        rule_arn = self.events.put_rule(Name=POLL_RULE, ScheduleExpression="rate(1 minute)",
                                        State="DISABLED",
                                        Description="ScaleServer polling baseline; one metric evaluation per minute")["RuleArn"]
        try:
            self.lam.add_permission(FunctionName=FUNCTION, StatementId="scaleserver-polling-tick",
                                    Action="lambda:InvokeFunction", Principal="events.amazonaws.com",
                                    SourceArn=rule_arn, SourceAccount=ACCOUNT)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceConflictException": raise
            policy = json.loads(self.lam.get_policy(FunctionName=FUNCTION)["Policy"])
            statement = next(s for s in policy["Statement"] if s["Sid"] == "scaleserver-polling-tick")
            if (statement.get("Principal", {}).get("Service") != "events.amazonaws.com"
                    or statement.get("Condition", {}).get("ArnLike", {}).get("AWS:SourceArn") != rule_arn):
                raise RuntimeError("Existing Lambda permission differs; inspect it manually")
        response = self.events.put_targets(Rule=POLL_RULE, Targets=[{
            "Id": "polling-controller", "Arn": arn,
            "RetryPolicy": {"MaximumEventAgeInSeconds": 120, "MaximumRetryAttempts": 2}}])
        if response.get("FailedEntryCount"):
            raise RuntimeError(json.dumps(response))
        if not self.mappings():
            self.lam.create_event_source_mapping(FunctionName=FUNCTION, EventSourceArn=QUEUE_ARN,
                                                 Enabled=False, BatchSize=1,
                                                 MaximumBatchingWindowInSeconds=0)
        else:
            for m in self.mappings():
                self.lam.update_event_source_mapping(UUID=m["UUID"], BatchSize=1,
                                                     MaximumBatchingWindowInSeconds=0)
        # Check existing alarm rule really targets this unqualified Lambda.
        targets = self.events.list_targets_by_rule(Rule=EVENT_RULE)["Targets"]
        if not any(t["Arn"] == arn and not any(k in t for k in ("Input", "InputPath", "InputTransformer")) for t in targets):
            raise RuntimeError("Alarm rule must target this Lambda with the original event; fix target in console")
        print("Installed in dry-run. BOTH sources remain stopped. Select an arm next.")

    def select(self, arm, run_id, live):
        self.events.describe_rule(Name=POLL_RULE)
        self.stop()
        cfg = self.config()
        env = cfg.get("Environment", {}).get("Variables", {}).copy()
        env.update(CONTROLLER_MODE=arm, DRY_RUN="false" if live else "true", EXPERIMENT_ID=run_id,
                   RUN_STARTED_AT=str(int(time.time())))
        self.lam.update_function_configuration(FunctionName=FUNCTION, Environment={"Variables": env})
        self.wait_update()
        if arm == "event":
            for m in self.mappings(): self.set_mapping(m, True)
            self.events.enable_rule(Name=EVENT_RULE)
        else:
            self.events.enable_rule(Name=POLL_RULE)
        print(f"Selected {arm}; dry_run={not live}; experiment_id={run_id}")
        print("Verify ASG=1/Healthy and cooldown settled, then run workload in controller EC2 SSH.")

    def status(self):
        cfg = self.config()
        print(json.dumps({"function": FUNCTION, "timeout": cfg["Timeout"],
                          "settings": {k: cfg.get("Environment", {}).get("Variables", {}).get(k)
                                       for k in ("CONTROLLER_MODE", "DRY_RUN", "EXPERIMENT_ID")}}, indent=2))
        for name in (EVENT_RULE, POLL_RULE):
            try:
                r = self.events.describe_rule(Name=name)
                print(name, r["State"], r.get("ScheduleExpression", "alarm pattern"))
            except ClientError as exc:
                if not missing(exc): raise
                print(name, "NOT CREATED")
        for m in self.mappings(): print("SQS", m["UUID"], m["State"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "stop", "status"): sub.add_parser(name)
    select = sub.add_parser("select")
    select.add_argument("arm", choices=["event", "polling"])
    select.add_argument("--run-id", required=True)
    select.add_argument("--live", action="store_true", help="Enable real ASG writes; default dry-run")
    args = parser.parse_args()
    setup = Setup()
    if args.command == "select": setup.select(args.arm, args.run_id, args.live)
    else: getattr(setup, args.command)()


if __name__ == "__main__": main()
