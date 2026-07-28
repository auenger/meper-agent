# 向量型知识库（Vector KB）开发实施计划

**关联设计文档**：`docs/planning-artifacts/workflow-engine-upgrade.md` 第 5 节
**分支**：`feat/knowledge-base-vector`
**状态**：待评审

---

## 架构定调（基于全部已确认决策）

| 维度 | 决策 |
|---|---|
| 向量库 | **Qdrant**，自建 docker（加入 docker-compose） |
| 检索存储 | 平台级全局一个 embedding 模型 → Qdrant **单 collection** `kb_chunks` + `kb_id` payload 过滤；存 **dense+sparse 双向量** |
| 现有 tree 型 KB | **完全不动**，保留 `kb_glob/grep/read` 三工具，作为 agent 专属探索工具 |
| vector 型 KB | 检索工具 `kb_search`，agent 和 workflow 都可用 |
| 工具注入模型 | **固定数量**工具（tree 3个 + vector 1个）+ 可选 `kb_id` 参数（复用现有 `KbManager` 模式）；不追求跨类型统一抽象 |
| embedding | 外部模型（OpenAI 兼容），复用 `Model` 表 + 加 `task_type` 字段；全局唯一（`KB_EMBEDDING_MODEL_ID`） |
| reranker | 外部 cross-encoder API（`KB_RERANKER_MODEL_ID`），**可选**，未配置则降级跳过 |
| chunk 参数 | 平台级固定默认值（800/100 token），KB 创建时不暴露 |
| 检索流程 | **混合召回**（dense+sparse，RRF 融合）→ **Rerank 精排** → 阈值过滤 |
| 文档清洗 | MVP 做（去页眉页脚/水印/冗余空行），提升切片和检索质量 |
| 检索范围 | 不自动合并跨库；agent 绑定多 KB 后，把每个 KB 描述注入工具，由 LLM 自主选择调用哪个（传 `kb_id`） |
| 文档格式 | 支持 PDF / Word / Markdown / TXT，单文件 ≤ 50MB |
| 原始文件存储 | 复用 `FileRef` + `LocalFileStorage`（文件一等公民设计） |

### 两类 KB 能力边界（核心）

| | tree 型 | vector 型 |
|---|---|---|
| 存取 | 文件系统 `.md` 目录树 | Qdrant 向量 + Mongo 文档元数据 |
| Agent 工具 | `kb_glob` / `kb_grep` / `kb_read`（探索式，保留现有） | `kb_search`（语义检索，新增） |
| Workflow 节点 | ❌ 不可用 | ✅ `kb_search` 节点（新增） |
| 适用场景 | 结构化文档、需 agent 自主浏览结构/读全文 | 大文档语义检索、FAQ |

---

## Phase 0：基础设施 + 依赖

### 0a. 依赖
**文件**：`backend/pyproject.toml:7-32`，按字母序追加：
- `langchain-qdrant`
- `pymupdf`（导入名 `fitz`）
- `python-docx`
- `qdrant-client`
- `jieba`（中文分词，生成 sparse 向量）
- `fastembed`（可选，sparse 向量生成的本地备选方案）

### 0b. 部署
**文件**：`deploy/docker-compose.yml`，仿 redis 块（行 98-106），在 redis 后追加 `qdrant:` 服务：
- `image: qdrant/qdrant:latest`
- 端口 `6333:6333`(HTTP) + `6334:6334`(gRPC)
- 卷挂 `${QDRANT_DATA_DIR:-${HOME}/.agent-flow/qdrant}:/qdrant/storage`
- backend + celery-worker 的 `depends_on` 追加 `qdrant`

### 0c. 配置
**文件**：`backend/app/core/config.py`
- 仿 `MONGODB_URI`(行 25) 加 `QDRANT_URL: str = "http://localhost:6333"`
- 在 KB_* 区块(行 114 后)加：
  - `KB_EMBEDDING_MODEL_ID: str = ""`
  - `KB_RERANKER_MODEL_ID: str = ""`（可选，未配则跳过 rerank）
  - `KB_VECTOR_CHUNK_SIZE: int = 800`（token）
  - `KB_VECTOR_CHUNK_OVERLAP: int = 100`（token）
  - `KB_QDRANT_COLLECTION: str = "kb_chunks"`
  - `KB_VECTOR_TOP_K: int = 5`（最终返回数）
  - `KB_VECTOR_RECALL_K: int = 20`（召回数，rerank 前）
  - `KB_VECTOR_SCORE_THRESHOLD: float = 0.5`
  - `KB_VECTOR_MAX_FILE_SIZE: int = 50*1024*1024`
  - `KB_VECTOR_EMBED_BATCH: int = 64`
