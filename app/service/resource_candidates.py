"""同步正式文件快照，复用 MyVector HNSW 完成同平台或跨平台检索。"""

import asyncio
import hashlib

from llama_index.core.schema import TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters, VectorStoreQuery

from app.infrastructure.myvector import MyVectorStore
from app.repository.resource import MySQLResourceRepository
from app.service.embedding import validate_vector
from app.model.resource import ResourceCandidate


class ReadyResourceSearch:
    def __init__(self, repository: MySQLResourceRepository, *, model_name: str, dimensions: int):
        self.repository = repository
        self.model_name = model_name
        self.dimensions = dimensions
        self._store: MyVectorStore | None = None
        self._lock = asyncio.Lock()

    async def search(self, source_platform: str, vector: list[float], *, top_k: int, min_similarity: float) -> list[ResourceCandidate]:
        return await self._search(source_platform, vector, top_k=top_k, min_similarity=min_similarity)

    async def search_all_platforms(self, vector: list[float], *, top_k: int, min_similarity: float) -> list[ResourceCandidate]:
        return await self._search(None, vector, top_k=top_k, min_similarity=min_similarity)

    async def _search(self, source_platform: str | None, vector: list[float], *, top_k: int, min_similarity: float) -> list[ResourceCandidate]:
        validate_vector(vector, self.dimensions)
        if type(top_k) is not int or not 1 <= top_k <= 100:
            raise ValueError("top_k 必须为 1 至 100 的整数")
        async with self._lock:
            rows = await self.repository.list_vectors(source_platform)
            if source_platform is None:
                # 无通行证的文件不占推荐候选名额，且不参与该次全平台快照。
                rows = [row for row in rows if bool(row.get("passport"))]
            if not rows:
                if self._store is not None:
                    await self._clear_snapshot(source_platform)
                return []
            # 业务表是权威来源，索引快照不能决定审核状态。校验完全部向量才替换快照。
            for row in rows:
                validate_vector(row["embedding"], self.dimensions)
            if self._store is None:
                self._store = MyVectorStore(self.repository.pool, model_name=self.model_name,
                                            dimensions=self.dimensions, operation_timeout_seconds=60)
            nodes = [TextNode(
                id_=str(row["id"]), text=row["abstract"] or "", embedding=row["embedding"],
                metadata={"resource_id": row["id"], "source_platform": row["source_platform"],
                          "source_id": row["source_id"], "file_url": row["file_url"], "status": "ready",
                          "name": row["name"], "abstract": row["abstract"],
                          "applicant": row.get("applicant"), "reviewer": row.get("reviewer"),
                          "passport": row.get("passport")},
                relationships={NodeRelationship.SOURCE: RelatedNodeInfo(
                    node_id="ready:" + hashlib.sha256(row["source_platform"].encode("utf-8")).hexdigest())},
            ) for row in rows]
            await self._clear_snapshot(source_platform)
            await self._store.async_add(nodes)
            filters = [MetadataFilter(key="status", value="ready")]
            if source_platform is not None:
                filters.insert(0, MetadataFilter(key="source_platform", value=source_platform))
            result = await self._store.aquery(VectorStoreQuery(
                query_embedding=vector, similarity_top_k=top_k,
                filters=MetadataFilters(filters=filters),
            ))
            return [{"resource_id": node.metadata["resource_id"],
                     "source_platform": node.metadata["source_platform"], "source_id": node.metadata["source_id"],
                     "file_url": node.metadata["file_url"], "name": node.metadata["name"],
                     "abstract": node.metadata["abstract"], "similarity": similarity,
                     "applicant": node.metadata.get("applicant"), "reviewer": node.metadata.get("reviewer"),
                     "passport": node.metadata.get("passport")}
                    for node, similarity in zip(result.nodes or [], result.similarities or [])
                    if (similarity > min_similarity if source_platform is None else similarity >= min_similarity)]

    async def _clear_snapshot(self, source_platform: str | None) -> None:
        if source_platform is None:
            await self._store.adelete_status("ready")
        else:
            await self._store.adelete_platform(source_platform)
