# 证券问答系统架构审查报告

**审查日期**: 2026-06-29  
**审查工具**: ccc (代码索引) + 手动代码审查  
**项目规模**: 52 源文件 | 37 测试文件 | 1558 代码块 | 299 测试用例

---

## 一、项目概况

### 1.1 系统定位
基于 Haystack 2.x + turbovec 的证券领域 RAG 问答系统，覆盖 M1-M6 全链路：
- **导入** → **混合检索** → **Early Exit** → **LLM 生成** → **防幻觉校验** → **人工审核** → **审计留痕** → **Web 管理**

### 1.2 技术栈
- **后端**: FastAPI + Uvicorn (21 个 REST 端点)
- **检索**: 混合检索 (BM25 + 向量 RRF 融合) + Cross-encoder Reranker
- **生成**: OpenAI 兼容 API (GPT-4 / Qwen 系列)
- **存储**: turbovec (量化向量库) + JSON/JSONL (标准答案/审核队列/审计日志)
- **可观测性**: OpenTelemetry + InMemoryMetrics

### 1.3 核心指标
| 指标 | 数值 |
|------|------|
| 源文件数 | 52 |
| 测试文件数 | 37 |
| 测试用例数 | 299 (266 单元 + 33 集成) |
| 代码块数 (ccc) | 1558 |
| API 端点数 | 21 |
| CLI 命令数 | 11 |

---

## 二、关键发现

### 🔴 P0 级问题（成本炸弹 / 稳定性风险）

#### 2.1 LLM 调用无熔断器与成本上限
**位置**: `src/qa/pipelines/querying.py` (L350-367)

**问题描述**:
- 每次问答可能触发 **4 次 LLM 调用**:
  1. 查询改写 (多意图分解)
  2. 主回答生成
  3. Faithfulness 校验 (LLM-as-judge)
  4. 审核入队 (如有)
- **无熔断器**: API 故障时无自动降级，可能导致级联失败
- **无成本上限**: 恶意请求或配置错误可能导致无限消耗

**风险量化**:
- 单次问答最大 token 消耗: ~15,000 (假设 GPT-4)
- 按 $0.03/1K token 计算: 单次问答成本 ~$0.45
- 1000 次问答/天: **$450/天 ≈ $13,500/月**

**建议修复**:
```python
# 在 querying.py 中添加熔断器
class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = None
        self.state = 'closed'  # closed, open, half-open
    
    def call(self, func, *args, **kwargs):
        if self.state == 'open':
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = 'half-open'
            else:
                raise CircuitBreakerOpen("熔断器开启，拒绝请求")
        
        try:
            result = func(*args, **kwargs)
            if self.state == 'half-open':
                self.state = 'closed'
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = 'open'
            raise
```

#### 2.2 Faithfulness 校验成本失控
**位置**: `src/qa/pipelines/components/faithfulness.py`

**问题描述**:
- 每次校验调用 LLM，max_tokens=2048
- 无缓存机制，相同回答重复校验
- 低风险场景（Reranker 高分）仍强制校验

**成本分析**:
- 假设平均 10 条声明，每条 ~200 token → ~2000 input + 1000 output
- 校验成本 ≈ 主生成成本的 **30-50%**
- 高频场景下成为主要成本项

**建议修复**:
1. 对 Reranker 分数 ≥ 0.95 的回答跳过校验（已实现，见 `_is_low_risk_context`）
2. 添加回答级缓存（hash(answer) → faithfulness_result）
3. 使用更便宜的模型（如 GPT-3.5-Turbo）进行校验

#### 2.3 查询改写可能触发多次嵌入与检索
**位置**: `src/qa/pipelines/querying.py` (L248-273)

**问题描述**:
- 多意图分解时，每个子问题独立嵌入 + 检索
- 3 个子问题 → 3 次嵌入 API + 3 次检索 → 成本 ×3
- 无并发控制，可能压垮嵌入服务

**建议修复**:
```python
# 使用 ThreadPoolExecutor 并发嵌入（带超时）
from concurrent.futures import ThreadPoolExecutor, TimeoutError

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(self._embed_query, sq) for sq in sub_questions]
    embeddings = []
    for future in futures:
        try:
            embeddings.append(future.result(timeout=5))  # 5s 超时
        except TimeoutError:
            logger.warning("子问题嵌入超时，跳过")
```

---

### 🟡 P1 级问题（架构缺陷）

#### 2.4 全局单例线程安全问题
**位置**: 多处全局变量