- **文件**：`backend/.env.example` 加 `QDRANT_URL=http://localhost:6333`、`KB_EMBEDDING_MODEL_ID=`

**验证**：`uv sync` 成功；`docker compose up qdrant` 启动；`curl http://localhost:6333/healthz` ok。

---

## Phase 1：embedding + Qdrant + parser 核心层

### 1a. Model 表加 task_type
- **`backend/app/models/model.py`**：仿 `CompatibilityType`(行 22) 新增 `class ModelTaskType(StrEnum): CHAT="chat"; EMBEDDING="embedding"; RERANK="rerank"`；`Model` 加 `task_type: ModelTaskType = Field(default=ModelTaskType.CHAT)`（行 102 附近）
- **`backend/app/db/indexes.py:31` 后**：`await db.models.create_index("task_type", name="idx_models_task_type")`
- **`backend/app/schemas/model.py`**（如有）：Response/Create 加 `task_type`（default chat，向后兼容）

### 1b. 向量模型工厂（embedding + reranker）
**新建** `backend/app/engine/vector_factory.py`（含两个工厂函数），仿 `llm_factory.py:28-101`：
```python
async def get_embedding_client() -> Embeddings:
    """dense 向量生成。"""
    model_ref = settings.KB_EMBEDDING_MODEL_ID
    if not model_ref or not model_ref.startswith("model_"):
        raise ValueError("未配置 KB_EMBEDDING_MODEL_ID")
    doc = await ModelService.get_model_config_by_id(model_ref)  # 自动解密 api_key
    if doc.get("task_type") != "embedding":
        raise ValueError("KB_EMBEDDING_MODEL_ID 指向的模型不是 embedding 类型")
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=doc["model_id"], base_url=doc["base_url"], api_key=doc["api_key"])

async def get_reranker() -> RerankerClient | None:
    """cross-encoder rerank 客户端，未配置返回 None（降级）。"""
    model_ref = settings.KB_RERANKER_MODEL_ID
    if not model_ref:
        return None
    doc = await ModelService.get_model_config_by_id(model_ref)
    return RerankerClient(doc["base_url"], doc["model_id"], doc["api_key"])
```
启动时校验 `KB_EMBEDDING_MODEL_ID`（必须，仅告警）；`KB_RERANKER_MODEL_ID`（可选）。

### 1c. Qdrant 向量存储封装（dense + sparse 双向量）
**新建** `backend/app/engine/tool/kb_vector_store.py`：
- `get_vector_store(embeddings) -> QdrantVectorStore`：`langchain_qdrant.QdrantVectorStore`（异步），collection=`settings.KB_QDRANT_COLLECTION`。**建 collection 时配置 dense（cosine）+ sparse（BM25）双向量场**。首次写入自动建 collection
- `add_chunks(kb_id, doc_id, chunks)`：分批 upsert（每批 `KB_VECTOR_EMBED_BATCH`）
  - dense 向量：embedding 模型生成
  - sparse 向量：jieba 分词 → BM25 稀疏向量（Qdrant 内置 sparse instantiation 或 fastembed）
  - payload：`{kb_id, doc_id, chunk_index, text, source_file, page}`
- `hybrid_search(kb_id, query, k, filter)`：**单次查询同时用 dense+sparse**，Qdrant 内置 RRF 融合排序返回
- `delete_by_doc(doc_id)`：按 doc_id payload 删 point
- `delete_by_kb(kb_id)`：删整个 KB 的 point

### 1d. parser + cleaner + chunker
**新建** `backend/app/engine/tool/kb_parser/`：
- `base.py`：`BaseParser.parse(file_bytes, file_type) -> list[(text, metadata)]`
- `pdf_parser.py`：`pymupdf` 按页 `page.get_text()`，避免一次性 load，每页带 `page` 元数据
- `word_parser.py`：`python-docx` 按段落流式读
- `markdown_parser.py` / `txt_parser.py`：返回单块文本
- `cleaner.py`：**文档清洗**——去除页眉页脚（PDF 边缘位置高频重复行）/ 水印 / 冗余空行 / 解析乱码。基于规则（启发式 + 正则），不引入 LLM
- `chunker.py`：`tiktoken` 计 token；`RecursiveCharacterTextSplitter` 配中文分隔符 `["\n\n","\n","。","！","？","；","，"," ",""]`；参数取平台默认；page 元数据带到每个 chunk

