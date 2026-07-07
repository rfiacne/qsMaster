"""
本地嵌入模型后端 — 使用 Jina Embedding Nano 作为本地 fallback

当外部 Embedding API 不可用或配置为 local 时自动启用。
首次使用会自动下载模型（约 500MB），后续缓存到本地。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class LocalEmbedder:
    """本地嵌入模型

    封装 sentence-transformers，默认使用 jina-embeddings-v5-text-nano。
    首次调用时自动下载模型，后续从缓存加载。
    """

    def __init__(  # noqa: E501
        self, model_name: str = "jinaai/jina-embeddings-v5-text-nano", task: str = "retrieval"
    ):
        self.model_name = model_name
        self._task = task
        self._model = None
        self._dimension = 768  # jina-embeddings-v5-text-nano 的维度
        self._encode_lock = threading.Lock()

    def _lazy_load(self):
        """延迟加载模型（首次调用时下载）"""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"正在加载本地嵌入模型: {self.model_name}")
            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True,
                model_kwargs={"default_task": self._task},
            )
            logger.info(f"本地嵌入模型加载完成: {self.model_name} (维度={self._dimension})")
        except ImportError:
            raise ImportError(
                "sentence-transformers 未安装。请执行:\n"
                "  pip install sentence-transformers torch\n"
                "或者配置 embedding.api_base_url 使用远程 API"
            )
        except Exception as e:
            raise RuntimeError(
                f"本地嵌入模型加载失败 ({self.model_name}): {e}\n"
                f"请检查网络连接，或设置环境变量 HF_ENDPOINT=https://hf-mirror.com 使用国内镜像\n"
                f"或配置 embedding.api_base_url 使用远程 API"
            )

    def encode(self, texts: list[str], task: str = "retrieval") -> list[list[float]]:
        """将文本列表转为嵌入向量

        线程安全：SentenceTransformer 的 encode() 方法对同一模型的并发调用
        可能导致崩溃或静默错误结果，故用 threading.Lock 保护。

        Args:
            texts: 文本列表
            task: jina v5 任务类型 (retrieval, text-matching, clustering, classification)
        """
        self._lazy_load()
        with self._encode_lock:
            embeddings = self._model.encode(texts, task=task, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]

    def encode_query(self, text: str) -> list[float]:
        """将单个查询文本转为嵌入向量"""
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension
