"""Deploy the exact committed source with bounded, retryable package upload.

Credentials are supplied by `tcb login` in CI. Runtime secrets remain in the
service configuration and are deliberately absent from deployment arguments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import requests


def api(action, body, *, service="tcbr", version="2022-02-17"):
    cli = [shutil.which("tcb")] if shutil.which("tcb") else [
        "npx.cmd" if os.name == "nt" else "npx", "--yes",
        "--package=@cloudbase/cli@3.8.7", "tcb",
    ]
    result = subprocess.run([
        *cli, "api", service, action, "--api-version", version,
        "--body", json.dumps(body), "--json", "-e", body["EnvId"],
        "-r", os.environ.get("CLOUDBASE_REGION", "ap-shanghai"),
    ], capture_output=True, text=True, encoding="utf-8", timeout=90, check=False)
    output = result.stdout
    try:
        response = json.loads(output[output.index("{"):])
    except ValueError as exc:
        raise RuntimeError(f"{action}: CLI returned invalid JSON (exit {result.returncode})") from exc
    if result.returncode or response.get("error"):
        error = response.get("error", {})
        raise RuntimeError(f"{action}: {error.get('code', 'CLI_ERROR')}: {error.get('message', '')}")
    return response["data"]


def upload_source(env_id, server):
    source = subprocess.run(["git", "archive", "--format=zip", "HEAD"],
                            capture_output=True, check=True).stdout
    info = api("DescribeCloudBaseBuildService", {"EnvId": env_id, "ServiceName": server},
               service="tcb", version="2018-06-08")
    headers = {item["Key"]: item["Value"] for item in info.get("UploadHeaders", [])}
    print(f"Uploading committed source: {len(source)} bytes", flush=True)
    for attempt in range(3):
        try:
            response = requests.put(info["UploadUrl"], data=source, headers=headers, timeout=(10, 90))
            if response.ok:
                print("Source package uploaded", flush=True)
                return {"DeployType": "package", "PackageName": info["PackageName"],
                        "PackageVersion": info["PackageVersion"], "ReleaseType": "FULL"}
            if response.status_code < 500:
                raise RuntimeError(f"Source upload rejected: HTTP {response.status_code}")
        except requests.RequestException:
            pass  # Signed URLs/headers must never be included in logged exceptions.
        print(f"Source upload attempt {attempt + 1} failed; retrying", flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("Source upload failed after three bounded attempts")


def main():
    env_id = os.environ["CLOUDBASE_ENV_ID"]
    server = os.environ.get("CLOUDBASE_SERVICE", "trading")
    result = api("UpdateCloudRunServer", {
        "EnvId": env_id, "ServerName": server,
        "DeployInfo": {**upload_source(env_id, server),
                       "DeployRemark": "GitHub " + os.environ.get("GITHUB_SHA", "manual")[:12]},
        "Items": [{"Key": "Port", "IntValue": 8000},
                  {"Key": "MinNum", "IntValue": 1}, {"Key": "MaxNum", "IntValue": 1},
                  {"Key": "HasDockerfile", "BoolValue": True},
                  {"Key": "Dockerfile", "Value": "Dockerfile"},
                  {"Key": "BuildDir", "Value": "."}],
    })
    task_id = result["TaskId"]
    if not task_id:
        raise RuntimeError("CloudBase returned no deployment task ID")
    print(f"CloudBase deployment task {task_id} registered", flush=True)
    deadline = time.monotonic() + 25 * 60
    previous = None
    while time.monotonic() < deadline:
        task = api("DescribeServerManageTask", {
            "EnvId": env_id, "ServerName": server, "TaskId": task_id,
        }).get("Task", {})
        state = (task.get("Status"), [(step.get("Name"), step.get("Status"))
                                      for step in task.get("Steps", [])])
        if state != previous:
            print("Deployment:", state, flush=True)
            previous = state
        if task.get("Status") == "finished":
            print("CloudBase deployment finished", flush=True)
            return
        if task.get("Status") in {"failed", "stopped", "cancel", "cancelled"}:
            raise RuntimeError("CloudBase deployment failed: " + str(task.get("FailReason", "")))
        time.sleep(20)
    raise TimeoutError("CloudBase deployment still running after 25 minutes; do not duplicate it")


if __name__ == "__main__":
    main()
