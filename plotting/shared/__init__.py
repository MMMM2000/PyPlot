"""Shared utilities for PyPlot plug-ins."""

from .readability import (
    ReadabilityControls,
    apply_readability,
    apply_readability_fonts,
    create_readability_group,
    sync_readability,
)
from .settings import get_settings
from .developer import developer_options
from .paths import (
    download_dir,
    sample_dir,
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
    get_last_used_dir,
    set_last_used_dir,
)
from .origin import origin_session, release_origin, schedule_origin_release
from .theme import ensure_app_theme, apply_system_theme

__all__ = [
    "ReadabilityControls",
    "apply_readability",
    "apply_readability_fonts",
    "create_readability_group",
    "sync_readability",
    "get_settings",
    "download_dir",
    "sample_dir",
    "prepare_output_dir",
    "get_last_output_dir",
    "set_last_output_dir",
    "get_last_used_dir",
    "set_last_used_dir",
    "origin_session",
    "schedule_origin_release",
    "release_origin",
]
