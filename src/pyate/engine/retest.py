"""Retest policy: decide which parts to retest and which result to keep."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import PartResult

__all__ = ["RetestConfig", "should_retest", "merge_attempt", "CONTACT_FAIL_BIN"]


#: Soft bin assigned by the flow executor when a site fails to make contact.
CONTACT_FAIL_BIN = 2


@dataclass
class RetestConfig:
    """Resolved retest settings for one run."""

    bins: list[int] | None = None
    max_retries: int = 0
    on: tuple[str, ...] = ()
    keep: str = "last"

    @classmethod
    def from_session(cls, session: Any, policy: Any = None) -> "RetestConfig":
        """Merge the session ``run.retest_bins`` with an optional batch policy."""
        bins = list(getattr(session.run, "retest_bins", []) or [])
        retries = int(getattr(session.run, "max_retries", 0) or 0)
        on: tuple[str, ...] = ()
        keep = "last"
        if policy is not None:
            bins = list(getattr(policy, "bins", []) or bins)
            retries = int(getattr(policy, "max_retries", retries) or 0)
            on = tuple(getattr(policy, "on", ()) or ())
            keep = str(getattr(policy, "keep", keep) or keep)
            if "contact_fail" in on and CONTACT_FAIL_BIN not in bins:
                bins.append(CONTACT_FAIL_BIN)
        return cls(bins=sorted(set(int(b) for b in bins)), max_retries=retries,
                   on=on, keep=keep)

    @property
    def enabled(self) -> bool:
        """True when at least one retry is allowed."""
        return self.max_retries > 0 and bool(self.bins)

    @classmethod
    def from_policy(cls, policy: Any) -> "RetestConfig":
        """Build a config from any object exposing ``bins``/``max_retries``/``on``."""
        if policy is None:
            return cls()
        bins = [int(value) for value in (getattr(policy, "bins", None) or [])]
        on = tuple(getattr(policy, "on", ()) or ())
        if "contact_fail" in on and CONTACT_FAIL_BIN not in bins:
            bins.append(CONTACT_FAIL_BIN)
        return cls(bins=sorted(set(bins)),
                   max_retries=int(getattr(policy, "max_retries", 0) or 0),
                   on=on, keep=str(getattr(policy, "keep", "last") or "last"))


def should_retest(part: PartResult, config: RetestConfig, attempt: int) -> bool:
    """True when ``part`` still deserves a retry on ``attempt`` (1-based)."""
    if attempt > config.max_retries:
        return False
    if part.skipped or part.passed:
        return False
    if part.soft_bin not in (config.bins or []):
        return False
    if part.extra.get("retest_exhausted"):
        return False
    return True


def merge_attempt(previous: PartResult, candidate: PartResult, keep: str = "last") -> PartResult:
    """Combine the previous verdict with the newest attempt.

    ``keep="last"`` (default) takes the newest result, ``keep="best"`` keeps the
    lowest *failing* bin when the newest attempt is still failing.
    """
    candidate.extra["previous_attempts"] = [
        *previous.extra.get("previous_attempts", []),
        previous,
    ]
    candidate.extra["retest"] = True
    candidate.retest = True
    if keep == "best" and not candidate.passed and not previous.passed:
        if int(previous.soft_bin) < int(candidate.soft_bin):
            previous.extra["previous_attempts"] = candidate.extra["previous_attempts"]
            previous.retest = True
            previous.extra["retest"] = True
            return previous
    return candidate
