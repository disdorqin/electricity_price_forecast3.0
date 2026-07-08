"""
Custom exceptions for the DB layer.
"""


class DbError(Exception):
    """Base DB error."""


class ConnectionError(DbError):
    """Cannot connect to database."""


class SchemaError(DbError):
    """Schema initialization failed."""


class QueryError(DbError):
    """Query execution failed."""


class FormalModeRequiresDb(DbError):
    """Formal mode requires a database connection."""


class ShadowContaminationError(DbError):
    """Shadow prediction would contaminate final output."""