**验证**：单测——mock embedding，验证 PDF/Word 转 text 块、cleaner 去除页眉页脚、chunker 切片数和重叠正确、metadata 正确传递、dense+sparse 双向量正确生成。

---

## Phase 2：数据模型 + Service + 索引 Pipeline

### 2a. KnowledgeBase 加 type 字段
- **`backend/app/models/knowledge_base.py:26` 后**：加 `type: str = Field(default="tree", description="tree / vector")`、`embedding_model_id: str = Field(default="")`
- **`backend/app/schemas/knowledge_base.py`**：
  - `KnowledgeBaseResponse`(行 45 后) 加 `type: str = "tree"`、`embedding_model_id: str = ""`
  - `KnowledgeBaseCreate`(行 64 后) 加 `type: str = Field(default="tree")`
- **`backend/app/db/indexes.py:44` 后**：`await db.knowledge_bases.create_index("type", name="idx_kb_type")`

### 2b. 文档元数据模型
**新建** `backend/app/models/knowledge_document.py`：
```python
class KnowledgeDocument(BaseModel):
    id: str = Field(default_factory=lambda: generate_id("kbdoc"), alias="_id")
    knowledge_base_id: str
    file_ref_id: str          # 关联 FileRef
    name: str
    file_type: str            # pdf/docx/md/txt
    file_size: int
    parse_status: str = "pending"   # pending/parsing/embedding/completed/failed
    parse_progress: int = 0         # 0-100
    parse_error: str = ""
    chunk_count: int = 0
    uploaded_by: str = ""
    created_at / updated_at: str
```
**新建** `backend/app/services/knowledge_document_service.py`：CRUD（仿 `KnowledgeBaseService` 风格，COLLECTION=`knowledge_documents`），索引 `knowledge_base_id`。

### 2c. Vector KB Service（按 type 分支，tree 型零改动）
**修改** `backend/app/services/kb_service.py`：
- `create_kb`(行 34)：加 `type` 参数；vector 型**不建 FS 目录**，记录 `embedding_model_id`（取 `settings.KB_EMBEDDING_MODEL_ID`）
- `upload_files`(行 191)：**按 `kb.type` 分支**
  - tree 型：**保持原逻辑不动**（仅 `.md`，UTF-8，写 FS）
  - vector 型：放宽格式（pdf/docx/md/txt）→ 校验大小 → `FileService.create` 存原始文件 → 创建 KnowledgeDocument(status=pending) → `index_kb_document.delay(kb_doc_id)` → 返回 document_ids
- `delete_kb`(行 100)：vector 型追加 `kb_vector_store.delete_by_kb(kb_id)` + 删 KnowledgeDocument 记录；tree 型保持原逻辑
- **重要**：tree 分支保持 100% 原行为，仅新增 vector 分支

### 2d. Celery 索引任务
**新建** `backend/app/workers/tasks/kb_indexing.py`，照抄 `workflow_execution.py:28-68` 模式（**必须用 `run_async` 包装**）：
```python
@celery_app.task(name="app.workers.tasks.kb_indexing.index_kb_document")
def index_kb_document(kb_doc_id: str) -> dict:
    return run_async(_index_async(kb_doc_id))

async def _index_async(kb_doc_id):
    doc = await KnowledgeDocumentService.get(kb_doc_id)
    kb = await KnowledgeBaseService.get(doc.knowledge_base_id)
    try:
        await update_status(kb_doc_id, "parsing")
        ref, raw = await FileService.load_content(doc.file_ref_id)
        text_blocks = parser.parse(raw, doc.file_type)         # 按页/段
        text_blocks = cleaner.clean(text_blocks)               # 文档清洗（去页眉页脚/水印/冗余）
        await update_status(kb_doc_id, "embedding")
        embeddings = await get_embedding_client()
        store = get_vector_store(embeddings)
        chunks = chunker.split(text_blocks, metadata={kb_id, doc_id, source_file})
        await store.add_chunks(kb.kb_id, doc.id, chunks)       # 内部分批，生成 dense+sparse 双向量，更新 progress
        await update_status(kb_doc_id, "completed", chunk_count=len(chunks), progress=100)
    except Exception as exc:
        await update_status(kb_doc_id, "failed", parse_error=str(exc))
        logger.error("kb_index_failed", kb_doc_id=kb_doc_id, error=exc)
```
**注册**：`workers/celery_app.py:16-22` include 列表追加 `"app.workers.tasks.kb_indexing"`

