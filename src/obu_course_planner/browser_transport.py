"""Bounded JSONL connection to the refresh-only, paced stealth browser."""
from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
from typing import Any
from urllib.parse import urlencode


class BrowserSession:
    """One browser/cookie session; the Node transport paces every network request."""

    def __init__(self, delay: float = 1.0) -> None:
        if not 0.1 <= delay <= 10:
            raise ValueError("Browser request interval must be between 0.1 and 10 seconds")
        helper = Path(__file__).with_name("browser") / "transport.cjs"
        try:
            self._process = subprocess.Popen(
                ["node", str(helper), "--interval-ms", str(delay * 1000)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError("Cannot start refresh browser transport; install Node and browser dependencies.") from exc
        self.requests = 0
        self._responses: Queue[str | None] = Queue()
        self._reader = Thread(target=self._read_responses, daemon=True)
        self._reader.start()

    def _read_responses(self) -> None:
        try:
            for line in self._process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _exchange(self, command: dict, timeout: float = 120) -> dict:
        try:
            self._process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            line = self._responses.get(timeout=timeout)
        except (BrokenPipeError, OSError, Empty) as exc:
            self._stop()
            raise RuntimeError("Refresh browser transport stopped or timed out; snapshot not replaced.") from exc
        if line is None:
            raise RuntimeError("Refresh browser transport exited without a response; snapshot not replaced.")
        try:
            result = json.loads(line)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ValueError("missing result")
            if not result["ok"]:
                raise RuntimeError(f"OBU browser request failed: {result.get('error', 'unknown browser error')}")
            count = result["request_count"]
            if not isinstance(count, int) or count < self.requests:
                raise ValueError("invalid request counter")
            self.requests = count
            return result
        except (ValueError, KeyError) as exc:
            raise RuntimeError("Invalid refresh browser response; snapshot not replaced.") from exc

    def request(self, method: str, url: str, *, params: dict | None = None, data: dict | None = None) -> str:
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        command: dict[str, Any] = {"method": method, "url": url}
        if data is not None:
            command["data"] = data
        result = self._exchange(command)
        if not isinstance(result.get("html"), str):
            raise RuntimeError("Refresh browser returned no HTML; snapshot not replaced.")
        return result["html"]

    def _stop(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def close(self) -> None:
        try:
            if self._process.poll() is None:
                self._exchange({"method": "CLOSE"}, timeout=15)
                self._process.wait(timeout=5)
        finally:
            self._stop()
            with suppress(BrokenPipeError, OSError):
                self._process.stdin.close()
            self._reader.join(timeout=1)
            self._process.stdout.close()

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            # Preserve the capture error even if closing the failed browser also fails.
            with suppress(Exception):
                self.close()
