"""Adapters for external services used by the backend and worker."""

from .r2 import (
    MAX_OBJECT_BYTES,
    R2ConfigurationError,
    R2Storage,
    R2StorageError,
)

__all__ = ["MAX_OBJECT_BYTES", "R2ConfigurationError", "R2Storage", "R2StorageError"]
