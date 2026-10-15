"""Exceptions shared by the modules. Messages are written for the person running the pipeline."""

from __future__ import annotations


class PipelineError(Exception):
    """Base class of every expected error."""


class ConfigError(PipelineError):
    """The configuration file is missing, malformed or contains invalid values."""


class SourceError(PipelineError):
    """A source file is missing, unreadable or does not have the expected structure."""


class WarehouseError(PipelineError):
    """The database cannot be reached, is not a pipeline database, or a statement failed."""


class QualityGateError(PipelineError):
    """The data does not meet the configured quality thresholds; nothing is published."""


class ConsistencyError(PipelineError):
    """An independent reconciliation check found a difference (e.g. totals or row counts)."""