**验证**：API 上传 PDF → Celery 任务跑完 → Qdrant 有 point → document status=completed, progress=100。

---

## Phase 3：检索编排 + API 接口

### 3a. 检索编排（混合召回 + Rerank）
**新建** `backend/app/engine/tool/kb_retriever.py`，编排三阶段检索：
```python
async def retrieve(
    kb_id: str, query: str,
    top_k: int = settings.KB_VECTOR_TOP_K,            # 最终返回数，默认 5
    recall_k: int = settings.KB_VECTOR_RECALL_K,      # 召回数，默认 20
    score_threshold: float = settings.KB_VECTOR_SCORE_THRESHOLD,
) -> list[dict]:
    """单 KB 混合检索 + rerank。"""
    # ① 混合召回：Qdrant 单次查询 dense+sparse，内置 RRF 融合
    embeddings = await get_embedding_client()
    store = get_vector_store(embeddings)
    recall = await store.hybrid_search(
        kb_id, query, k=recall_k,
        filter=Filter(must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]),
    )
    # ② Rerank 精排（未配置 reranker 则降级跳过，直接用 RRF 融合结果）
    reranker = await get_reranker()
    if reranker is not None:
        recall = await reranker.rerank(query, recall)
    # ③ 阈值过滤 + 截断
    return [
        {"text": r.text, "score": r.score,
         "doc_id": r.doc_id, "source_file": r.source_file, "page": r.page}
        for r in recall if r.score >= score_threshold
    ][:top_k]
```
- Agent 的 `kb_search` 工具（Phase 4）、Workflow 的 `kb_search` 节点（Phase 5）、检索测试 API（3b）统一调用此函数

### 3b. API 端点
**修改** `backend/app/api/v1/knowledge_bases.py`（复用 prefix `/knowledge-bases`）：
- 现有 create/list/get/update/delete 自动支持 `type`（schema 改了即可）
- **新增** vector 专用端点（委托 service）：
  - `GET /{kb_id}/documents` — 文档列表（分页，含 parse_status/progress）
  - `DELETE /{kb_id}/documents/{doc_id}` — 删文档（删 Qdrant point + document 记录）
  - `POST /{kb_id}/documents/reindex/{doc_id}` — 重新索引失败文档
  - `POST /{kb_id}/search` — 检索测试（query + top_k，调用 `kb_retriever.retrieve`）
- **改造** `upload_documents`(行 267)：委托 `KnowledgeBaseService.upload_files`（已按 type 分支），vector 型返回含 document_ids 的响应

**验证**：上传 → 索引 → `POST /search` 走完「混合召回 → rerank → 过滤」返回相关切片；不配 reranker 时降级正常返回。

---

## Phase 4：Agent 集成（vector 型 kb_search 工具）

### 4a. kb_search 工具管理器
**新建** `backend/app/engine/tool/kb_search_manager.py`，**严格仿 `kb_manager.py` 的模式**（固定数量工具 + 可选 kb_id 参数 + 描述列出可用 KB）：
```python
class _KbSearchArgs(BaseModel):
    query: str = Field(..., description="检索查询文本")
    kb_id: str | None = Field(None, description="限定单个知识库；省略则跨所有绑定的向量知识库")

class KbSearchManager:
    def __init__(self, vector_kb_infos: dict[str, str]):
        """vector_kb_infos: {kb_id: 'name — description'}"""
        self._kb_infos = vector_kb_infos

    async def search(self, query: str, kb_id: str | None = None, top_k: int = None) -> str:
        target_ids = [kb_id] if kb_id else list(self._kb_infos)
        all_results = []
        for kid in target_ids:
            res = await kb_retriever.retrieve(kid, query, top_k=top_k or settings.KB_VECTOR_TOP_K)  # 混合召回+rerank
            all_results.extend([{"kb_id": kid, **r} for r in res])
        return json.dumps(all_results, ensure_ascii=False)

    def make_tools(self) -> list[StructuredTool]:
        kb_hint = "; ".join(f"{kid}={info}" for kid, info in self._kb_infos.items()) or "none"
        async def _search_coro(query, kb_id=None): return await self.search(query, kb_id)
        return [StructuredTool.from_function(
            _search_coro, name="kb_search",
            description=("在向量知识库中语义检索相关文档片段，返回 文本+评分+来源。"
                         " 适用于大文档语义检索、FAQ。"
                         f" 可用知识库: {kb_hint}"),
            args_schema=_KbSearchArgs, coroutine=_search_coro)]
```

