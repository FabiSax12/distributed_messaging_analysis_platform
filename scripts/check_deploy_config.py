"""Check the actual remote-node env generator with synthetic credentials only."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    text = (Path(__file__).parent / "deploy/remote-node.sh").read_text()
    marker = "python3 - >app.env.new <<'PY'\n"
    code = text.split(marker, 1)[1].split("\nPY\n", 1)[0]
    config = {
        "coordinator": {
            "COGNITO_ISSUER_URL": "https://cognito-idp.us-east-1.amazonaws.com/test",
            "COGNITO_CLIENT_ID": "client",
            "COGNITO_USER_POOL_ID": "pool",
            "ALERTS_TOPIC_ARN": "arn:synthetic",
            "M1_MEMORY_BUDGET_BYTES": 1000,
            "M1_TASK_BASE_BYTES": 100,
            "M1_OPERATION_BASE_BYTES": {"text_analyze": 100},
            "COORDINATOR_TLS": True,
        }
    }
    env = {
        **os.environ,
        "RDS_SECRET_JSON": json.dumps({"username": "synthetic", "password": "p#x$z"}),
        "MQ_SECRET_JSON": json.dumps({"username": "synthetic", "password": "p#x$z"}),
        "MQ_ENDPOINT": "amqps://example.invalid:5671",
        "RDS_ENDPOINT": "example.invalid:5432",
        "RDS_DB_NAME": "test",
        "S3_BUCKET": "synthetic",
        "AWS_REGION": "us-east-1",
        "NODE_ROLE": "coordinator",
        "POOL": "",
        "ORIGIN_VERIFY_SECRET_VALUE": "synthetic-only",
        "RUNTIME_CONFIG_JSON": json.dumps(config),
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "COGNITO_CLIENT_ID='client'" in result.stdout
    assert "M1_OPERATION_BASE_BYTES='" in result.stdout
    assert "p%23x%24z" in result.stdout
    config["coordinator"]["UNRECOGNIZED_SETTING"] = "x"
    env["RUNTIME_CONFIG_JSON"] = json.dumps(config)
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "Unknown runtime setting" in result.stderr
    env["RUNTIME_CONFIG_JSON"] = "{}"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "coordinator missing" in result.stderr
    print(
        "Remote deployment settings, credential quoting and fail-closed config checks passed"
    )


if __name__ == "__main__":
    main()
