# Deployment Guide

Securities QA Agent 部署指南。

## 目录

- [快速开始 (Docker)](#快速开始-docker)
- [手动部署](#手动部署)
- [环境变量](#环境变量)
- [生产环境检查清单](#生产环境检查清单)
- [故障排查](#故障排查)

---

## 快速开始 (Docker)

### 前置条件

- Docker 20.10+
- Docker Compose 2.0+
- LLM API 端点（OpenAI 兼容）

### 启动

```bash
# 1. 克隆代码
git clone <repo-url>
cd qsMaster

# 2. 配置环境变量
cp .env.example .env
vim .env  # 编辑填入实际的 API 地址和密钥

# 3. 启动服务
docker compose up -d

# 4. 检查健康状态
curl http://localhost:8001/api/v1/qa/health

# 5. 打开前端
open http://localhost:8001
```

### 常用命令

```bash
# 查看日志
docker compose logs -f qa-agent

# 停止服务
docker compose down

# 重建镜像
docker compose up -d --build

# 进入容器
docker compose exec qa-agent bash
```

---

## 手动部署

### 前置条件

- Python 3.11+
- pip / uv

### 安装

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装项目
pip install -e .

# 4. 复制配置文件
cp config.example.yaml ~/.qa/config.yaml
vim ~/.qa/config.yaml  # 编辑配置
```

### 启动服务

```bash
# 方式 1: 直接启动
uvicorn qa.api.server:app --host 0.0.0.0 --port 8001

# 方式 2: 使用 systemd (Linux)
sudo systemctl start qa-agent
sudo systemctl enable qa-agent

# 方式 3: 使用 supervisor
supervisorctl start qa-agent
```

---

## 环境变量

所有配置项都可通过环境变量覆盖，前缀为 `QA_`。

### LLM 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QA_LLM_API_BASE_URL` | LLM API 地址 | `http://internal-llm:8000/v1` |
| `QA_LLM_API_KEY` | LLM API 密钥 | (无) |
| `QA_LLM_MODEL` | LLM 模型名 | `gpt-4` |
| `QA_LLM_TIMEOUT_SECONDS` | 请求超时(秒) | `30` |

### Embedding 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QA_EMBEDDING_API_BASE_URL` | Embedding API 地址 | `http://internal-llm:8000/v1` |
| `QA_EMBEDDING_API_KEY` | Embedding API 密钥 | (无) |
| `QA_EMBEDDING_MODEL` | Embedding 模型名 | `bge-m3` |

### Server 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QA_SERVER_HOST` | 监听地址 | `127.0.0.1` |
| `QA_SERVER_PORT` | 监听端口 | `8001` |
| `QA_SERVER_API_KEYS` | API 密钥列表(逗号分隔) | (空=不鉴权) |
| `QA_SERVER_RATE_LIMIT_RPM` | 每分钟请求限制(per key) | `60` |
| `QA_SERVER_ALLOWED_ORIGINS` | CORS 允许来源(逗号分隔) | `http://localhost:8001` |

### 示例 .env 文件

```bash
# LLM
QA_LLM_API_BASE_URL=http://your-llm:8000/v1
QA_LLM_API_KEY=sk-your-key-here
QA_LLM_MODEL=gpt-4

# Embedding
QA_EMBEDDING_API_BASE_URL=http://your-llm:8000/v1
QA_EMBEDDING_API_KEY=sk-your-key-here
QA_EMBEDDING_MODEL=bge-m3

# Server
QA_SERVER_HOST=0.0.0.0
QA_SERVER_PORT=8001
QA_SERVER_API_KEYS=sk-api-key-1,sk-api-key-2
QA_SERVER_RATE_LIMIT_RPM=100
QA_SERVER_ALLOWED_ORIGINS=https://your-domain.com,http://localhost:8001
```

---

## 生产环境检查清单

部署到生产环境前，请确认以下事项：

### 安全

- [ ] **API 密钥已配置**: `QA_SERVER_API_KEYS` 非空
- [ ] **CORS 已限制**: `QA_SERVER_ALLOWED_ORIGINS` 不包含 `*`
- [ ] **LLM 密钥安全**: 使用环境变量，不硬编码
- [ ] **HTTPS 已启用**: 通过反向代理 (nginx/caddy) 配置 TLS
- [ ] **非 root 运行**: Docker 镜像默认非 root 用户

### 性能

- [ ] **限流已配置**: `QA_SERVER_RATE_LIMIT_RPM` 根据容量设置
- [ ] **LLM 超时合理**: `QA_LLM_TIMEOUT_SECONDS` 30-60 秒
- [ ] **数据持久化**: Docker volume 或挂载目录已配置

### 监控

- [ ] **健康检查**: `/api/v1/qa/health` 可访问
- [ ] **日志收集**: stdout/stderr 已接入日志系统
- [ ] **指标暴露**: `/api/v1/qa/metrics` 可访问（可选接入 Prometheus）

### 备份

- [ ] **数据备份**: `./data/` 目录定期备份
- [ ] **配置备份**: `config.yaml` 版本控制

---

## 故障排查

### 服务无法启动

```bash
# 检查日志
docker compose logs qa-agent

# 常见原因:
# - LLM API 不可达
# - 端口被占用
# - 配置文件语法错误
```

### LLM 调用失败

```bash
# 测试 LLM 连通性
curl http://your-llm:8000/v1/models

# 检查 API key
echo $QA_LLM_API_KEY
```

### 索引构建慢

- 检查 OCR 是否启用但无必要: `indexing.ocr_enabled: false`
- 调整批量大小: `indexing.batch_size: 200`
- 检查文档超时: `indexing.doc_timeout_seconds: 120`

### 内存占用高

- 减少 BM25 索引大小: 考虑使用 PostgreSQL 全文检索 (`pg.enabled: true`)
- 调整向量维度: `embedding.dimensions: 512` (降低精度换内存)
- 启用 2-bit 量化: `vector_store.bit_width: 2`

---

## 反向代理配置

### Nginx 示例

```nginx
server {
    listen 443 ssl http2;
    server_name qa.your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

### Caddy 示例

```
qa.your-domain.com {
    reverse_proxy localhost:8001
}
```
