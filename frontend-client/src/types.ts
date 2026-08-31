export interface AuthUser {
  id: string
  username: string
  role: string
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: AuthUser | null
}

export interface RecommendedItem {
  label: string
  prompt?: string | null
}

export interface AgentSummary {
  id: string
  name: string
  description: string
  avatar?: string | null
  status: string
  accessSource: 'company_owned' | 'platform_assigned'
  /** 终端用户首屏欢迎词（Markdown） */
  welcomeMessage?: string
  /** 终端用户首屏推荐问题/操作快捷项 */
  recommendedItems?: RecommendedItem[]
  /** 该 Agent 是否开启实时语音对话（仅 apikey/ext 列表返回） */
  voiceEnabled?: boolean
}

export interface EffectiveResource {
  resource_id: string
  name: string
  status: string
  details: Record<string, unknown>
  access_source: 'company_owned' | 'platform_assigned'
  effective_actions: string[]
}

export interface AgentRecord {
  id: string
  name: string
  description: string | null
  avatar?: string | null
  status: string
  welcome_message?: string | null
  recommended_items?: RecommendedItem[]
  voice_enabled?: boolean
}

export interface ChatSession {
  id: string
  user_id: string
  agent_id: string
  title: string | null
  status: string
  created_at: string | null
}

export interface ToolCallMeta {
  tool_call_id: string
  name: string
  args?: string
  result?: string
  is_error?: boolean
  auto?: boolean
}

export interface ProcessMeta {
  reasoning?: string
  thoughts?: string[]
  tool_calls?: ToolCallMeta[]
}

export interface MessageRecord {
  id: string
  session_id: string
  role: string
  content: string
  /** 展示文案（快捷指令 label）——content 是实际发送给 AI 的内容，气泡优先展示该字段 */
  display_text?: string | null
  /** 本轮执行请求 id——消息级反馈（§8.2）的轮次键 */
  request_id?: string
  timeline_entries?: Array<{
    type: string
    content?: string
    tool_name?: string
    args?: Record<string, unknown>
    /** tool_call entry 的唯一 id(来自 AIMessage.tool_calls[i].id)。 */
    id?: string
    /** tool_result entry 对应的 tool_call id(来自 ToolMessage.tool_call_id)。 */
    tool_call_id?: string
    /** tool_result entry 是否为执行失败(来自 ToolMessage.status == "error")。
     *  旧数据没有该字段,按 falsy 处理为正常完成。 */
    is_error?: boolean
  }>
  files?: Array<{
    id?: string
    _id?: string
    name: string
    mime_type: string
    size?: number
  }>
  created_at: string | null
}

export interface ToolRun {
  id: string
  name: string
  /** LangGraph tool_call id,用于精确配对 tool_call ↔ tool_result。 */
  toolCallId?: string
  args?: string
  result?: string
  isError?: boolean
  auto?: boolean
  status: 'running' | 'complete' | 'error'
}

export interface AttachmentView {
  id: string
  name: string
  contentType: string
  url?: string
  kind: 'image' | 'file'
  source: 'upload' | 'output' | 'local'
}

/** 内容块:assistant 消息按真实执行顺序排列的原子单元。
 * text / reasoning / tool 三类交替出现,保留 agent 思考-工具-回复的原始顺序。 */
export type ContentBlock =
  | { type: 'text'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'tool'; tool: ToolRun }

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  /** user 消息:单个 text block;assistant 消息:按执行顺序交错的 blocks。 */
  content: ContentBlock[]
  /** 展示文案（快捷指令 label）——设置时 user 气泡优先渲染它，隐藏实际发送的指令 */
  displayText?: string
  attachments: AttachmentView[]
  charts: string[]
  status: 'loading' | 'success' | 'error' | 'abort'
  createdAt?: Date
  error?: string
  /** 本轮执行请求 id——消息级反馈（§8.2）的轮次键（流 done 事件/历史加载） */
  requestId?: string
}

export interface ClarificationField {
  name: string
  label: string
  field_type: 'text' | 'number' | 'boolean' | 'select'
  required: boolean
  options?: string[] | null
  default?: string | number | boolean | null
  description?: string | null
}

export interface HitlState {
  taskId: string
  /** Discriminator: clarification (ask_clarification) vs workflow_confirmation (confirm_workflow). */
  kind: 'clarification' | 'workflow_confirmation'
  question: string
  clarificationType: string
  context?: string
  options: string[]
  fields?: ClarificationField[]
  // workflow_confirmation fields (confirm_workflow) — only set when kind === 'workflow_confirmation'.
  workflowName?: string
  workflowDescription?: string
  inputPreview?: Record<string, unknown>
}

/** 忽略标记文案——与后端 MessageService.DISMISSED_RESULT_TEXT 保持一致
 *  （dismiss 持久化的合成 tool_result 内容）。 */
export const DISMISSED_CLARIFICATION_TEXT = '(用户已忽略此问题)'

export interface SessionFile {
  id?: string
  name?: string
  path: string
  size: number
  mime?: string
  is_output?: boolean
  modified: number | null
}

export interface FileUploadResult {
  id: string
  name: string
  path: string
  size: number
  mime: string
  is_output: boolean
}

export type StreamEventType =
  | 'text'
  | 'text_delta'
  | 'thinking'
  | 'thinking_delta'
  | 'tool_call_start'
  | 'tool_call'
  | 'tool_result'
  | 'interrupt'
  | 'error'

export interface StreamEvent {
  type?: StreamEventType
  done?: true
  /** done 事件携带——本轮请求 id（消息级反馈轮次键） */
  request_id?: string
  content?: string
  tool_name?: string
  args?: Record<string, unknown>
  auto?: boolean
  /** tool_call 事件的唯一 id(LangGraph AIMessage.tool_calls[i].id)。
   * 用于和后续 tool_result 事件的 tool_call_id 精确配对。 */
  id?: string
  /** tool_result 事件对应的 tool_call id(来自 ToolMessage.tool_call_id)。 */
  tool_call_id?: string
  // interrupt payload — clarification (ask_clarification) fields
  question?: string
  clarification_type?: string
  context?: string | null
  options?: string[] | null
  fields?: ClarificationField[] | null
  // interrupt payload — workflow_confirmation (confirm_workflow) fields
  kind?: 'clarification' | 'workflow_confirmation'
  workflow_name?: string
  workflow_description?: string
  input_preview?: Record<string, unknown> | null
  interrupt_id?: string
  status?: 'success' | 'error'
  source?: 'llm' | 'tool' | 'graph'
}
