"""Apply mutable RabbitMQ backpressure policy without deleting durable queues.

Run on the coordinator with AMQP_URL and BROKER_MANAGEMENT_URL in the environment.
"""

import base64
import json
import os
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


def main():
    broker = urlparse(os.environ["AMQP_URL"])
    credentials = base64.b64encode(
        f"{unquote(broker.username or '')}:{unquote(broker.password or '')}".encode()
    ).decode()
    local = broker.hostname in {"127.0.0.1", "localhost"}
    default = (
        f"http://{broker.hostname}:15673"
        if local
        else f"https://{broker.hostname}:15671"
    )
    endpoint = os.environ.get("BROKER_MANAGEMENT_URL", default).rstrip("/")
    if not endpoint.startswith("https://") and broker.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Remote management requires HTTPS")
    length = int(os.environ.get("BROKER_MAX_LENGTH", "1000"))
    if length < 1:
        raise ValueError("BROKER_MAX_LENGTH must be positive")
    body = json.dumps(
        {
            "pattern": r"^work\.",
            "apply-to": "queues",
            "priority": 10,
            "definition": {"max-length": length, "overflow": "reject-publish"},
        }
    ).encode()
    request = Request(
        endpoint + "/api/policies/%2F/bounded-work",
        data=body,
        method="PUT",
        headers={
            "Authorization": "Basic " + credentials,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        assert response.status in {201, 204}
    print("Work-queue backpressure policy applied; existing messages preserved")


if __name__ == "__main__":
    main()
