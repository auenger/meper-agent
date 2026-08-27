# Workflow 静态验证模块

## 概述

`WorkflowValidator` 提供工作流的**静态结构分析**功能，在**不执行任务**的情况下检测潜在问题，避免无效执行。

## 核心功能

### 1. DAG 结构验证

- **环检测**：检测工作流中是否存在循环依赖
- **孤立节点检测**：识别无法从 start 节点到达的节点
- **必需节点检查**：验证是否包含 start 和 end 节点

```python
from app.engine.workflow.validator import validate_workflow

result = validate_workflow(workflow_doc)
if not result.is_valid:
    for error in result.errors:
        print(f"错误: {error.code} - {error.message}")
```

### 2. 变量引用验证

检查所有 `{{node.field}}` 表达式：

- 引用是否存在
- 引用的节点是否在上游（拓扑顺序）
- 防止前向引用（引用下游节点的输出）

**示例错误**：

```
[ERROR] INVALID_VARIABLE_REF: Variable reference '{{nonexistent.field}}'
        refers to non-existent source 'nonexistent' (node: agent_123)

[ERROR] FORWARD_REFERENCE: Variable reference '{{agent2.result}}'
        refers to downstream or unrelated node 'agent2' (node: agent_1)
```

### 3. 节点配置验证

验证每种节点类型的必需字段：

| 节点类型 | 必需字段 |
|---------|---------|
| `agent` | `agent_id` |
| `subflow` | `workflow_id` |
| `tool` | `tool_id` |
| `gateway` | `conditions`（警告级别） |

agent 节点的输出模型与 opt-in 配置另有专项校验：

**agent 输出 = 类 API 返回的 JSON 对象**（所有执行分支结构恒定，下游引用永不踩空）：

| 字段 | 类型 | 说明 |
|-----|---------|------|
| `status` | string | `"ok"`（正常）/ `"insufficient"`（信息不足信号） |
| `response` | string / object / array | 核心内容：默认文本；声明返回结构后为原生 dict/list |
| `agent_id` | string | 固定 |
| `files` | array | 产出文件（insufficient 时恒为 `[]`） |
| `usage` | object | token 用量（insufficient 时恒为 `{}`） |
| `needed_info` | string | 信息不足时 Agent 说明需补充的内容，否则 `""` |

- **`insufficient_branch`（信息不足分支）**：abort_workflow 触发时不再硬失败，而是
  输出 `status="insufficient"` 并只执行该分支（典型：human 澄清节点）。校验
  目标节点存在且不指向自身（计入连边索引，参与 DAG/孤儿检测）。
- **`response_schema`（response 返回结构）**：`{type: "text"|"object"|"array",
  fields: [...]}`；字段 `{name, type: string|number|boolean|enum|object,
  required, enum_values, description, fields(第二层)}`，**嵌套最多两层**。
  校验：type 白名单、字段名须为合法标识符（特殊字符会破坏
  `{{node.response.field}}` 引用）、enum 必须带非空 `enum_values`、第三层嵌套
  报错。运行时 Agent 最终回复必须是符合契约的 JSON（违规自动带反馈重试一次，
  二次违规按 `AGENT_OUTPUT_SCHEMA_VIOLATION` 失败），解析后的原生 dict/list
  写入 `response`，下游 `{{node.response.field.sub}}` 直接取值；array 用数字
  下标（`{{node.response.0.title}}`）。

注意：DAG 不允许回边，信息不足分支澄清后不能回环重跑原 agent 节点，需由作者
接续新节点。

### 4. 循环调用检测（异步）

静态分析 Agent→Workflow→Agent 的调用链，检测潜在的循环调用：

```python
from app.engine.workflow.validator import validate_workflow_async

result = await validate_workflow_async(workflow_doc)
# 包括跨工作流的循环调用检测
```

## API 接口

### 验证已存在的工作流

```http
POST /api/v1/workflows/{workflow_id}/validate
```

**响应示例**：

```json
{
  "workflow_id": "wf_xxx",
  "is_valid": false,
  "error_count": 2,
  "warning_count": 1,
  "issues": [
    {
      "severity": "error",
      "code": "CYCLE_DETECTED",
      "message": "Workflow contains a cycle: a → b → a",
      "node_id": null,
      "context": {"cycle": ["a", "b", "a"]}
    }
  ]
}
```

### 验证内联工作流（创建前）

```http
POST /api/v1/workflows/validate
Content-Type: application/json

{
  "name": "My Workflow",
  "nodes": [...],
  "edges": [...]
}
```

