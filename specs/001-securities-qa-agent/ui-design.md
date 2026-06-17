# 前端 UI 设计文档：Securities QA Agent

**关联 Spec**: [M1 Spec](../spec.md) · [M5 Spec](../../005-web-frontend-api-gateway/spec.md)

**创建日期**: 2026-06-09

**状态**: Draft

---

## 1. 设计定位

### 1.1 目标用户

证券行业从业人员（交易员、清算专员、合规人员），具备基础领域知识但非技术背景。

### 1.2 使用场景

| 角色 | 场景 | 核心操作 |
|------|------|----------|
| 普通用户 | 日常知识问答 | 输入问题、查看回答、查看引用来源、追问 |
| 管理员 | 知识库维护 | 上传文档、查看索引状态、删除文档、配置系统 |
| 业务专家 | 知识审核（M3+） | 审核答案、标注标准问答对 |

### 1.3 设计原则

1. **简洁优先**：核心功能（问答）首屏可达，无冗余操作
2. **引用透明**：每条回答必须标注来源，可展开查看原文
3. **反馈即时**：打字机效果展示 LLM 生成过程
4. **渐进增强**：M1 聚焦问答闭环，管理功能随后补充

---

## 2. 整体布局

```
┌─────────────────────────────────────────────────┐
│  Logo          证券清算知识问答系统     用户信息   │  ← 顶栏
├──────────────────────┬──────────────────────────┤
│                      │                          │
│  📁 知识库管理        │   问答主区域              │
│  ├─ 文档列表          │   ┌──────────────────┐  │
│  ├─ 上传文档          │   │ 💬 历史消息流     │  │
│  ├─ 索引状态          │   │                  │  │
│  └─ 统计概览          │   │ 用户: T+1 清算?  │  │
│                      │   │                  │  │
│  📋 标准答案 (M3+)    │   │ AI: T+1 是指...  │  │
│  ├─ 答案管理          │   │ [来源:规则.pdf]   │  │
│  └─ 审核队列          │   │                  │  │
│                      │   └──────────────────┘  │
│  📊 统计 (M5)         │   ┌──────────────────┐  │
│  ├─ 问答趋势          │   │ 输入框 [提问]     │  │
│  └─ 热门问题          │   └──────────────────┘  │
│                      │                          │
├──────────────────────┴──────────────────────────┤
│  页脚                                             │
└─────────────────────────────────────────────────┘
```

### 2.1 布局方案

| 方案 | 适用阶段 | 说明 |
|------|----------|------|
| **全宽单栏** | M1 MVP | 问答主区域占满全屏，管理功能通过右上角下拉菜单或模态框访问 |
| **左侧边栏 + 主区域** | M5 完整版 | 左侧导航提供知识库管理、标准答案、统计等功能入口 |

---

## 3. 页面设计

### 3.1 问答页面（核心）

**路径**: `/` 或 `/chat`

**功能**:
- 消息列表（历史问答记录流式展示）
- 文本输入框 + 发送按钮
- 引用来源折叠展示
- 空状态引导文案

**交互流程**:

```
用户输入问题 → Enter / 点击发送
    │
    ├─ 问题气泡出现在消息列表
    │
    ├─ 显示"正在检索..." 状态指示器
    │
    ├─ LLM 开始生成（打字机效果逐字输出）
    │   └─ 过程中引用标注逐步出现 [来源:N]
    │
    ├─ 生成完成
    │   ├─ 回答完整展示
    │   └─ 引用来源列表（可折叠/展开）
    │
    └─ 用户可继续追问（上下文保留）
```

**空状态**:

```
┌──────────────────────────────────┐
│                                  │
│    💡                          │
│    证券清算知识问答助手          │
│                                  │
│    您可以这样开始：              │
│    • "沪深交易所 T+1 清算流程"   │
│    • "银行间债券市场清算方式"    │
│    • "CSDC 的职责是什么"         │
│                                  │
│    输入问题开始问答...           │
│                                  │
└──────────────────────────────────┘
```

### 3.2 引用来源渲染

**行内标注方案**:
```
中国证券登记结算有限公司（CSDC）负责证券的集中统一登记与结算[1]。
T+1 交收制度下，交易日次一交易日完成资金交收[2]。

[1] 来源: CSDC业务规则.pdf · 第3章第2节
[2] 来源: 沪深交易所清算流程手册.md · 第5页
```

**交互**:
- 点击 `[1]` 标注 → 弹出浮层显示原文片段
- 浮层包含：文件名、段落位置、原文内容、相似度分数
- 点击浮层外区域关闭

### 3.3 错误状态

| 状态 | 用户看到的提示 | 操作建议 |
|------|---------------|----------|
| 知识库为空 | "知识库尚未建立，请联系管理员导入文档" | 展示管理员联系方式 |
| LLM 不可用 | "服务暂时不可用，请稍后重试" | 自动重试按钮（3 次后禁用） |
| 检索无结果 | "未找到相关信息，请尝试换个问法" | 显示相关建议问题 |
| 网络中断 | "连接已断开，请检查网络后继续" | 自动重连指示器 |
| 超时 | "请求超时，请稍后重试" | 重试按钮 |