**问题描述**:
```python
# src/qa/config/settings.py
_settings: Settings | None = None

# src/qa/pipelines/components/tracing.py
_tracer: Tracer | None = None
_metrics: InMemoryMetrics | None = None

# src/qa/api/dependencies.py
_store = None
_query_pipeline = None
_index_pipeline = None
_bm25_index = None
```

**风险**:
- 多线程并发初始化可能导致竞态条件
- 无锁保护的写入可能导致数据不一致
- FastAPI 异步环境下问题更严重

**建议修复**:
使用 `functools.lru_cache` 或依赖注入替代全局单例：
```python
from functools import lru_cache

@lru_cache()
def get_settings() -> Settings:
    return Settings.load()
```

#### 2.5 OpenAI 客户端重复创建
**位置**: `src/qa/pipelines/querying.py` (L670-676)

**问题描述**:
```python
# 每次流式请求都重建客户端
client = OpenAI(
    api_key=api_key,
    base_url=settings.llm.api_base_url,
    timeout=settings.llm.timeout_seconds,
)
```

**性能影响**:
- 客户端初始化耗时 ~50-100ms
- 连接池无法复用，TCP 握手开销累积
- 高并发下可能耗尽端口

**建议修复**:
```python
# 在 QueryPipeline 初始化时创建一次
class QueryPipeline:
    def __init__(self, ...):
        self._llm_client = None
    
    @property
    def llm_client(self):
        if self._llm_client is None:
            from openai import OpenAI
            settings = get_settings()
            self._llm_client = OpenAI(
                api_key=settings.llm.resolved_api_key,
                base_url=settings.llm.api_base_url,
                timeout=settings.llm.timeout_seconds,
            )
        return self._llm_client
```

#### 2.6 BM25 索引重建无锁保护
**位置**: `src/qa/pipelines/components/bm25_index.py`

**问题描述**:
- 文档索引后触发 BM25 重建
- 重建过程中如有查询请求，可能读取到不完整索引
- 无版本控制或原子切换

**建议修复**:
使用双缓冲（double buffering）:
```python
class BM25Index:
    def __init__(self):
        self._active_index = None
        self._building_index = None
        self._lock = threading.RLock()
    
    def rebuild_async(self, docs):
        # 在后台线程构建新索引
        new_index = self._build(docs)
        with self._lock:
            self._active_index = new_index  # 原子切换
```

---

### 🟢 P2 级问题（优化机会）

#### 2.7 可观测性缺少成本追踪
**位置**: `src/qa/pipelines/components/tracing.py`

**现状**:
- 有延迟、QPS、命中率指标
- **无 token 用量、成本、每问答成本追踪**

**建议新增**:
```python
class InMemoryMetrics:
    def __init__(self):
        # ... 现有字段
        self._token_usage = []
        self._cost_per_request = []
    
    def record_token_usage(self, model: str, input_tokens: int, output_tokens: int):
        # 根据模型价格计算成本
        cost = self._calculate_cost(model, input_tokens, output_tokens)
        self._token_usage.append({
            'model': model,
            'input': input_tokens,
            'output': output_tokens,
            'cost': cost,
            'timestamp': time.time()
        })
```

#### 2.8 缓存命中后仍执行 Faithfulness
**位置**: `src/qa/pipelines/querying.py` (L221-234)

**问题描述**:
- 查询缓存命中后直接返回，**跳过** Faithfulness
- 但缓存写入前未记录是否通过校验
- 可能导致未校验回答被重复使用

**建议修复**:
在缓存结果中记录 faithfulness 状态：
```python
@dataclass
class CachedResult:
    result: QueryResult
    faithfulness_checked: bool
    faithfulness_score: float
```

#### 2.9 无 A/B 测试框架
**现状**:
- 无法对比不同模型效果（GPT-4 vs Qwen）
- 无法测试新 prompt 模板
- 路由决策基于人工判断

**建议实现**:
```python
class ShadowTestRouter:
    """将 5% 流量路由到实验模型，异步对比效果"""
    
    def __init__(self, baseline_model, experiment_model, traffic_split=0.05):
        self.baseline = baseline_model
        self.experiment = experiment_model
        self.split = traffic_split
    
    async def route(self, question):
        if random.random() < self.split:
            # 影子测试：异步调用实验模型
            asyncio.create_task(self._shadow_test(question))
            return await self.baseline.generate(question)
        return await self.baseline.generate(question)
```

#### 2.10 前端静态文件挂载位置不当
**位置**: `src/qa/api/server.py` (L72-100)

