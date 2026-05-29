"""cd-county-matcher: Match US Congressional Districts to counties by area overlap."""

from .matcher import (
    CDCountyMatcher,
    UpdatedMatcher,
    MANUAL_SOURCE_URLS,
    OSF_DOWNLOAD_BASE,
    OSF_API_BASE,
    osf_download_url,
    discover_osf_files,
    load_osf_guids,
    load_osf_project_guid,
)

__version__ = "1.0.0"
__all__ = [
    "CDCountyMatcher",
    "UpdatedMatcher",
    "MANUAL_SOURCE_URLS",
    "OSF_DOWNLOAD_BASE",
    "OSF_API_BASE",
    "osf_download_url",
    "discover_osf_files",
    "load_osf_guids",
    "load_osf_project_guid",
]
