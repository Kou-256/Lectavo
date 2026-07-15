"""GUI- and CLI-neutral processing API for Lectavo."""

from .processor import (
    PRESETS,
    BatchResult,
    OperationOptions,
    ProcessingCancelled,
    ProcessingConfigurationError,
    ProcessingOptions,
    ProcessResult,
    ProgressEvent,
    discover_videos,
    is_cuda_available,
    process_batch,
    process_video,
    sanitize_stem,
)

__all__ = [
    "BatchResult",
    "OperationOptions",
    "PRESETS",
    "ProcessingCancelled",
    "ProcessingConfigurationError",
    "ProcessingOptions",
    "ProcessResult",
    "ProgressEvent",
    "discover_videos",
    "is_cuda_available",
    "process_batch",
    "process_video",
    "sanitize_stem",
]
