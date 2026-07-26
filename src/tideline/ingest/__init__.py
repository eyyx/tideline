"""Source adapters. The registry maps an ATS name to its fetch function."""

from tideline.ingest import ashby, greenhouse, lever
from tideline.ingest.base import Adapter, CompanySpec, IngestError, load_companies, make_client

ATS_ADAPTERS: dict[str, Adapter] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}

__all__ = [
    "ATS_ADAPTERS",
    "Adapter",
    "CompanySpec",
    "IngestError",
    "load_companies",
    "make_client",
]
