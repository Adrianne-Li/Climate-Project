"""cd-county-matcher: match US Congressional Districts to counties."""

from .matcher import (
    CDCountyMatcher,
    UpdatedMatcher,
    MANUAL_SOURCE_URLS,
    OSF_API_BASE,
    OSF_DOWNLOAD_BASE,
    discover_osf_files,
    load_osf_guids,
    load_osf_project_guid,
    osf_download_url,
)

__version__ = "1.1.0"

__all__ = [
    "CDCountyMatcher",
    "UpdatedMatcher",
    "MANUAL_SOURCE_URLS",
    "OSF_API_BASE",
    "OSF_DOWNLOAD_BASE",
    "discover_osf_files",
    "load_osf_guids",
    "load_osf_project_guid",
    "osf_download_url",
]
