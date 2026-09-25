"""Evaluation layer: performance metrics and result persistence."""

from src.evaluation.metrics import compute_metrics
from src.evaluation.report import RunMetadata, save_run

__all__ = ["RunMetadata", "compute_metrics", "save_run"]
