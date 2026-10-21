from flask import Flask, Response
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor


import json
from pathlib import Path

DRAIN_FILE = Path(__file__).resolve().parent.parent / "data" / "draining.json"

def backend_enabled(backend):
    try:
        disabled = json.loads(DRAIN_FILE.read_text())
        return backend not in disabled
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False

app = Flask(__name__)

BACKENDS = [
    "http://172.31.31.51:5000",
    "http://172.31.16.38:5000",
    "http://172.31.16.59:5000",
]

healthy_backends = []
backend_lock = threading.Lock()
next_index = 0


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
                print(f"Healthy backends: {available}", flush=True)

            time.sleep(1)


@app.route("/work")
def work():
    global next_index

    with backend_lock:
        available = healthy_backends.copy()

        if not available:
            return {"error": "No healthy backend available"}, 503

        start = next_index % len(available)
        next_index += 1

    for offset in range(len(available)):
        backend = available[(start + offset) % len(available)]

        if not backend_enabled(backend):
            continue

        try:
            response = requests.get(
                backend + "/work",
                timeout=(0.3, 10),
            )

            if response.status_code == 200:
                return Response(
                    response.content,
                    status=200,
                    content_type=response.headers.get(
                        "Content-Type", "application/json"
                    ),
                )

        except requests.RequestException:
            continue

    return {"error": "All selected backends failed"}, 503


@app.route("/health")
def health():
    with backend_lock:
        available = healthy_backends.copy()

    return {
        "status": "load balancer healthy",
        "backends": available,
    }


if __name__ == "__main__":
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