### 4b. 注入点改造
**修改** `backend/app/engine/harness_integration/context.py:171-186`：现有 KB 注入段按 `type` 分组：
```python
kb_docs = await get_database()["knowledge_bases"].find({"_id": {"$in": kb_ids}}).to_list(...)

# tree 型：原逻辑不动
tree_roots = {d["_id"]: get_kb_base_path(d["_id"]) for d in kb_docs if d.get("type", "tree") == "tree"}
if tree_roots:
    all_tools.extend(KbManager(tree_roots).make_tools())

# vector 型：新增
vector_infos = {d["_id"]: f"{d['name']} — {d.get('description','')}" for d in kb_docs if d.get("type") == "vector"}
if vector_infos:
    all_tools.extend(KbSearchManager(vector_infos).make_tools())
```
- 一个 agent 可同时绑 tree + vector 型 KB，两类工具同时注入，工具名天然不冲突

**验证**：绑定 vector KB 的 agent 对话能调 `kb_search`；同时绑 tree+vector 的 agent 两类工具都可用。

---

## Phase 5：Workflow 集成（vector 型 kb_search 节点）

### 5a. kb_search 节点 executor
**新建** `backend/app/engine/workflow/nodes/kb_search.py`（仿 `ToolNodeExecutor`，`node_executor.py:737-869`）：
```python
class KbSearchNodeExecutor(BaseNodeExecutor):
    async def execute(self, variables):
        cfg = self.node_config
        expr = ExpressionEngine(variables)
        query = expr.resolve_str(cfg.get("query", ""))
        kb_ids = cfg.get("kb_ids", [])           # 节点配置选的 KB
        top_k = cfg.get("top_k", settings.KB_VECTOR_TOP_K)
        results = []
        for kid in kb_ids:
            results.extend(await kb_retriever.retrieve(kid, query, top_k=top_k))  # 混合召回+rerank
        return NodeResult(success=True, output={"results": results, "query": query})
```
**注册**：`node_executor.py:1137` `_NODE_EXECUTOR_MAP` 加 `"kb_search": KbSearchNodeExecutor`

**验证**：Workflow 加 kb_search 节点（选 vector KB + query 变量引用）→ 下游 LLM 节点用 `{{node_id.results}}` 拿到检索上下文。

---

## Phase 6：前端

### 6a. API 封装
**修改** `frontend-studio/src/services/knowledge-api.ts`：
- `KnowledgeBase`(行 11) 加 `type: 'tree' | 'vector'`、`embedding_model_id`
- `KnowledgeBaseCreateInput`(行 23) 加 `type`
- 新增方法：`listDocuments`、`deleteDocument`、`reindexDocument`、`search`；query keys 加 `documents`/`search`

### 6b. 统一 KB 选择器（关键复用组件）
**新建** `frontend-studio/src/components/ui/KbSelector.tsx`（多选 checkbox 列表 + 搜索框 + 类型图标）：
- 数据源：`knowledgeApi.list()`
- 显示：每个 KB 行显示 名称 + 类型图标(tree 📁 / vector 🔍) + description
- **Agent 编辑器的"知识库"字段** 和 **Workflow kb_search 节点的"选择知识库"字段** 都复用此组件
- tree/vector 混排可选（选择器只负责"选哪些 KB"，调用方式由类型决定）

### 6c. 创建页加类型选择
**修改** `frontend-studio/src/components/KnowledgeBasePage.tsx`：
- 创建表单(行 164 后)加"知识库类型"选择（复用 `components/ui/index.tsx:95` 的 `Select`，tree/vector 两选项）
- 每个选项描述写清用途：tree=「agent 探索式浏览结构化文档」、vector=「agent/workflow 语义检索大文档」
- vector 型选中时只读展示当前 embedding 模型
- 卡片(行 107-139)加类型徽章；`setOpenKb` 带 `type`

