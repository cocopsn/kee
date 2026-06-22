"""Tiny ChromaDB v2 HTTP client.

We don't pull the `chromadb` Python package — it's heavy and pulls in DuckDB
and a bunch of server bits we don't want on the agent node. Instead we hit
the v2 REST API directly with httpx. The API is stable and small enough
that this is cleaner than vendoring the SDK.

ChromaDB v2 paths are tenant-aware:
    /api/v2/tenants/{tenant}/databases/{db}/collections
    /api/v2/tenants/{tenant}/databases/{db}/collections/{name}/add
    /api/v2/tenants/{tenant}/databases/{db}/collections/{name}/query

We use `default_tenant` and `default_database` unless overridden.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

import httpx

from kee.config import settings

logger = logging.getLogger(__name__)


DEFAULT_TENANT = os.environ.get("KEE_CHROMA_TENANT", "default_tenant")
DEFAULT_DATABASE = os.environ.get("KEE_CHROMA_DATABASE", "default_database")


class ChromaUnavailable(RuntimeError):
    pass


class ChromaClient:
    def __init__(
        self,
        host: str | None = None,
        tenant: str = DEFAULT_TENANT,
        database: str = DEFAULT_DATABASE,
        timeout_s: float = 10.0,
    ) -> None:
        self.host = (host or settings.chromadb_host).rstrip("/")
        self.tenant = tenant
        self.database = database
        self.timeout = timeout_s
        # ChromaDB v2 paths require the collection's UUID, not its name.
        # Cache the resolution name → uuid so we hit /collections/<name> once.
        self._uuid_cache: dict[str, str] = {}

    # ── Internal helpers ──────────────────────────────────────────────────
    def _base(self) -> str:
        return f"{self.host}/api/v2/tenants/{self.tenant}/databases/{self.database}"

    def _coll_path(self, name_or_id: str, suffix: str = "") -> str:
        return f"{self._base()}/collections/{name_or_id}{suffix}"

    async def _coll_uuid(self, name: str) -> str:
        """Resolve the collection name to its UUID. Cached after first call."""
        cached = self._uuid_cache.get(name)
        if cached:
            return cached
        meta = await self._request("GET", self._coll_path(name))
        if not isinstance(meta, dict) or "id" not in meta:
            raise ChromaUnavailable(
                f"ChromaDB returned no `id` for collection {name!r}: {meta}"
            )
        self._uuid_cache[name] = meta["id"]
        return meta["id"]

    async def _request(self, method: str, url: str, json: Any = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(method, url, json=json)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            raise ChromaUnavailable(f"ChromaDB at {self.host} unreachable: {e}") from e

        if r.status_code >= 400:
            raise ChromaUnavailable(
                f"ChromaDB {method} {url} returned {r.status_code}: {r.text[:300]}"
            )
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # ── Public API ────────────────────────────────────────────────────────
    async def health(self) -> bool:
        try:
            await self._request("GET", f"{self.host}/api/v2/heartbeat")
            return True
        except ChromaUnavailable:
            return False

    async def get_or_create_collection(self, name: str, metadata: dict | None = None) -> dict:
        try:
            meta = await self._request("GET", self._coll_path(name))
        except ChromaUnavailable as e:
            err = str(e)
            missing = (
                "404" in err
                or "does not exist" in err.lower()
                or "InvalidCollection" in err
            )
            if not missing:
                raise
            meta = await self._request(
                "POST",
                f"{self._base()}/collections",
                json={"name": name, "metadata": metadata or {},
                      "get_or_create": True},
            )
        # Cache the UUID so subsequent add/upsert/query don't have to GET again.
        if isinstance(meta, dict) and meta.get("id"):
            self._uuid_cache[name] = meta["id"]
        return meta

    async def add(
        self,
        collection: str,
        ids: Sequence[str],
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict] | None = None,
    ) -> None:
        if not ids:
            return
        body: dict[str, Any] = {
            "ids": list(ids),
            "documents": list(documents),
            "embeddings": [list(e) for e in embeddings],
        }
        if metadatas:
            body["metadatas"] = list(metadatas)
        uuid = await self._coll_uuid(collection)
        await self._request("POST", self._coll_path(uuid, "/add"), json=body)

    async def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict] | None = None,
    ) -> None:
        if not ids:
            return
        body: dict[str, Any] = {
            "ids": list(ids),
            "documents": list(documents),
            "embeddings": [list(e) for e in embeddings],
        }
        if metadatas:
            body["metadatas"] = list(metadatas)
        uuid = await self._coll_uuid(collection)
        await self._request("POST", self._coll_path(uuid, "/upsert"), json=body)

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        uuid = await self._coll_uuid(collection)
        await self._request(
            "POST",
            self._coll_path(uuid, "/delete"),
            json={"ids": list(ids)},
        )

    async def query(
        self,
        collection: str,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "query_embeddings": [list(e) for e in query_embeddings],
            "n_results": n_results,
        }
        if where:
            body["where"] = where
        uuid = await self._coll_uuid(collection)
        return await self._request(
            "POST", self._coll_path(uuid, "/query"), json=body,
        )
