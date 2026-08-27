/**
 * WorkflowBaseNode — 通用自定义节点底座（紧凑版）。
 *
 * 显示：图标（代表节点类型）+ 主要信息摘要。
 *
 * lucide-react 版本（替换原 @ant-design/icons）。
 */
import { memo, type ReactNode } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import {
  PlayCircle,
  StopCircle,
  Workflow,
  Wrench,
  GitBranch,
  Split,
  UserCheck,
} from 'lucide-react'
import type { WorkflowNodeData } from '../utils/canvas-converters'

type Props = NodeProps & { data: WorkflowNodeData }

/** 节点类型 → 图标组件映射（直接在渲染时创建，避免序列化问题） */
const TYPE_ICONS: Record<string, ReactNode> = {
  start: <PlayCircle size={12} strokeWidth={2} />,
  end: <StopCircle size={12} strokeWidth={2} />,
  agent: <Workflow size={12} strokeWidth={2} />,
  tool: <Wrench size={12} strokeWidth={2} />,
  gateway: <GitBranch size={12} strokeWidth={2} />,
  parallel: <Split size={12} strokeWidth={2} />,
  human: <UserCheck size={12} strokeWidth={2} />,
}

/**
 * 从节点 config 中提取一段简要描述，显示在节点卡片上。
 */
function getNodeSummary(type: string, config: Record<string, unknown>): string {
  switch (type) {
    case 'start': {
      // 优先读 config.output_variables（用户自定义输出变量）
      const outputVars = config.output_variables as Array<{ name?: string }> | undefined
      if (outputVars && outputVars.length > 0) {
        return `输出: ${outputVars.map((v) => v.name).join(', ')}`
      }
      // fallback 到 input_schema
      const schema = config.input_schema as { properties?: Record<string, unknown> } | undefined
      const keys = schema?.properties ? Object.keys(schema.properties) : []
      if (keys.length === 0) return '未定义输出变量'
      return `输出: ${keys.join(', ')}`
    }
    case 'end': {
      const mapping = config.output_mapping as Record<string, unknown> | undefined
      if (!mapping || Object.keys(mapping).length === 0) return '未定义输出映射'
      return `输出: ${Object.keys(mapping).join(', ')}`
    }
    case 'agent': {
      const agentId = config.agent_id as string | undefined
      const query = config.input_query as string | undefined
      if (agentId && query) {
        const shortQuery = query.length > 25 ? `${query.slice(0, 25)}...` : query
        return `Agent: ${agentId.slice(0, 8)}.. | ${shortQuery}`
      }
      if (agentId) return `Agent: ${agentId.slice(0, 8)}.. | 未填写查询`
      if (query) {
        const shortQuery = query.length > 25 ? `${query.slice(0, 25)}...` : query
        return `查询: ${shortQuery}`
      }
      return '请选择 Agent 并填写查询'
    }
    case 'tool': {
      const toolId = config.tool_id as string | undefined
      if (toolId) return `工具: ${toolId}`
      return '未选择工具'
    }
    case 'gateway': {
      const conditions = config.conditions as unknown[] | undefined
      if (conditions && conditions.length > 0) return `${conditions.length} 个条件分支`
      return '未配置条件'
    }
    case 'parallel': {
      const branches = config.branches as unknown[] | undefined
      if (branches && branches.length > 0) return `${branches.length} 个并行分支`
      return '未配置分支'
    }
    case 'human': {
      const title = config.title as string | undefined
      if (title) return title
      return '未设置审批标题'
    }
    default:
      return ''
  }
}

/**
 * 必填配置完整性检查（与后端 validator 的 MISSING_* 规则对齐）。
 * 不完整的节点在画布上以琥珀色边框 + 角标警示，避免执行时才被
 * WORKFLOW_VALIDATION_FAILED 拦下、对着裸 node_id 找不到节点。
 */
function isIncomplete(type: string, config: Record<string, unknown>): boolean {
  switch (type) {
    case 'agent':
      return !config.agent_id
    case 'tool':
      return !config.tool_id
    case 'subflow':
      return !config.workflow_id
    default:
      return false
  }
}

function WorkflowBaseNode({ data, selected }: Props) {
  const { typeColor, workflowNode } = data
  const nodeType = workflowNode.type
  const isStart = nodeType === 'start'
  const isEnd = nodeType === 'end'
  const icon = TYPE_ICONS[nodeType]
  const summary = getNodeSummary(nodeType, workflowNode.config as Record<string, unknown>)
  const incomplete = isIncomplete(nodeType, workflowNode.config as Record<string, unknown>)

  return (
    <div
      className={`
        relative rounded-md bg-[#18181b] shadow-sm border transition-shadow cursor-pointer
        ${selected ? 'border-[#1E5EFF] shadow-md' : incomplete ? 'border-amber-500 hover:shadow-md' : 'border-[#27272a] hover:shadow-md'}
      `}
      style={{ minWidth: 110, maxWidth: 160 }}
    >
      {/* 配置不完整角标 */}
      {incomplete && (
        <span
          title="配置不完整：缺少必填项（如未选择 Agent），执行前校验会拦截"
          className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-amber-500"
        />
      )}
      {/* 主体 */}
      <div className="px-2 py-1.5">
        {/* 图标 + 名称 */}
        <div className="flex items-center gap-1">
          <span className="text-xs flex-shrink-0" style={{ color: typeColor }}>{icon}</span>
          <span className="text-[11px] font-medium text-[#fafafa] truncate">{data.label}</span>
        </div>

        {/* 摘要信息 */}
        {summary && (
          <div className="text-[10px] text-slate-400 mt-0.5 truncate" title={summary}>
            {summary}
          </div>
        )}
      </div>

      {/* Source Handle — End 节点隐藏 */}
      {!isEnd && (
        <Handle
          type="source"
          position={Position.Right}
          className="!w-1.5 !h-1.5 !border-[1.5px] !border-[#09090b] !transition-colors"
          style={{ backgroundColor: typeColor }}
        />
      )}

      {/* Target Handle — Start 节点隐藏 */}
      {!isStart && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-1.5 !h-1.5 !border-[1.5px] !border-[#09090b] !transition-colors"
          style={{ backgroundColor: typeColor }}
        />
      )}
    </div>
  )
}

export default memo(WorkflowBaseNode)