## 自动验证集成

### 1. Workflow 保存时验证

更新 `nodes` 或 `edges` 时自动运行验证：

```python
# WorkflowService.update() 会自动验证
await WorkflowService.update(workflow_id, {"nodes": new_nodes})
# 如果验证失败，抛出 ValidationError
```

### 2. Task 执行前验证

执行工作流前快速检查结构：

```python
# WorkflowEngine.run_and_persist() 会自动验证
await engine.run_and_persist(task_id)
# 如果验证失败，Task 立即标记为 FAILED
```

## 错误代码参考

| 代码 | 严重级别 | 描述 |
|-----|---------|------|
| `NO_START_NODE` | ERROR | 缺少 start 节点 |
| `NO_END_NODE` | ERROR | 缺少 end 节点 |
| `CYCLE_DETECTED` | ERROR | 检测到循环依赖 |
| `INVALID_VARIABLE_REF` | ERROR | 引用不存在的节点 |
| `FORWARD_REFERENCE` | ERROR | 引用下游节点 |
| `MISSING_AGENT_ID` | ERROR | Agent 节点缺少 agent_id |
| `MISSING_WORKFLOW_ID` | ERROR | Subflow 节点缺少 workflow_id |
| `MISSING_TOOL_ID` | ERROR | Tool 节点缺少 tool_id |
| `INVALID_INSUFFICIENT_BRANCH` | ERROR | agent 节点 insufficient_branch 指向不存在/自身的节点 |
| `INVALID_RESPONSE_SCHEMA` | ERROR | agent 节点 response_schema 结构定义不合法（类型/字段名/枚举/嵌套超两层） |
| `MULTIPLE_START_NODES` | WARNING | 多个 start 节点 |
| `ORPHAN_NODE` | WARNING | 孤立节点（不可达） |
| `EMPTY_GATEWAY_CONDITIONS` | WARNING | Gateway 无条件 |

## 使用场景

### 场景 1：前端工作流编辑器

用户在编辑器中保存工作流时：

```javascript
// 前端调用
const response = await fetch(`/api/v1/workflows/${workflowId}/validate`, {
  method: 'POST',
});
const result = await response.json();

if (!result.is_valid) {
  // 显示错误提示，阻止用户发布
  showError(result.issues);
}
```

### 场景 2：CI/CD 管道

在部署前验证工作流：

```bash
# 使用 curl 验证
curl -X POST http://localhost:8000/api/v1/workflows/wf_xxx/validate \
  -H "Authorization: Bearer $TOKEN"

# 解析结果
jq '.is_valid'
```

### 场景 3：批量验证

验证所有已发布的工作流：

```python
from app.services.workflow_service import WorkflowService
from app.engine.workflow.validator import validate_workflow

workflows, _ = await WorkflowService.list(status=WorkflowStatus.PUBLISHED)
for wf in workflows:
    result = validate_workflow(wf)
    if not result.is_valid:
        print(f"Workflow {wf['name']} has errors: {result.errors}")
```

## 性能考虑

- **同步验证** (`validate_workflow`): ~1ms per workflow — 适合实时反馈
- **异步验证** (`validate_workflow_async`): ~10-100ms — 包含跨工作流分析，适合后台任务

## 扩展指南

### 添加新的验证规则

1. 在 `WorkflowValidator` 类中添加新方法：

```python
def _check_my_new_rule(self) -> list[ValidationIssue]:
    issues = []
    # 验证逻辑
    return issues
```

2. 在 `validate()` 方法中调用：

```python
def validate(self) -> ValidationResult:
    issues: list[ValidationIssue] = []
    issues.extend(self._check_dag_structure())
    issues.extend(self._check_variable_references())
    issues.extend(self._check_node_configs())
    issues.extend(self._check_my_new_rule())  # 添加这里
    return ValidationResult.from_issues(issues)
```

3. 添加测试：

```python
def test_my_new_rule():
    workflow = _make_workflow([...])
    result = validate_workflow(workflow)
    assert not result.is_valid
    assert any(i.code == "MY_NEW_ERROR" for i in result.errors)
```

## 相关文件

- `backend/app/engine/workflow/validator.py` — 核心实现
- `backend/app/api/v1/workflows.py` — API 接口
- `backend/app/services/workflow_service.py` — 保存时验证
- `backend/app/engine/workflow/engine.py` — 执行前验证
- `backend/tests/test_workflow_validator.py` — 测试用例
