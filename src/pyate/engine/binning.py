"""Binning: turn the collected results into soft/hard bins."""

from __future__ import annotations

from ..config.models import BinningConfig
from ..engine.context import SiteContext

__all__ = ["BinningEngine"]


class BinningEngine:
    """Applies ``binning.yaml`` rules to a finished part."""

    def __init__(self, config: BinningConfig) -> None:
        self.config = config

    def soft_bin(self, ctx: SiteContext, forced_bin: int | None = None) -> int:
        """Resolve the soft bin for a site context.

        An explicit ``forced_bin`` (``goto_bin`` node or a per-item bin
        override) wins even when no result failed, so a flow can park a part in
        an engineering bin on purpose.
        """
        if forced_bin is not None:
            return int(forced_bin)
        failed = [r for r in ctx.results if not r.passed and not r.skipped]
        if not failed:
            return self.config.pass_bin
        mapped: list[int] = []
        for result in failed:
            candidate = self.config.fail_code_bins.get(result.fail_code)
            if candidate is not None:
                mapped.append(candidate)
        if mapped:
            priority = self.config.bin_priority
            for sbin in priority:
                if sbin in mapped:
                    return sbin
            return min(mapped)
        return self.config.default_fail_bin

    def compute(self, ctx: SiteContext, forced_bin: int | None = None) -> tuple[int, int]:
        """Return ``(soft_bin, hard_bin)`` and store them on the context."""
        sbin = self.soft_bin(ctx, forced_bin)
        hbin = self.config.hard_bin_of(sbin)
        ctx.soft_bin = sbin
        ctx.hard_bin = hbin
        return sbin, hbin

    def is_pass(self, sbin: int) -> bool:
        """True when ``sbin`` is the configured pass bin."""
        return sbin == self.config.pass_bin
