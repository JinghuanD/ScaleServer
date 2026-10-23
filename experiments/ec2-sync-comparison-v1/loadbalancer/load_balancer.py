import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, Response, request


app = Flask(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DRAIN_FILE = DATA_DIR / "draining.json"

DIAGNOSTIC_LOG = (
    DATA_DIR
    / f"lb_diagnostics_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl"
)

BACKENDS = [
    "http://172.31.31.51:5000",
    "http://172.31.16.38:5000",
    "http://172.31.16.59:5000",
]

healthy_backends = []
backend_lock = threading.Lock()
diagnostic_lock = threading.Lock()
next_index = 0


def backend_enabled(backend):
    try:
        disabled = json.loads(DRAIN_FILE.read_text())
        return backend not in disabled
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False


def check_backend(backend):
    if not backend_enabled(backend):
        return False

    try:
        response = requests.get(
            backend + "/health",
            timeout=(0.3, 1),
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def health_monitor():
    global healthy_backends

    with ThreadPoolExecutor(max_workers=3) as pool:
        while True:
            checks = list(pool.map(check_backend, BACKENDS))

            available = [
                backend
                for backend, healthy in zip(BACKENDS, checks)
                if healthy
            ]

            with backend_lock:
                changed = available != healthy_backends
                healthy_backends = available

            if changed:
                print(
                    f"Healthy backends: {available}",
                    flush=True,
                )

            time.sleep(1)


def write_diagnostic(record):
    try:
        with diagnostic_lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)

            with DIAGNOSTIC_LOG.open(
                "a",
                encoding="utf-8",
            ) as file:
                file.write(json.dumps(record) + "\n")

    except OSError as exc:
        app.logger.error(
            "Diagnostic log write failed: %s",
            exc,
        )


@app.route("/work")
def work():
    global next_index

    route_started = time.perf_counter()
    received_timestamp = time.time()

    request_id = (
        request.headers.get("X-Request-ID", "")[:128]
        or uuid.uuid4().hex
    )

    attempts = []

    def finish(result):
        elapsed_ms = (
            time.perf_counter() - route_started
        ) * 1000

        forward_ms = sum(
            attempt["elapsed_ms"]
            for attempt in attempts
        )

        result.headers["X-Request-ID"] = request_id
        result.headers["X-LB-Route-Ms"] = (
            f"{elapsed_ms:.3f}"
        )
        result.headers["X-LB-Forward-Ms"] = (
            f"{forward_ms:.3f}"
        )
        result.headers["X-LB-Attempts"] = str(
            len(attempts)
        )
        result.headers["X-LB-Backend"] = (
            attempts[-1]["backend"]
            if attempts else ""
        )

        write_diagnostic({
            "kind": "request_finished",
            "timestamp": time.time(),
            "received_timestamp": received_timestamp,
            "request_id": request_id,
            "status": result.status_code,
            "route_ms": elapsed_ms,
            "retry_count": max(
                0,
                len(attempts) - 1,
            ),
            "attempts": attempts,
        })

        return result

    with backend_lock:
        available = healthy_backends.copy()

        if available:
            start = next_index % len(available)
            next_index += 1

    if not available:
        return finish(
            app.make_response((
                {"error": "No healthy backend available"},
                503,
            ))
        )

    for offset in range(len(available)):
        backend = available[
            (start + offset) % len(available)
        ]

        if not backend_enabled(backend):
            continue

        attempt_started = time.perf_counter()

        attempt = {
            "backend": backend,
            "status": None,
            "error": "",
        }

        upstream = None

        try:
            upstream = requests.get(
                backend + "/work",
                timeout=(0.3, 10),
                headers={
                    "X-Request-ID": request_id,
                },
            )

            attempt["status"] = upstream.status_code

        except requests.RequestException as exc:
            attempt["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        attempt["elapsed_ms"] = (
            time.perf_counter() - attempt_started
        ) * 1000

        attempts.append(attempt)

        if (
            upstream is not None
            and upstream.status_code == 200
        ):
            return finish(
                Response(
                    upstream.content,
                    status=200,
                    content_type=upstream.headers.get(
                        "Content-Type",
                        "application/json",
                    ),
                )
            )

    return finish(
        app.make_response((
            {"error": "All selected backends failed"},
            503,
        ))
    )


@app.route("/health")
def health():
    with backend_lock:
        available = healthy_backends.copy()

    return {
        "status": "load balancer healthy",
        "backends": available,
    }


if __name__ == "__main__":
    print(
        f"Diagnostic log: {DIAGNOSTIC_LOG}",
        flush=True,
    )

    threading.Thread(
        target=health_monitor,
        daemon=True,
    ).start()

    app.run(
        host="0.0.0.0",
        port=8000,
        threaded=True,
        use_reloader=False,
    )
