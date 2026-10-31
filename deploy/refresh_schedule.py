#!/usr/bin/python3
"""Run the deployed snapshot refresher in a bounded, disposable container.

Configured by environment (systemd EnvironmentFile=/etc/obu-course-planner/refresh.env):
  OBU_PLANNER_REFRESH_IMAGE  refresher image reference (built from Dockerfile.refresh, versioned
                             independently of the request-serving API image)
  OBU_PLANNER_VOLUME         docker volume holding schedule-snapshot.json

One paced pass, no retries: the native transport enforces at most one outbound HTTP(S) request per
second across a whole capture, and a failed run leaves the previous snapshot in place. Nothing here
touches the running API container.
"""
import os
import subprocess

image = os.environ["OBU_PLANNER_REFRESH_IMAGE"]
volume = os.environ["OBU_PLANNER_VOLUME"]

subprocess.run([
    "docker", "run", "--rm", "--name", "obu-course-planner-refresh",
    "--user", "10002:10001",
    "--memory", "1g", "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "256",
    "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--shm-size", "256m",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--mount", f"type=volume,source={volume},target=/data/schedule",
    image, "--output", "/data/schedule/schedule-snapshot.json", "--delay", "1",
], check=True)