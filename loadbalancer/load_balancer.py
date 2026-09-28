from flask import Flask, Response
import requests
import itertools

app = Flask(__name__)

BACKENDS = [
    "http://localhost:5000",
    "http://localhost:5001",
    "http://localhost:5002"
]

backend_cycle = itertools.cycle(BACKENDS)


@app.route("/work")
def work():

    # Try each backend at most once
    for _ in range(len(BACKENDS)):

        backend = next(backend_cycle)

        try:
            response = requests.get(
                backend + "/work",
                timeout=10
            )

            if response.status_code == 200:
                return Response(
                    response.content,
                    status=response.status_code,
                    content_type=response.headers.get(
                        "Content-Type",
                        "application/json"
                    )
                )

        except requests.RequestException:
            # Container may currently be stopped
            continue

    return {
        "error": "No backend available"
    }, 503


@app.route("/health")
def health():
    return {"status": "load balancer healthy"}


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000,
        threaded=True
    )