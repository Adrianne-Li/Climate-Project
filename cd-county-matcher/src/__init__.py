"""cd-county-matcher: Match US Congressional Districts to counties by area overlap."""

from .matcher import CDCountyMatcher, UpdatedMatcher, MANUAL_SOURCE_URLS

__version__ = "1.0.0"
__all__ = ["CDCountyMatcher", "UpdatedMatcher", "MANUAL_SOURCE_URLS"]
