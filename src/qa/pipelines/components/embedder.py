"""
嵌入工具函数 — 管理远程 API 和本地模型的自动切换

策略:
  auto  → 先试远程 API，失败则 fallback 到本地模型
  api   → 仅使用远程 API，失败则报错
  local → 仅使用本地模型（jina-embeddings-v5-text-nano）
"""

from __future__ import annotations

import logging

from qa.config.settings import get_settings

logger = logging.getLogger(__name__)

# 本地 embedder 全局单例（模型只加载一次）
_local_embedder = None


def get_local_embedder():
    """获取本地嵌入模型（延迟加载）"""
    global _local_embedder
    if _local_embedder is None:
        from qa.pipelines.components.local_embedder import LocalEmbedder
        _local_embedder = LocalEmbedder()
    return _local_embedder


def create_remote_client():
    """创建远程 OpenAI 兼容客户端"""
    settings = get_settings()
    from openai import OpenAI
    return OpenAI(
        api_key=settings.embedding.resolved_api_key or settings.llm.resolved_api_key or "",
        base_url=settings.embedding.api_base_url,
        timeout=10,  # 短超时用于快速检测
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """嵌入文本列表（自动选择后端）

    策略:
      local → 仅本地
      api   → 仅远程
      auto  → 先远程，失败则本地
    """
    settings = get_settings()
    backend = settings.embedding.backend

    if backend == "local":
        logger.info("使用本地嵌入模型 (jina-embeddings-v5-text-nano)")
        return _embed_local(texts)

    if backend == "api":
        logger.info("使用远程 API 嵌入")
        return _embed_remote(texts)

    # auto: 先试远程
    try:
        return _embed_remote(texts)
    except Exception as e:
        logger.warning(f"远程嵌入失败 ({e})，回退到本地模型")
        try:
            return _embed_local(texts)
        except Exception as local_e:
            raise RuntimeError(
                f"远程和本地嵌入均不可用。远程: {e}, 本地: {local_e}"
            )


def embed_query(text: str) -> list[float]:
    """嵌入单个查询"""
    return embed_texts([text])[0]


def _embed_remote(texts: list[str]) -> list[list[float]]:
    """使用远程 API 嵌入"""
    settings = get_settings()
    client = create_remote_client()
    resp = client.embeddings.create(
        model=settings.embedding.model,
        input=texts,
    )
    return [d.embedding for d in resp.data]


def _embed_local(texts: list[str]) -> list[list[float]]:
    """使用本地模型嵌入"""
    embedder = get_local_embedder()
    return embedder.encode(texts)