---

## 4. 组件设计

### 4.1 消息气泡

```html
<div class="message">
  <div class="message-role user">用户</div>
  <div class="message-content">沪深交易所 T+1 清算流程是什么？</div>
</div>

<div class="message">
  <div class="message-role assistant">助手</div>
  <div class="message-content streaming">
    <!-- 打字机效果输出的 Markdown 渲染 -->
  </div>
  <div class="message-sources">
    <div class="source-item" data-id="1">
      <span class="source-badge">[1]</span>
      <span class="source-title">CSDC业务规则.pdf</span>
      <span class="source-score">0.92</span>
    </div>
  </div>
</div>
```

### 4.2 引用浮层

```html
<div class="source-popover">
  <div class="source-popover-header">
    <strong>[1]</strong> CSDC业务规则.pdf
  </div>
  <div class="source-popover-body">
    <p>第 3 章第 2 节（第 45-50 页）</p>
    <blockquote>
      中国证券登记结算有限公司对证券交易实行...
    </blockquote>
    <div class="source-popover-meta">
      <span>来源: CSDC</span>
      <span>类别: clearing_rule</span>
      <span>相似度: 0.92</span>
    </div>
  </div>
</div>
```

### 4.3 输入区域

```html
<div class="input-area">
  <textarea
    class="question-input"
    placeholder="输入问题..."
    rows="1"
    data-autoresize
  ></textarea>
  <button class="send-btn" disabled>
    ▶ 发送
  </button>
  <div class="input-hints">
    <kbd>Enter</kbd> 发送 · <kbd>Shift+Enter</kbd> 换行
  </div>
</div>
```

### 4.4 状态指示器

```html
<div class="status-indicator">
  <span class="status-dot searching"></span>
  正在检索相关文档...
</div>

<div class="status-indicator">
  <span class="status-dot generating"></span>
  正在生成回答...
</div>
```

---

## 5. 技术选型

| 层级 | M1 MVP 方案 | M5 完整版方案 |
|------|-------------|--------------|
| **UI 框架** | 纯 HTML + CSS + JS（零依赖） | React + Ant Design |
| **后端通信** | 调用 CLI（subprocess）或直接 FastAPI | FastAPI REST + SSE |
| **流式展示** | EventSource (SSE) 或轮询 | Server-Sent Events |
| **Markdown 渲染** | marked.js (CDN) | react-markdown |
| **样式** | CSS Variables + Flexbox | Ant Design + Tailwind |
| **存储** | 无（无状态前端） | Redis 会话缓存 |

### 5.1 M1 轻量前端策略

M1 阶段提供一个 `index.html` 单文件前端，通过 fetch API 调用 Python 后端的 HTTP 接口：

```
浏览器 (index.html)
    │  fetch /api/v1/qa/ask  (POST)
    ▼
FastAPI / Flask 轻量服务
    │  调用 qa.pipelines.querying.QueryPipeline
    ▼
Python QA Agent Core
    │  turbovec + Haystack + LLM
    ▼
返回 JSON 响应 → 前端渲染
```

---

## 6. 响应式设计

### 6.1 断点

| 断点 | 宽度 | 布局 |
|------|------|------|
| 手机 | < 768px | 全宽单栏，输入框置底固定 |
| 平板 | 768-1024px | 全宽单栏，输入框置底，侧栏可滑动 |
| 桌面 | > 1024px | 左侧边栏 + 主区域 |

### 6.2 适配要点

- 输入框在移动端固定底部（`position: sticky; bottom: 0`）
- 引用浮层在移动端改为全屏底部面板
- 侧栏在移动端通过汉堡菜单切换

---

## 7. 无障碍设计

- 所有图标元素提供 `aria-label`
- 颜色对比度 ≥ 4.5:1（WCAG AA）
- 键盘导航支持（Tab/Enter/Escape）
- 动态内容更新使用 `aria-live="polite"`
- 打字机效果不阻止屏幕阅读器

---

## 8. 后续扩展（M5+）

| 组件 | 阶段 | 说明 |
|------|------|------|
| 文档管理页 | M5 | 上传/删除/批量操作文档 |
| 标准答案管理 | M5 | CRUD 标准问答对，别名管理 |
| 审核队列 | M5 | 审核候选答案，标注确认/驳回 |
| 统计看板 | M5 | 问答趋势图、准确率、热门问题 |
| 用户管理 | M5 | 登录/角色/权限 |
| API 文档 | M5 | OpenAPI/Swagger UI |

---

## 9. 前端 HTML 文件

前端实现位于 `frontend/index.html`，为一个完整的单页应用：

- 零外部运行时依赖（CSS 内联，JS 内联）
- 支持打字机效果流式展示
- 引用来源折叠 + 浮层展示
- 多轮对话上下文保留
- 完整的空状态/错误状态/加载状态
- 响应式布局适配桌面和移动端

详见 [frontend/index.html](../../frontend/index.html) 的完整实现。
