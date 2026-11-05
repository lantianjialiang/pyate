"""Reference plugin #1: an ADC DUT model plus an INL/DNL test item.

This package only touches the public extension points (``register_dut`` /
``register_testitem``); no core source file has to change to use it.
"""

from __future__ import annotations

from typing import Any

from pyate.plugins.hookspec import API_VERSION, hookimpl

from .dut import AdcModel
from .items import AdcInlDnlItem, AdcOffsetGainItem

__version__ = "0.1.0"
__all__ = ["AdcModel", "AdcInlDnlItem", "AdcOffsetGainItem", "Plugin"]


class Plugin:
    """Entry object referenced by the ``pyate.plugins`` entry point."""

    @hookimpl
    def pyate_api_version(self) -> str:
        """Plugin API version this plugin targets."""
        return API_VERSION

    @hookimpl
    def pyate_register(self, registry: Any) -> None:
        """Register the ADC DUT model and the linearity test items."""
        registry.add_dut("adc", AdcModel)
        registry.add_testitem("adc_inl_dnl", AdcInlDnlItem)
        registry.add_testitem("adc_offset_gain", AdcOffsetGainItem)
        registry.note("plugin_adc_dut: adc model + adc_inl_dnl/adc_offset_gain items")
