"""
PostgreSQL 全文检索后端（FR-002）

使用 pg_trgm + tsvector 替代内存 BM25，支持中文模糊匹配。
PostgreSQL 不可用时自动降级到 BM25，不阻塞问答。

用法:
    retriever = PgFullTextRetriever(
        host="localhost", port=5432, dbname="qa",
        user="qa", password="***",
    )
    results = retriever.search("CCASS 交收", top_k=10)
    # PG 不可用时返回 None，调用方回退到 BM25
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PgFullTextRetriever:
    """PostgreSQL 全文检索器

    使用 tsvector 做关键词匹配 + pg_trgm 做模糊匹配。
    连接失败/查询异常时自动返回 None（调用方回退到 BM25）。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        dbname: str = "qa",
        user: str = "qa",
        password: str = "",
        min_conn: int = 1,
        max_conn: int = 5,
    ):
        self._config = {
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "password": password,
        }
        self._pool: Any = None
        self._available = False
        self._min_conn = min_conn
        self._max_conn = max_conn

    # ─── 连接管理 ──────────────────────────────────────

    def _init_pool(self) -> bool:
        """初始化连接池"""
        if self._pool is not None:
            return self._available
        try:
            from psycopg2 import pool

            self._pool = pool.ThreadedConnectionPool(self._min_conn, self._max_conn, **self._config)
            self._available = True
            self._ensure_table()
            logger.info(
                f"PostgreSQL 全文检索已连接: "
                f"{self._config['host']}:{self._config['port']}/{self._config['dbname']}"
            )
        except Exception as e:
            logger.warning(f"PostgreSQL 连接失败，将使用 BM25 降级: {e}")
            self._available = False
            self._pool = None
        return self._available

    def _get_conn(self) -> Any | None:
        """获取连接"""
        if not self._init_pool():
            return None
        try:
            return self._pool.getconn()
        except Exception as e:
            logger.error(f"获取 PG 连接失败: {e}")
            return None

    def _put_conn(self, conn: Any) -> None:
        """归还连接"""
        if conn and self._pool:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    # ─── 表结构 ──────────────────────────────────────

    def _ensure_table(self) -> None:
        """创建表 + 索引（如不存在）"""
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            # 启用 pg_trgm
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            # 文档表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS qa_documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    file_path TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    tsvector_col TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('simple', coalesce(content, ''))
                    ) STORED
                )
            """)
            # tsvector GIN 索引
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_qa_tsvector
                ON qa_documents USING GIN (tsvector_col)
            """)
            # pg_trgm GIN 索引（模糊匹配）
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_qa_trgm
                ON qa_documents USING GIN (content gin_trgm_ops)
            """)
            conn.commit()
            logger.debug("PG 全文检索表结构已就绪")
        except Exception as e:
            logger.error(f"PG 表结构初始化失败: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    # ─── 索引 ──────────────────────────────────────

    def index_documents(self, docs: list[tuple[str, str, str, str, str]]) -> int:
        """批量索引文档

        Args:
            docs: [(id, content, file_path, source, category), ...]

        Returns:
            写入数，失败返回 0
        """
        if not self._init_pool():
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        try:
            cur = conn.cursor()
            count = 0
            for doc_id, content, file_path, source, category in docs:
                cur.execute(
                    """
                    INSERT INTO qa_documents (id, content, file_path, source, category)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        file_path = EXCLUDED.file_path
                """,
                    (doc_id, content, file_path, source, category),
                )
                count += 1
            conn.commit()
            logger.debug(f"PG 索引完成: {count} 文档")
            return count
        except Exception as e:
            logger.error(f"PG 索引失败: {e}")
            conn.rollback()
            return 0
        finally:
            self._put_conn(conn)

    def delete_documents(self, ids: list[str]) -> int:
        """删除文档"""
        if not self._init_pool():
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM qa_documents WHERE id = ANY(%s)", (ids,))
            conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.error(f"PG 删除失败: {e}")
            conn.rollback()
            return 0
        finally:
            self._put_conn(conn)

    # ─── 检索

    def search(
        self,
        query_text: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]] | None:
        """全文检索

        先用 tsquery 精确匹配，再用 pg_trgm 模糊匹配补充。
        低于 top_k 时只返回匹配到的结果。

        Returns:
            [{"id", "content", "file_path", "score"}, ...]
            失败/不可用时返回 None（调用方降级到 BM25）
        """
        if not self._init_pool():
            return None
        conn = self._get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor()
            # tsquery: 对查询文本做简单分词（中文 2-gram + 英文词）
            query_tokens = self._tokenize_query(query_text)
            if not query_tokens:
                return []
            tsquery = " & ".join(query_tokens)

            # tsvector 精确排名 + pg_trgm 模糊相似度加权
            # 使用 tsquery（中文 2-gram 分词）替代 plainto_tsquery（仅英文词干）
            sql = """
                SELECT id, content, file_path,
                    ts_rank(tsvector_col, to_tsquery('simple', %s)) * 0.7
                    + similarity(content, %s) * 0.3 AS score
                FROM qa_documents
                WHERE tsvector_col @@ to_tsquery('simple', %s)
                   OR content %% %s
                ORDER BY score DESC
                LIMIT %s
            """
            cur.execute(sql, (tsquery, query_text, tsquery, query_text, top_k))
            rows = cur.fetchall()

            results = []
            for row in rows:
                doc_id, content, file_path, score = row
                results.append(
                    {
                        "id": doc_id,
                        "content": content,
                        "file_path": file_path or "",
                        "score": round(float(score), 4),
                    }
                )
            return results

        except Exception as e:
            logger.error(f"PG 检索失败（将降级到 BM25）: {e}")
            return None
        finally:
            self._put_conn(conn)

    def count(self) -> int:
        """文档总数"""
        if not self._init_pool():
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM qa_documents")
            return cur.fetchone()[0]
        except Exception:
            return 0
        finally:
            self._put_conn(conn)

    def clear(self) -> int:
        """清空所有文档"""
        if not self._init_pool():
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM qa_documents")
            conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.error(f"PG 清空失败: {e}")
            conn.rollback()
            return 0
        finally:
            self._put_conn(conn)

    # ─── 工具 ──────────────────────────────────────

    @staticmethod
    def _tokenize_query(text: str) -> list[str]:
        """简单中文/英文分词（用于 tsquery）"""
        import re

        tokens = []
        # 提取英文单词
        eng_words = re.findall(r"[a-zA-Z0-9]+", text)
        tokens.extend(w.lower() for w in eng_words)
        # 提取中文（按字符 + 2-gram）
        chinese_chars = re.findall(r"[\u4e00-\u9fff]+", text)
        for chunk in chinese_chars:
            # 单个中文字符作为一个 token
            tokens.extend(list(chunk))
            # 2-gram 提升召回
            tokens.extend([chunk[i : i + 2] for i in range(len(chunk) - 1)])
        # 去重 + 限制长度
        unique = list(dict.fromkeys(tokens))
        return [t for t in unique if len(t) <= 50][:20]  # 最多 20 个 token

    def close(self) -> None:
        """关闭连接池"""
        if self._pool:
            try:
                self._pool.closeall()
            except Exception:
                pass
            self._pool = None
            self._available = False
