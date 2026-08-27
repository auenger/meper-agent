/**
 * AgentNodeConfig — Agent 节点配置面板。
 *
 * 输出模型（API 返回体）：
 * - 固定字段（status/response/agent_id/files/usage/needed_info）由引擎恒定
 *   提供，无需用户配置；下游直接 {{node.status}}、{{node.files.0.file_id}}。
 * - response 是核心内容：默认文本（零配置）；通过「返回结构」声明为
 *   对象/数组（字段+说明，嵌套最多两层），运行时引擎按契约把 Agent 的
 *   JSON 回复解析为原生结构写入 response，下游 {{node.response.field.sub}}
 *   直接取值（不依赖 JSON 字符串深解析）。
 *
 * - Agent ID 通过 Select 选择
 * - 查询（input_query）：必填，作为 user message，支持变量池
 * - 上下文（input_prompt）：可选，注入 Agent 的 context 卡槽，支持变量池
 * - 信息不足分支（insufficient_branch）：abort_workflow 触发时走该分支而非硬失败
 *
 * antd 组件 → 原生 Tailwind ui 封装；保留 @tanstack/react-query（指向 studio agent-api）。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Select, Input, Spin, Switch, Button, Modal } from '../../../components/ui'
import { agentApi, agentKeys } from '../../../services/agent-api'
import VariableSelector from '../VariableSelector'
import type { WorkflowNode } from '../../../services/workflows-api'

/** response 结构字段（与后端 _VALID_FIELD_TYPES 一致） */
interface ResponseField {
  name: string
  type: 'string' | 'number' | 'boolean' | 'enum' | 'object'
  /** 列表标志：任何基础类型都可为列表（string 列表 / enum 列表 / object 列表） */
  is_list?: boolean
  required?: boolean
  enum_values?: string[]
  description?: string
  /** 仅第一层 object 字段可带（第二层，不可再嵌） */
  fields?: ResponseField[]
}

interface ResponseSchema {
  type: 'text' | 'object' | 'array'
  fields?: ResponseField[]
}

const FIELD_TYPE_OPTIONS = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔' },
  { value: 'enum', label: '枚举' },
]

const L1_FIELD_TYPE_OPTIONS = [
  ...FIELD_TYPE_OPTIONS,
  { value: 'object', label: '对象（嵌套一层）' },
]

const RESPONSE_TYPE_OPTIONS = [
  { value: 'text', label: '文本（默认）' },
  { value: 'object', label: '对象 { }' },
  { value: 'array', label: '数组 [ ]' },
]

/** 「自评模式」一键模板：模糊自判变成封闭枚举，配合网关/信息不足分支路由 */
const SELF_ASSESS_TEMPLATE: ResponseField[] = [
  {
    name: 'status',
    type: 'enum',
    required: true,
    enum_values: ['completed', 'insufficient_info'],
    description: 'completed=任务完成；insufficient_info=信息不足（建议同时配置信息不足分支）',
  },
  { name: 'summary', type: 'string', required: true, description: '结果摘要（信息不足时说明缺什么）' },
  { name: 'assumptions', type: 'string', required: false, description: '执行中所做的关键假设' },
]

interface Props {
  config: Record<string, unknown>
  onChange: (c: Record<string, unknown>) => void
  currentNodeId: string
  allNodes: WorkflowNode[]
}

