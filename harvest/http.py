from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

USER_AGENT = "ff-assets-historical-witness/1.0 (+https://github.com/jeleff1000/ff-assets)"


def classify_status(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status == 404:
        return "absent"
    if status == 429:
        return "throttled"
    if status >= 500:
        return "retryable"
    if status in {401, 403}:
        return "blocked"
    return "error"


@dataclass(frozen=True)
class Response:
    url: str
    status_code: int
    status: str
    body: bytes
    content_type: str
    error: str | None = None


class BudgetExhausted(RuntimeError):
    """The shard spent its wall-clock budget. Raised so the job FAILS rather than being
    cancelled by the runner hours later."""


class HttpClient:
    def __init__(
        self, *, delay_seconds: float, retries: int = 3, budget_seconds: float | None = None
    ) -> None:
        self.delay_seconds = delay_seconds
        self.retries = retries
        # WALL-CLOCK BUDGET. Retries escalate (30/60/90s on throttle) and nothing bounded
        # the total, so a shard that hit sustained network trouble consumed its entire
        # 350-minute job timeout and was then CANCELLED -- which is not a failure, so it
        # did not even trip the zero-record gate cleanly. Measured 2026-07-27:
        # team_season_results shard 0 ran 5h50m on a 504-seed partition while its
        # siblings did 468-489 seeds in ~17 MINUTES each. Not a size problem -- 3% more
        # work, 20x the time -- so re-slicing would not have helped; only a budget does.
        self.budget_seconds = budget_seconds
        self._started = time.monotonic()
        self._last_request = 0.0

    def _check_budget(self) -> None:
        if self.budget_seconds is None:
            return
        spent = time.monotonic() - self._started
        if spent > self.budget_seconds:
            raise BudgetExhausted(
                f"wall-clock budget exhausted after {spent / 60:.1f} min "
                f"(limit {self.budget_seconds / 60:.0f} min) -- the host is not serving "
                "this runner at a usable rate; fail now rather than burning the job timeout"
            )

    def _pace(self) -> None:
        remaining = self.delay_seconds - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def fetch(self, url: str) -> Response:
        self._check_budget()
        for attempt in range(self.retries):
            self._pace()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=45) as reply:
                    self._last_request = time.monotonic()
                    body = reply.read()
                    code = int(reply.status)
                    return Response(url, code, classify_status(code), body, reply.headers.get_content_type())
            except urllib.error.HTTPError as exc:
                self._last_request = time.monotonic()
                status = classify_status(exc.code)
                if status == "throttled":
                    retry_after = exc.headers.get("Retry-After")
                    seconds = float(retry_after) if retry_after and retry_after.isdigit() else 30.0 * (attempt + 1)
                    time.sleep(seconds)
                elif status == "retryable" and attempt + 1 < self.retries:
                    time.sleep(3.0 * (attempt + 1))
                else:
                    return Response(url, exc.code, status, b"", "", str(exc))
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last_request = time.monotonic()
                if attempt + 1 == self.retries:
                    return Response(url, 0, "network_error", b"", "", str(exc))
                time.sleep(3.0 * (attempt + 1))
        return Response(url, 0, "network_error", b"", "", "retry exhaustion")