### 6d. vector 详情页
**新建** `frontend-studio/src/components/KbVectorDetailPage.tsx`：
- 文档列表表格（名称/类型/状态徽章 pending-parsing-embedding-completed-failed/进度条/切片数/上传时间/删除）
- 上传区（原生 `<input accept=".pdf,.docx,.md,.txt" multiple>`，复用 `uploadDocuments` FormData 模式）
- 检索测试面板（输入框 → 调 `search` → 展示 top-k 切片 + 评分 + 来源）

### 6e. 路由分流
**修改** `frontend-studio/src/App.tsx:546-556`：`KbDetailPage` 顶层先查 KB 详情拿 `type`，按 type 渲染 `<KbDirectoryEditor>`(tree) 或 `<KbVectorDetailPage>`(vector)。`openKb` state(行 98) 加 `type` 字段。

**验证**：创建 vector KB → 上传 PDF → 文档列表状态变 completed → 检索测试返回结果。Agent 绑定该 KB → 对话中调用 kb_search。

---

## 风险与注意事项

1. **Qdrant 维度锁定**：collection 维度由首个 embedding 模型确定。换 embedding 模型 = 重建 collection + 重新 embedding 全部历史 chunk（MVP 接受，文档已注明）。这也是 chunk 参数走平台级固定的原因。
2. **Celery 必须用 `run_async` 包装**（`workers/loop.py:38`），否则 motor/qdrant client 跨 loop 报错。索引任务严格照抄 `workflow_execution.py` 模式。
3. **embedding 模型必须先配**：部署后需先在 Model 表配一个 `task_type=embedding` 的模型，把其 `model_` id 填入 `KB_EMBEDDING_MODEL_ID`。启动时校验并告警。
4. **reranker 可选降级**：`KB_RERANKER_MODEL_ID` 未配时检索跳过 rerank，直接用 RRF 融合结果（召回质量略降但系统可用）。部署时若想启用需配一个 `task_type=rerank` 的模型。
5. **中文 sparse 分词**：BM25 sparse 向量依赖 jieba 分词，需验证中文专有名词/编号的命中效果。英文文本无需分词。
6. **tree 型 KB 100% 不受影响**：所有 service 方法在 tree 分支走原逻辑，现有 `kb_glob/grep/read` 工具名和行为完全不变。删除/创建 tree KB 行为不变。
7. **工具命名不冲突**：tree（kb_glob/grep/read）和 vector（kb_search）工具名天然区分，agent 同时绑定两类无歧义。
8. **权限**：复用现有 `knowledge:read`/`knowledge:write`，无需新增权限点。

---

## 可选增强（P1，非 MVP 阻塞项）

以下能力已规划但不纳入 MVP，按需在 MVP 稳定后增量开发。每项可独立叠加，互不依赖：

| # | 能力 | 价值 | 实现路径 | 影响面 |
|---|---|---|---|---|
| 1 | **查询改写**（HyDE / 多查询生成 / 查询分解） | 提升多轮对话和模糊问题的召回率 | 在 `kb_retriever` 召回前加一步 LLM 改写查询（复用 chat 模型）；多查询并行召回后融合 | `kb_retriever.py` 前置改写层 |
| 2 | **语义切片** | 替代固定 token 切分，按标题层级/段落边界切分，保持语义完整 | 用 MarkdownHeaderTextSplitter（MD）/ PDF 结构感知 splitter，替换 `chunker.py` 的 RecursiveCharacterTextSplitter | `kb_parser/chunker.py` |
| 3 | **元数据抽取 + 过滤** | LLM 抽取章节/作者/日期/标签，支持"在 2024 年文档里检索" | Pipeline 加一步 LLM 元数据抽取，写入 Qdrant payload；检索时支持 metadata filter 条件 | `kb_indexing.py` + payload schema + 检索 filter |
| 4 | **多模态文档**（图片 OCR / PDF 表格解析） | 支持含图表的 PDF | PDF 表格用 camelot/pdfplumber，图片用 paddleocr/tesseract；解析结果转 markdown 后进常规切片 | `kb_parser/` 新增 table/image parser |

实施建议：能力 3（元数据过滤）企业知识库需求最强，建议优先；能力 2（语义切片）对检索质量提升明显但需重切历史数据；能力 1、4 按实际场景取舍。

---

## 开发顺序

- Phase 0→6 顺序开发，每个 Phase 完成后独立验证
- Phase 1-3 后端核心可先全跑通（CLI/API 验证），Phase 4-5 集成，Phase 6 前端
- 每个 Phase 建议独立 commit
