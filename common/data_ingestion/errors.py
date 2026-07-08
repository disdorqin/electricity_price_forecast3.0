"""
Custom exceptions for the data ingestion pipeline.
"""


class DataIngestionError(Exception):
    """Base exception for all data ingestion errors."""


class FileScanError(DataIngestionError):
    """Raised when file scanning fails (e.g., directory not found, permission denied)."""


class ImportError(DataIngestionError):
    """Raised when a file could not be imported into the database."""


class DatasetNotReadyError(DataIngestionError):
    """Raised when attempting to build a dataset that does not have all required data."""