export default function AgentNodeConfig({ config, onChange, currentNodeId, allNodes }: Props) {
  const { data: agentsData, isLoading } = useQuery({
    queryKey: agentKeys.list({ page: 1, page_size: 100, status: 'published' }),
    queryFn: () => agentApi.list({ page: 1, page_size: 100, status: 'published' }),
  })

  const agents = agentsData?.items ?? []
  const agentOptions = agents.map((a) => ({
    value: a.id,
    label: `${a.name} (${a.id.substring(0, 8)}...)`,
  }))

  // 查询内容变更（作为 user message）
  const handleInputQueryChange = (val: string) => {
    onChange({ ...config, input_query: val })
  }

  // 上下文变更（注入 Agent 的 context 卡槽）
  const handleContextChange = (val: string) => {
    onChange({ ...config, input_prompt: val })
  }

  // ── 返回结构（response_schema）──
  const responseSchema = (config.response_schema as ResponseSchema | null) ?? null
  const schemaType: ResponseSchema['type'] = responseSchema?.type ?? 'text'
  const fields = responseSchema?.fields ?? []

  const setSchema = (schema: ResponseSchema | null) => {
    onChange({ ...config, response_schema: schema })
  }

  const setSchemaType = (t: string | null) => {
    const nextType = (t ?? 'text') as ResponseSchema['type']
    if (nextType === 'text') {
      setSchema(null)
      return
    }
    setSchema({ type: nextType, fields: fields.length > 0 ? fields : [{ name: '', type: 'string' }] })
  }

  const setFields = (next: ResponseField[]) => {
    setSchema({ ...(responseSchema as ResponseSchema), fields: next })
  }

  const updateField = (idx: number, patch: Partial<ResponseField>) => {
    setFields(fields.map((f, i) => (i === idx ? { ...f, ...patch } : f)))
  }

  const updateSubField = (idx: number, subIdx: number, patch: Partial<ResponseField>) => {
    const field = fields[idx]
    if (!field?.fields) return
    updateField(idx, {
      fields: field.fields.map((g, j) => (j === subIdx ? { ...g, ...patch } : g)),
    })
  }

  // ── 信息不足分支 ──
  const handleInsufficientBranchChange = (val: string | null) => {
    onChange({ ...config, insufficient_branch: val || null })
  }

  // 信息不足分支候选：画布上除自身外的所有节点（典型：人工审批澄清节点）
  const branchOptions = allNodes
    .filter((n) => n.node_id !== currentNodeId)
    .map((n) => ({ value: n.node_id, label: `${n.type} · ${n.node_id}` }))

  const insufficientBranch = (config.insufficient_branch as string) || null

  /** 渲染一行字段编辑（layer 1 = response 直属字段，layer 2 = object 子字段） */
  const renderFieldRow = (
    field: ResponseField,
    idx: number,
    layer: 1 | 2,
    onPatch: (patch: Partial<ResponseField>) => void,
    onRemove: () => void,
  ) => {
    const typeOptions = layer === 1 ? L1_FIELD_TYPE_OPTIONS : FIELD_TYPE_OPTIONS
    return (
      <div key={`${layer}-${idx}`} className="space-y-1.5 border-t border-slate-700/40 pt-2">
        <div className="grid grid-cols-[1fr_120px_32px_32px_44px] gap-1.5 items-center">
          <Input
            placeholder="字段名（如 status）"
            value={field.name}
            onChange={(e) => onPatch({ name: e.target.value })}
          />
          <Select
            className="w-full"
            value={field.type}
            onChange={(val) => onPatch({ type: (val ?? 'string') as ResponseField['type'] })}
            options={typeOptions}
          />
          <div className="flex items-center justify-center" title="必填">
            <Switch
              size="small"
              checked={!!field.required}
              onChange={(checked) => onPatch({ required: checked })}
            />
          </div>
          <div className="flex items-center justify-center" title="列表（string 列表 / enum 列表 / object 列表）">
            <Switch
              size="small"
              checked={!!field.is_list}
              onChange={(checked) => onPatch({ is_list: checked })}
            />
          </div>
          <Button size="small" onClick={onRemove}>删除</Button>
        </div>
        {field.type === 'enum' && (
          <Input
            placeholder="枚举值（逗号分隔，如 completed,insufficient_info）"
            value={(field.enum_values ?? []).join(',')}
            onChange={(e) =>
              onPatch({
                enum_values: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
              })
            }
          />
        )}
        <Input
          placeholder="说明（可选，写入 Agent 提示帮助其理解字段含义）"
          value={field.description ?? ''}
          onChange={(e) => onPatch({ description: e.target.value })}
        />
      </div>
    )
  }

  // ── 放大编辑（Modal 大空间填写，与面板内嵌编辑实时同步同一份 config） ──
  const [zoomOpen, setZoomOpen] = useState(false)

  /** 返回结构编辑器主体（面板内嵌 + 放大 Modal 共用） */
  const renderSchemaBody = () => (
    <>
      <Select
        className="w-full"
        value={schemaType}
        onChange={setSchemaType}
        options={RESPONSE_TYPE_OPTIONS}
      />
      <div className="text-[10px] text-[#71717a]">
        节点输出是类 API 返回体：status/files/usage 等固定字段由引擎恒定提供；
        response 默认是文本。改为对象/数组后，Agent 最终回复必须是符合下方契约的
        JSON（违规自动带反馈重试一次），下游用 {'{{'}节点.response.字段{'}'} 直接取值，
        嵌套最多两层；字段可勾选「列表」（如 tags 文本列表、authors 对象列表）。
      </div>

      {schemaType !== 'text' && (
        <div className="flex gap-1.5 justify-end">
          <Button
            size="small"
            onClick={() =>
              setFields(SELF_ASSESS_TEMPLATE.map((f) => ({
                ...f,
                enum_values: [...(f.enum_values ?? [])],
              })))
            }
          >
            自评模式模板
          </Button>
          <Button
            size="small"
            onClick={() => setFields([...fields, { name: '', type: 'string' }])}
          >
            加字段
          </Button>
        </div>
      )}

      {schemaType !== 'text' && fields.map((field, idx) => (
        <div key={`l1-${idx}`} className="space-y-1.5">
          {renderFieldRow(
            field,
            idx,
            1,
            (patch) => updateField(idx, patch),
            () => setFields(fields.filter((_, i) => i !== idx)),
          )}
          {field.type === 'object' && (
            <div className="ml-4 border-l-2 border-slate-700/60 pl-2.5 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-slate-500">
                  {field.name || '（未命名）'}的子字段
                  {field.is_list ? '（对象列表的元素结构' : '（第二层'}
                  ，不可再嵌套）
                </span>
                <Button
                  size="small"
                  onClick={() =>
                    updateField(idx, {
                      fields: [...(field.fields ?? []), { name: '', type: 'string' }],
                    })
                  }
                >
                  加子字段
                </Button>
              </div>
              {(field.fields ?? []).map((sub, subIdx) =>
                renderFieldRow(
                  sub,
                  subIdx,
                  2,
                  (patch) => updateSubField(idx, subIdx, patch),
                  () =>
                    updateField(idx, {
                      fields: (field.fields ?? []).filter((_, j) => j !== subIdx),
                    }),
                ),
              )}
            </div>
          )}
        </div>
      ))}
    </>
  )

  return (
    <div className="space-y-3">
      {/* Agent 选择 */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Agent</label>
        {isLoading ? (
          <Spin size="small" />
        ) : (
          <Select
            className="w-full"
            value={(config.agent_id as string) || null}
            onChange={(val) => onChange({ ...config, agent_id: val ?? '' })}
            options={agentOptions}
            placeholder="选择 Agent..."
            showSearch
            filterOption={(input, option) =>
              (String(option?.label ?? '').toLowerCase().includes(input.toLowerCase()))
            }
            allowClear
          />
        )}
      </div>

      {/* 查询（必填）→ user message */}
      <div>
        <VariableSelector
          label="查询"
          value={config.input_query as string ?? ''}
          onChange={handleInputQueryChange}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
          placeholder="作为用户消息发送给 Agent，支持 {{变量}} ..."
          rows={2}
          required
        />
      </div>

      {/* 上下文（可选）→ 注入 context 卡槽 */}
      <div>
        <VariableSelector
          label="上下文"
          value={config.input_prompt as string ?? ''}
          onChange={handleContextChange}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
          placeholder="注入到 Agent 的 context 卡槽，支持 {{变量}} ..."
          rows={3}
        />
        <div className="text-[10px] text-[#71717a] mt-0.5">
          此内容会覆盖 Agent 的 context 卡槽值，用于注入工作流上下文。角色、任务、约束等由 Agent 自身配置决定。
        </div>
      </div>

      {/* Temperature + 最大重试 + 超时 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-slate-400 mb-1">Temperature</label>
          <Input
            type="number"
            value={(config.temperature as number) ?? 0.7}
            onChange={(e) => onChange({ ...config, temperature: parseFloat(e.target.value) || 0.7 })}
            step={0.1}
            min={0}
            max={2}
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">最大重试</label>
          <Input
            type="number"
            value={(config.max_retry as number) ?? 3}
            onChange={(e) => onChange({ ...config, max_retry: parseInt(e.target.value) || 3 })}
            min={0}
            max={10}
          />
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-slate-400 mb-1">超时 (ms)</label>
          <Input
            type="number"
            value={(config.timeout_ms as number) ?? 300000}
            onChange={(e) => onChange({ ...config, timeout_ms: parseInt(e.target.value) || 300000 })}
            min={1000}
            step={1000}
          />
          <div className="text-[10px] text-[#71717a] mt-0.5">
            Agent 节点无人值守执行（不会暂停询问用户），超时后按失败处理并触发重试。
          </div>
        </div>
      </div>

      {/* 信息不足分支（opt-in）：abort_workflow 触发 → 走该分支而非硬失败 */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">信息不足分支</label>
        <Select
          className="w-full"
          value={insufficientBranch}
          onChange={handleInsufficientBranchChange}
          options={branchOptions}
          placeholder="不设置（默认：Agent 判定输入不足时工作流失败终止）"
          allowClear
        />
        <div className="text-[10px] text-[#71717a] mt-0.5">
          设置后，Agent 调用 abort_workflow 时不再失败终止，而是输出
          status=&quot;insufficient&quot; 并只执行该分支（典型：接一个人工审批节点收集补充信息，
          审批意见可用 {'{{'}human.comment{'}'}{' '} 引回下游）。
        </div>
      </div>

      {/* 返回结构（response 的结构契约，opt-in） */}
      <div className="border border-slate-700/60 rounded-md p-2.5 space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs text-slate-400">返回结构（response）</label>
          <Button size="small" onClick={() => setZoomOpen(true)}>
            ⤢ 放大编辑
          </Button>
        </div>
        {renderSchemaBody()}
      </div>

      {/* 放大编辑：大空间填写长内容（枚举值/说明/嵌套字段），与面板实时同步 */}
      <Modal
        open={zoomOpen}
        title="返回结构（response）"
        width={720}
        okText="完成"
        cancelText="关闭"
        onOk={() => setZoomOpen(false)}
        onCancel={() => setZoomOpen(false)}
      >
        <div className="space-y-2 max-h-[65vh] overflow-y-auto">{renderSchemaBody()}</div>
      </Modal>
    </div>
  )
}