**问题描述**:
- 前端文件挂载在 API 服务器中
- 前端更新需重启后端服务
- 无法独立部署和 CDN 加速

**建议修复**:
- 前端独立部署（Nginx / Vercel / CloudFront）
- 通过 API Gateway 统一入口
- 或使用 monorepo 工具（Turborepo）管理

---

## 三、架构优化路线图

### 短期（1-2 周）
1. **添加 LLM 熔断器** → 防止成本失控
2. **修复 OpenAI 客户端重复创建** → 性能提升 50-100ms/请求
3. **添加 token 用量追踪** → 成本可视化

### 中期（1-2 月）
4. **全局单例重构为依赖注入** → 线程安全
5. **BM25 双缓冲重建** → 零停机索引更新
6. **实现 A/B 测试框架** → 数据驱动模型选择

### 长期（3-6 月）
7. **前端独立部署** → 解耦前后端
8. **引入向量数据库（Milvus/Pinecone）** → 替代 turbovec，支持分布式
9. **实现自适应检索** → 根据问题复杂度动态调整 top_k

---

## 四、成本优化建议

### 4.1 模型分级策略

| 任务 | 推荐模型 | 预估成本/1K token |
|------|---------|------------------|
| 主回答生成 | GPT-4 / Qwen-Max | $0.03 |
| Faithfulness 校验 | GPT-3.5-Turbo | $0.002 |
| 查询改写 | GPT-3.5-Turbo | $0.002 |
| 嵌入 | bge-m3 (本地) | $0 (一次性) |

**预期节省**: 通过模型分级，成本降低 **60-70%**

### 4.2 缓存策略优化

```python
# 多级缓存
L1: 精确匹配缓存 (TTL=5min) → 命中率 ~20%
L2: 语义相似缓存 (embedding cosine > 0.95) → 命中率 ~15%
L3: 回答级缓存 (hash(answer)) → 命中率 ~10%

总命中率: ~45% → 成本降低 45%
```

### 4.3 批量处理优化

```python
# 批量嵌入（当前逐条调用）
# 优化前: 10 个子问题 → 10 次 API 调用
# 优化后: 10 个子问题 → 1 次批量调用（OpenAI 支持 max 2048 条）

response = client.embeddings.create(
    model="bge-m3",
    input=sub_questions  # 批量输入
)
```

**预期节省**: 嵌入成本降低 **80-90%**

---

## 五、稳定性建议

### 5.1 添加健康检查端点增强

```python
@app.get("/health/detailed")
async def detailed_health():
    return {
        "llm_api": await check_llm_api(),
        "embedding_api": await check_embedding_api(),
        "vector_store": await check_vector_store(),
        "bm25_index": check_bm25_index(),
        "disk_space": check_disk_space(),
        "memory_usage": check_memory_usage(),
    }
```

### 5.2 实现优雅降级

```python
# 当 Faithfulness 服务不可用时，跳过校验而非阻塞
try:
    report = await faithfulness_evaluator.evaluate(...)
except ServiceUnavailable:
    logger.warning("Faithfulness 服务不可用，跳过校验")
    report = FaithfulnessReport(result=SKIPPED)
```

### 5.3 添加请求限流

```python
from fastapi import HTTPException
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/v1/qa/ask")
@limiter.limit("60/minute")  # 每分钟 60 次
async def ask(request: Request, ...):
    ...
```

---

## 六、总结

### 当前架构评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完整性 | ⭐⭐⭐⭐⭐ | M1-M6 全部实现，功能丰富 |
| 代码质量 | ⭐⭐⭐⭐ | 结构清晰，测试覆盖良好 |
| 成本可控性 | ⭐⭐ | 缺少成本追踪和熔断机制 |
| 稳定性 | ⭐⭐⭐ | 基本异常处理到位，但缺少熔断和降级 |
| 可扩展性 | ⭐⭐⭐ | 组件化设计良好，但全局单例限制并发 |
| 可观测性 | ⭐⭐⭐ | 有 OTel 集成，但缺少成本维度 |

### 优先行动项

1. **立即**: 添加 LLM 熔断器和成本追踪（P0）
2. **本周**: 修复 OpenAI 客户端重复创建（P1）
3. **本月**: 重构全局单例为依赖注入（P1）
4. **下季度**: 实现 A/B 测试框架和模型分级（P2）

---

**报告生成**: 自主优化架构师  
**审查工具**: ccc (代码索引) + 手动代码审查  
**下次审查建议**: 实施优化后 3 个月进行复查
