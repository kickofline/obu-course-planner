#!/usr/bin/python3
"""Run the deployed app's snapshot refresher in a bounded, disposable container.

Configured by environment (systemd EnvironmentFile=/etc/obu-course-planner/refresh.env):
  OBU_PLANNER_CONTAINER  name prefix of the running API container (used to find its image)
  OBU_PLANNER_VOLUME     docker volume holding schedule-snapshot.json
  OBU_REFRESH_USER_AGENT optional User-Agent for requests to OBU (passed into the container)
"""
import os
import subprocess

prefix = os.environ["OBU_PLANNER_CONTAINER"]
volume = os.environ["OBU_PLANNER_VOLUME"]

images = subprocess.check_output(
    ["docker", "ps", "--filter", f"name={prefix}", "--format", "{{.Image}}"], text=True,
).splitlines()
images = list(dict.fromkeys(images))
if len(images) != 1:
    raise SystemExit("Expected one deployed course planner API image; refresh not started during missing/ambiguous rollout.")

env = ["--env", "OBU_REFRESH_USER_AGENT"] if os.environ.get("OBU_REFRESH_USER_AGENT") else []
subprocess.run([
    "docker", "run", "--rm", "--name", "obu-course-planner-refresh", *env,
    "--user", "10002:10001", "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
    "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--mount", f"type=volume,source={volume},target=/data/schedule",
    "--entrypoint", "python", images[0], "-m", "obu_course_planner.refresh",
    "--output", "/data/schedule/schedule-snapshot.json", "--delay", "1",
], check=True)
