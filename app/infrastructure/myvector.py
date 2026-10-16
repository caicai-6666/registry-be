"""试用级 LlamaIndex MyVector 适配器：异步写入、HNSW 检索和过滤。"""

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Literal

import aiomysql
from pydantic import Field, PrivateAttr
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores.types import (
    BasePydanticVectorStore, FilterCondition, FilterOperator, MetadataFilter,
    VectorStoreQuery, VectorStoreQueryMode, VectorStoreQueryResult,
)
from llama_index.core.vector_stores.utils import metadata_dict_to_node, node_to_metadata_dict

from app.service.embedding import validate_vector


class MyVectorStore(BasePydanticVectorStore):
    stores_text: bool = True
    model_name: str = Field(min_length=1, max_length=200)
    dimensions: int = Field(ge=1, le=4096)
    search_mode: Literal["hnsw", "exact"] = "hnsw"
    candidate_limit: int = Field(default=10000, ge=100, le=10000)
    operation_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    _pool: aiomysql.Pool = PrivateAttr()
    _operation_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _index_dirty: bool = PrivateAttr(default=True)

    def __init__(self, pool: aiomysql.Pool, **kwargs):
        super().__init__(**kwargs)
        self._pool = pool

    @property
    def client(self):
        return self._pool

    def add(self, nodes, **kwargs):
        raise NotImplementedError("请使用 async_add 或异步 LlamaIndex 索引")

    def query(self, query, **kwargs):
        raise NotImplementedError("请使用 aquery 或 retriever.aretrieve")

    def delete(self, ref_doc_id, **kwargs):
        raise NotImplementedError("请使用 adelete")

    async def async_add(self, nodes: Sequence[BaseNode], **kwargs) -> list[str]:
        async with self._operation_lock:
            return await self._add(nodes)

    async def _add(self, nodes: Sequence[BaseNode]) -> list[str]:
        values = []
        for node in nodes:
            vector = node.get_embedding()
            validate_vector(vector, self.dimensions)
            if not node.node_id or len(node.node_id) > 128:
                raise ValueError("节点标识长度必须为 1 至 128")
            values.append((node.node_id, self.model_name, self.dimensions,
                           node.ref_doc_id, node.metadata.get("source_platform"), node.metadata.get("status"),
                           json.dumps(node_to_metadata_dict(node), ensure_ascii=False, allow_nan=False),
                           json.dumps(vector, allow_nan=False)))
        if not values:
            return []
        self._index_dirty = True
        async with asyncio.timeout(self.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor() as cursor:
                await connection.begin()
                try:
                    await cursor.executemany(
                        "INSERT INTO myvector_documents "
                        "(node_id,model_name,dimensions,ref_doc_id,source_platform,status,metadata,embedding) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,myvector_construct(%s)) AS incoming "
                        "ON DUPLICATE KEY UPDATE dimensions=incoming.dimensions,ref_doc_id=incoming.ref_doc_id,"
                        "source_platform=incoming.source_platform,status=incoming.status,"
                        "metadata=incoming.metadata,embedding=incoming.embedding", values,
                    )
                    await connection.commit()
                except BaseException:
                    await connection.rollback()
                    raise
        return [node.node_id for node in nodes]

    async def aquery(self, query: VectorStoreQuery, **kwargs) -> VectorStoreQueryResult:
        async with self._operation_lock:
            return await self._query(query)

    async def _query(self, query: VectorStoreQuery) -> VectorStoreQueryResult:
        if query.mode != VectorStoreQueryMode.DEFAULT or query.query_embedding is None:
            raise ValueError("当前仅支持向量检索")
        if bool(query.node_ids) or bool(query.doc_ids) or query.sparse_top_k is not None:
            raise ValueError("当前不支持节点、文档或稀疏检索参数")
        validate_vector(query.query_embedding, self.dimensions)
        if not 1 <= query.similarity_top_k <= 100:
            raise ValueError("top_k 必须为 1 至 100")
        conditions = ["model_name=%s", "dimensions=%s"]
        params = [self.model_name, self.dimensions]
        if query.filters is not None:
            if query.filters.condition != FilterCondition.AND:
                raise ValueError("当前过滤仅支持 AND")
            for item in query.filters.filters:
                if not isinstance(item, MetadataFilter) or item.key not in {"source_platform", "status"} or item.operator != FilterOperator.EQ:
                    raise ValueError("当前仅支持 source_platform、status 的相等过滤")
                conditions.append(f"{item.key}=%s")
                params.append(item.value)
        async with asyncio.timeout(self.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                if self.search_mode == "hnsw" and self._index_dirty:
                    await cursor.execute("SELECT COUNT(*) AS n FROM myvector_documents")
                    if not (await cursor.fetchone())["n"]:
                        return VectorStoreQueryResult(nodes=[], ids=[], similarities=[])
                    await self._rebuild_index(cursor)
                vector = json.dumps(query.query_embedding, allow_nan=False)
                if self.search_mode == "exact":
                    rows = await self._exact_query(cursor, conditions, params, vector, query.similarity_top_k)
                else:
                    rows = await self._hnsw_query(cursor, conditions, params, vector, query.similarity_top_k)
        return VectorStoreQueryResult(
            nodes=[metadata_dict_to_node(json.loads(row["metadata"])) for row in rows],
            ids=[row["node_id"] for row in rows],
            similarities=[max(-1.0, min(1.0, 1.0 - row["distance"])) for row in rows],
        )

    async def _rebuild_index(self, cursor) -> None:
        # 固定镜像没有在线更新保证，写入提交后在下一次查询前重建，防止使用旧向量。
        await cursor.execute(
            "SELECT COLUMN_COMMENT AS options FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='myvector_documents' AND COLUMN_NAME='embedding'"
        )
        definition = await cursor.fetchone()
        options = definition["options"] if definition else ""
        dimension = re.search(r"\bdim=(\d+)", options)
        capacity = re.search(r"\bsize=(\d+)", options)
        if not dimension or int(dimension[1]) != self.dimensions or not capacity:
            raise ValueError("MyVector 字段配置的维度或容量不符合要求")
        await cursor.execute("SELECT COUNT(*) AS n FROM myvector_documents")
        if (await cursor.fetchone())["n"] > int(capacity[1]):
            raise ValueError("MyVector 索引缓存超过字段配置容量，需先扩容")
        await cursor.execute("SELECT COUNT(*) AS n FROM myvector_documents WHERE dimensions <> %s", (self.dimensions,))
        if (await cursor.fetchone())["n"]:
            raise ValueError("一个 HNSW 索引不能包含不同维度的向量")
        await cursor.execute("SELECT DATABASE() AS name")
        index = (await cursor.fetchone())["name"] + ".myvector_documents.embedding"
        await cursor.execute("CALL mysql.myvector_index_build(%s,'id')", (index,))
        result = await cursor.fetchall()
        while await cursor.nextset():
            pass
        if not any("SUCCESS" in str(row) for row in result):
            raise RuntimeError("MyVector 索引重建失败，请核对插件内部连接与索引目录配置")
        await cursor.execute("CALL mysql.myvector_index_status(%s)", (index,))
        status = "\n".join(str(value) for row in await cursor.fetchall() for value in row.values())
        while await cursor.nextset():
            pass
        if "Type : HNSW" not in status or f"Dimension : {self.dimensions}\n" not in status or "Distance : Cosine" not in status:
            raise ValueError("MyVector 索引必须为相同维度的 HNSW/Cosine 索引")
        self._index_dirty = False

    async def rebuild_index(self) -> None:
        async with self._operation_lock, asyncio.timeout(self.operation_timeout_seconds):
            self._index_dirty = True
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await self._rebuild_index(cursor)

    async def _exact_query(self, cursor, conditions, params, vector, top_k):
        await cursor.execute(
            "SELECT node_id,metadata,myvector_distance(embedding,myvector_construct(%s),'Cosine') AS distance "
            "FROM myvector_documents WHERE " + " AND ".join(conditions) +
            " ORDER BY distance,node_id LIMIT %s", [vector, *params, top_k],
        )
        return await cursor.fetchall()

    async def _hnsw_query(self, cursor, conditions, params, vector, top_k):
        await cursor.execute("SELECT DATABASE() AS name")
        index = (await cursor.fetchone())["name"] + ".myvector_documents.embedding"
        await cursor.execute("SELECT COUNT(*) AS n FROM myvector_documents")
        total = (await cursor.fetchone())["n"]
        if not total:
            return []
        candidate_count = min(total, max(100, top_k * 10), self.candidate_limit)
        while True:
            # 同一 SQL 中完成 ANN 与距离读取；插件距离缓存属于当前数据库连接。
            # 使用明确的 nn 字符串和四参数 UDF，绕过旧镜像不支持的五参数改写。
            await cursor.execute(
                "SELECT node_id,metadata,myvector_row_distance(id) AS distance "
                "FROM myvector_documents WHERE " + " AND ".join(conditions) +
                " AND id IN (SELECT candidate_id FROM "
                "(SELECT myvector_ann_set(%s,'id',myvector_construct(%s),%s) AS candidates) AS result, "
                "JSON_TABLE(result.candidates,'$[*]' COLUMNS(candidate_id BIGINT UNSIGNED PATH '$')) AS ids) "
                "ORDER BY distance,node_id LIMIT %s",
                [*params, index, vector, f"nn={candidate_count}", top_k],
            )
            rows = await cursor.fetchall()
            if len(rows) >= top_k or candidate_count >= total:
                return rows
            if candidate_count >= self.candidate_limit:
                # 强筛选达到插件候选上限时精确兜底，不能把符合条件的资源静默遗漏。
                return await self._exact_query(cursor, conditions, params, vector, top_k)
            candidate_count = min(total, candidate_count * 2, self.candidate_limit)

    async def adelete(self, ref_doc_id: str, **kwargs) -> None:
        async with self._operation_lock, asyncio.timeout(self.operation_timeout_seconds):
            self._index_dirty = True
            async with self._pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM myvector_documents WHERE model_name=%s AND ref_doc_id=%s",
                    (self.model_name, ref_doc_id),
                )

    async def adelete_platform(self, source_platform: str) -> None:
        async with self._operation_lock, asyncio.timeout(self.operation_timeout_seconds):
            self._index_dirty = True
            async with self._pool.acquire() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM myvector_documents WHERE model_name=%s AND source_platform=%s",
                    (self.model_name, source_platform),
                )

    async def adelete_status(self, status: str) -> None:
        async with self._operation_lock, asyncio.timeout(self.operation_timeout_seconds):
            self._index_dirty = True
            async with self._pool.acquire() as connection, connection.cursor() as cursor:
                # 全平台快照仅清理当前模型的正式资源缓存，保留其他模型或状态。
                await cursor.execute(
                    "DELETE FROM myvector_documents WHERE model_name=%s AND status=%s",
                    (self.model_name, status),
                )
