# Existing EC2 deployment runbook

These commands operate an already configured deployment; they are not a bootstrap procedure. Configure the worker instance IDs, private addresses, and AWS region in the controller and load balancer for the intended environment. The control host needs Flask, requests, boto3, and botocore plus an IAM role allowing the EC2 describe/start/stop operations used by the controller. Worker application and metrics services must already exist.

Use the merged repository paths below, not the snapshot's `src/` directory. Ensure `data/` exists, the cloud load balancer is running, Worker 1 is healthy, Workers 2 and 3 are stopped, and `data/draining.json` matches the intended backend state. Run only one scaling controller at a time.

```bash
cd ~/ScaleServer
source .venv-cloud/bin/activate
python loadbalancer/load_balancer.py
```

In another terminal, select one controller mode:

```bash
python controller/ec2_controller.py baseline --seconds 480
# Or:
python controller/ec2_controller.py event --seconds 480
```

Start the workload promptly in a separate terminal, with a unique label:

```bash
python workload/cloud_latency_test.py --label baseline_step_4
# Use event_step_4 for an event-mode run.
```

Wait for `controller_stopped` before switching modes or resetting worker state. The controller updates `data/draining.json` to disable or enable backends. Its `--observe-only` option suppresses scaling actions but still queries AWS and worker metrics. Do not import/run the EC2 controller merely to inspect it: module initialization creates an AWS client.

The latest `loadbalancer/load_balancer.py` targets EC2 workers. Historical local Docker experiments should use `loadbalancer/local_load_balancer.py` with the existing local controller scripts. The cloud request logger writes to `data/latency_<timestamp>/`; the formal trial folders in this snapshot were organized after collection.

No deployment or cloud-resource operation is performed by importing this snapshot into Git. The archive's original repository/tag suggestions were not executed.
