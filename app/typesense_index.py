"""
Typesense vector index for semantic-router.

Implements the BaseIndex interface so SemanticRouter can store and query
route embeddings in a Typesense collection instead of in-memory.

Typesense returns cosine *distance* (0 = identical, 2 = opposite).
semantic-router expects cosine *similarity* scores, so we convert:
    similarity = 1 - (distance / 2)
"""

import hashlib
import json
import os
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

import numpy as np
from pydantic import ConfigDict

from semantic_router.index.base import BaseIndex, IndexConfig
from semantic_router.schema import ConfigParameter, SparseEmbedding
from semantic_router.utils.logger import logger

try:
    import typesense
except ImportError:
    raise ImportError(
        "typesense package required. Install with: pip install typesense"
    )


# ── Defaults ────────────────────────────────────────────────────────────────
_DEFAULT_COLLECTION = os.environ.get("TYPESENSE_COLLECTION", "semantic_routes")


class TypesenseIndex(BaseIndex):
    """Persistent vector index backed by Typesense."""

    type: str = "typesense"
    collection_name: str = _DEFAULT_COLLECTION
    client: Optional[Any] = None  # typesense.Client

    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        collection_name: str = _DEFAULT_COLLECTION,
        typesense_host: str | None = None,
        typesense_port: str | None = None,
        typesense_protocol: str | None = None,
        typesense_api_key: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.collection_name = collection_name
        self.client = typesense.Client(
            {
                "nodes": [
                    {
                        "host": typesense_host
                        or os.environ.get("TYPESENSE_HOST", "localhost"),
                        "port": typesense_port
                        or os.environ.get("TYPESENSE_PORT", "8108"),
                        "protocol": typesense_protocol
                        or os.environ.get("TYPESENSE_PROTOCOL", "http"),
                    }
                ],
                "api_key": typesense_api_key
                or os.environ.get("TYPESENSE_API_KEY", ""),
                "connection_timeout_seconds": 10,
            }
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _ensure_collection(self, num_dim: int) -> None:
        """Create the Typesense collection if it doesn't already exist."""
        try:
            self.client.collections[self.collection_name].retrieve()
            logger.info("Typesense collection '%s' already exists.", self.collection_name)
        except typesense.exceptions.ObjectNotFound:
            schema = {
                "name": self.collection_name,
                "fields": [
                    {"name": "sr_id", "type": "string"},
                    {"name": "sr_route", "type": "string", "facet": True},
                    {"name": "sr_utterance", "type": "string"},
                    {"name": "sr_function_schema", "type": "string", "optional": True},
                    {"name": "sr_metadata", "type": "string", "optional": True},
                    {
                        "name": "vec",
                        "type": "float[]",
                        "num_dim": num_dim,
                    },
                ],
            }
            self.client.collections.create(schema)
            logger.info(
                "Created Typesense collection '%s' with %d dimensions.",
                self.collection_name,
                num_dim,
            )

    @staticmethod
    def _make_id(route: str, utterance: str) -> str:
        """Deterministic document ID from route + utterance."""
        raw = f"{route}::{utterance}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ── BaseIndex interface ──────────────────────────────────────────────────

    def add(
        self,
        embeddings: List[List[float]],
        routes: List[str],
        utterances: List[Any],
        function_schemas: Optional[List[Dict[str, Any]]] = None,
        metadata_list: List[Dict[str, Any]] = [],
        **kwargs,
    ):
        num_dim = len(embeddings[0])
        self._ensure_collection(num_dim)
        self.dimensions = num_dim

        documents = []
        for i, (emb, route, utt) in enumerate(zip(embeddings, routes, utterances)):
            doc = {
                "id": self._make_id(route, str(utt)),
                "sr_id": self._make_id(route, str(utt)),
                "sr_route": route,
                "sr_utterance": str(utt),
                "sr_function_schema": json.dumps(
                    function_schemas[i] if function_schemas and i < len(function_schemas) else None
                ),
                "sr_metadata": json.dumps(
                    metadata_list[i] if metadata_list and i < len(metadata_list) else {}
                ),
                "vec": emb,
            }
            documents.append(doc)

        # Batch upsert
        self.client.collections[self.collection_name].documents.import_(
            documents, {"action": "upsert"}
        )
        logger.info("Upserted %d documents into Typesense.", len(documents))

    def delete(self, route_name: str):
        """Delete all documents for a given route."""
        self.client.collections[self.collection_name].documents.delete(
            {"filter_by": f"sr_route:={route_name}"}
        )
        logger.info("Deleted route '%s' from Typesense.", route_name)

    async def adelete(self, route_name: str) -> list[str]:
        self.delete(route_name)
        return []

    def delete_all(self):
        try:
            self.client.collections[self.collection_name].delete()
            logger.info("Deleted Typesense collection '%s'.", self.collection_name)
        except typesense.exceptions.ObjectNotFound:
            pass

    def delete_index(self):
        self.delete_all()

    def describe(self) -> IndexConfig:
        try:
            info = self.client.collections[self.collection_name].retrieve()
            return IndexConfig(
                type=self.type,
                dimensions=self.dimensions or 0,
                vectors=info.get("num_documents", 0),
            )
        except typesense.exceptions.ObjectNotFound:
            return IndexConfig(type=self.type, dimensions=0, vectors=0)

    def is_ready(self) -> bool:
        try:
            self.client.collections[self.collection_name].retrieve()
            return True
        except Exception:
            return False

    async def ais_ready(self) -> bool:
        return self.is_ready()

    def query(
        self,
        vector: np.ndarray,
        top_k: int = 5,
        route_filter: Optional[List[str]] = None,
        sparse_vector: dict[int, float] | SparseEmbedding | None = None,
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Nearest-neighbor search against Typesense.

        Returns (scores, route_names) where scores are cosine *similarities*
        in descending order, matching semantic-router's expectations.
        """
        vec_str = ",".join(str(v) for v in vector.tolist())
        search_params = {
            "q": "*",
            "vector_query": f"vec:([{vec_str}], k:{top_k})",
            "per_page": top_k,
        }
        if route_filter:
            filter_expr = " || ".join(f"sr_route:={r}" for r in route_filter)
            search_params["filter_by"] = filter_expr

        results = self.client.collections[self.collection_name].documents.search(
            search_params
        )

        scores = []
        route_names = []
        for hit in results.get("hits", []):
            # Typesense returns cosine distance (0=identical, 2=opposite)
            # Convert to similarity: 1 - (distance / 2)
            distance = hit.get("vector_distance", 2.0)
            similarity = 1.0 - (distance / 2.0)
            scores.append(similarity)
            route_names.append(hit["document"]["sr_route"])

        return np.array(scores, dtype=np.float64), route_names

    async def aquery(
        self,
        vector: np.ndarray,
        top_k: int = 5,
        route_filter: Optional[List[str]] = None,
        sparse_vector: dict[int, float] | SparseEmbedding | None = None,
    ) -> Tuple[np.ndarray, List[str]]:
        return self.query(vector, top_k, route_filter, sparse_vector)

    def _get_all(
        self, prefix: Optional[str] = None, include_metadata: bool = False
    ) -> tuple[list[str], list[dict]]:
        """Retrieve all documents. Used by get_utterances / get_routes."""
        ids: list[str] = []
        metadata: list[dict] = []

        page = 1
        per_page = 250
        while True:
            results = self.client.collections[self.collection_name].documents.search(
                {"q": "*", "per_page": per_page, "page": page}
            )
            hits = results.get("hits", [])
            if not hits:
                break
            for hit in hits:
                doc = hit["document"]
                ids.append(doc.get("sr_id", doc.get("id", "")))
                meta = {
                    "sr_route": doc.get("sr_route", ""),
                    "sr_utterance": doc.get("sr_utterance", ""),
                    "sr_function_schema": doc.get("sr_function_schema", "{}"),
                }
                if include_metadata and doc.get("sr_metadata"):
                    try:
                        extra = json.loads(doc["sr_metadata"])
                        meta.update(extra)
                    except (json.JSONDecodeError, TypeError):
                        pass
                metadata.append(meta)
            page += 1

        return ids, metadata

    async def _async_get_all(
        self, prefix: Optional[str] = None, include_metadata: bool = False
    ) -> tuple[list[str], list[dict]]:
        return self._get_all(prefix, include_metadata)

    def _remove_and_sync(self, routes_to_delete: dict):
        for route, utterances in routes_to_delete.items():
            for utt in utterances:
                doc_id = self._make_id(route, str(utt))
                try:
                    self.client.collections[self.collection_name].documents[doc_id].delete()
                except typesense.exceptions.ObjectNotFound:
                    pass

    def _init_index(self, force_create: bool = False) -> Union[Any, None]:
        if self.dimensions:
            self._ensure_collection(self.dimensions)
        return None

    async def _init_async_index(self, force_create: bool = False):
        return self._init_index(force_create)

    def __len__(self) -> int:
        try:
            info = self.client.collections[self.collection_name].retrieve()
            return info.get("num_documents", 0)
        except Exception:
            return 0

    # ── config read/write (used for sync hashing) ───────────────────────────

    def _read_config(self, field: str, scope: str | None = None) -> ConfigParameter:
        """Read a config value stored as a special document."""
        config_id = f"__config__{field}"
        try:
            doc = self.client.collections[self.collection_name].documents[config_id].retrieve()
            return ConfigParameter(field=field, value=doc.get("sr_utterance", ""), scope=scope)
        except typesense.exceptions.ObjectNotFound:
            return ConfigParameter(field=field, value="", scope=scope)

    def _write_config(self, config: ConfigParameter) -> ConfigParameter:
        """Write a config value as a special document."""
        if self.dimensions is None:
            return config
        config_id = f"__config__{config.field}"
        doc = {
            "id": config_id,
            "sr_id": config_id,
            "sr_route": "__config__",
            "sr_utterance": str(config.value),
            "sr_function_schema": "{}",
            "sr_metadata": "{}",
            "vec": [0.0] * (self.dimensions or 1),
        }
        self.client.collections[self.collection_name].documents.upsert(doc)
        return config
