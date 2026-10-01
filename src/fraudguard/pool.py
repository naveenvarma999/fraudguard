"""Bounded least-busy inference routing; workers never write workspace state."""

import json
import math
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from fastapi import HTTPException


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Pool:
    def __init__(self, urls, key, transport=None):
        self.urls = [url.strip().rstrip("/") for url in urls.split(",") if url.strip()]
        if not self.urls or len(set(self.urls)) != len(self.urls):
            raise ValueError("Provide distinct inference worker URLs")
        for url in self.urls:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Worker URLs must be HTTP(S) origins without credentials")
        if len(key) < 32:
            raise ValueError("INFERENCE_KEY must contain at least 32 characters")
        self.key, self.transport = key, transport or self._request
        self.lock = threading.Lock()
        self.busy = [0] * len(self.urls)
        self.failed_until = [0.0] * len(self.urls)
        self.counts = [0] * len(self.urls)
        self.cursor = 0

    def _request(self, url, payload):
        request = urllib.request.Request(
            url + "/internal/score",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Inference-Key": self.key},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=8) as response:
            return json.loads(response.read(65536))

    def score(self, rows, version, digest):
        # At most one attempt per worker. Retries are safe here: workers have no writes.
        tried = set()
        for _ in self.urls:
            with self.lock:
                now = time.monotonic()
                choices = [
                    i
                    for i in range(len(self.urls))
                    if i not in tried and self.busy[i] < 2 and self.failed_until[i] <= now
                ]
                if not choices:
                    break
                i = min(choices, key=lambda n: (self.busy[n], (n - self.cursor) % len(self.urls)))
                self.cursor = (i + 1) % len(self.urls)
                self.busy[i] += 1
            tried.add(i)
            try:
                result = self.transport(
                    self.urls[i], {"transactions": rows, "version": version, "digest": digest}
                )
                scores = result["probabilities"]
                if (
                    result["version"] != version
                    or len(scores) != len(rows)
                    or not all(
                        type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in scores
                    )
                ):
                    raise ValueError("Invalid worker response")
                with self.lock:
                    self.counts[i] += 1
                return scores
            except Exception:
                with self.lock:
                    self.failed_until[i] = time.monotonic() + 10
            finally:
                with self.lock:
                    self.busy[i] -= 1
        raise HTTPException(
            503,
            "Inference workers unavailable or busy. Retry with backoff.",
            headers={"Retry-After": "10"},
        )

    def status(self):
        with self.lock:
            return [
                {
                    "worker": i + 1,
                    "in_flight": self.busy[i],
                    "completed": self.counts[i],
                    "circuit_open": self.failed_until[i] > time.monotonic(),
                }
                for i in range(len(self.urls))
            ]
