/**
 * Tools API service — wraps backend /tools endpoints.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export interface SkillFile {
  path: string
  content: string
  size: number
}

export interface Tool {
  id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  instructions: string
  source: string
  source_file: string
  mcp_connection_id: string
  version: number
  tags: string[]
  avatar: string
  files: SkillFile[]
  created_at: string
  updated_at: string
}

export interface ToolListParams {
  page?: number
  page_size?: number
  name?: string
  /** Filter by source: markdown / mcp / builtin */
  source?: string
  /** Filter by MCP connection ID */
  mcp_connection_id?: string
}

export interface ToolListResponse {
  items: Tool[]
  total: number
  page: number
  page_size: number
}

export interface ToolUploadResult {
  created: Tool[]
  errors: ToolUploadError[]
}

export interface ToolUploadError {
  filename: string
  error: string
}

export interface SkillFileTreeNode {
  key: string
  title: string
  is_leaf: boolean
  children?: SkillFileTreeNode[]
  size: number
}

export interface SkillFileTreeResponse {
  tool_id: string
  files: SkillFileTreeNode[]
}

export interface SkillFileUpdatePayload {
  content: string
}

export interface BuiltinTool {
  name: string
  description: string
  parameters: Record<string, unknown>
}

/* ─── API methods ─── */

export const toolsApi = {
  /**
   * List tools with optional pagination, name search, source filter.
   * GET /api/v1/tools
   */
  async list(params: ToolListParams = {}): Promise<ToolListResponse> {
    const res = await apiClient.get<ToolListResponse>('/api/v1/tools', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.name ? { name: params.name } : {}),
        ...(params.source ? { source: params.source } : {}),
        ...(params.mcp_connection_id ? { mcp_connection_id: params.mcp_connection_id } : {}),
      },
    })
    return res.data
  },

  /**
   * List built-in tools (bash / read / write).
   * GET /api/v1/tools/builtin
   */
  async listBuiltins(): Promise<BuiltinTool[]> {
    const res = await apiClient.get<BuiltinTool[]>('/api/v1/tools/builtin')
    return res.data
  },

  /**
   * Get a single tool by ID.
   * GET /api/v1/tools/{id}
   */
  async get(toolId: string): Promise<Tool> {
    const res = await apiClient.get<Tool>(`/api/v1/tools/${encodeURIComponent(toolId)}`)
    return res.data
  },

  /**
   * Upload Skill files (single files or a whole directory).
   * POST /api/v1/tools/upload
   *
   * The backend groups files into directory-mode tools by the `/` in each
   * part's filename. For folder uploads (input[webkitdirectory]) each File
   * carries `webkitRelativePath` like "my-skill/SKILL.md" — pass it as the
   * third append() arg so the filename keeps the path. Loose files have an
   * empty webkitRelativePath and fall back to `name` (single-file mode).
   */
  async upload(files: File[]): Promise<ToolUploadResult> {
    const formData = new FormData()
    files.forEach((f) => {
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      formData.append('files', f, rel)
    })
    const res = await apiClient.post<ToolUploadResult>('/api/v1/tools/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return res.data
  },

  /**
   * Update a tool's tags.
   * PUT /api/v1/tools/{id}
   */
  async update(toolId: string, data: { tags?: string[] }): Promise<Tool> {
    const res = await apiClient.put<Tool>(`/api/v1/tools/${encodeURIComponent(toolId)}`, data)
    return res.data
  },

  /**
   * Delete a tool.
   * DELETE /api/v1/tools/{id}
   */
  async remove(toolId: string): Promise<void> {
    await apiClient.delete(`/api/v1/tools/${encodeURIComponent(toolId)}`)
  },

  /**
   * Upload a Skill/Tool avatar image (cropped PNG blob).
   * POST /api/v1/tools/{id}/avatar (multipart) → returns { avatar: url }.
   */
  async uploadAvatar(toolId: string, file: Blob): Promise<string> {
    const form = new FormData()
    form.append('file', file, 'avatar.png')
    const res = await apiClient.post<{ avatar: string }>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/avatar`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    return res.data.avatar
  },

  /**
   * Remove a Skill/Tool avatar (revert to default logo).
   * DELETE /api/v1/tools/{id}/avatar
   */
  async removeAvatar(toolId: string): Promise<void> {
    await apiClient.delete(`/api/v1/tools/${encodeURIComponent(toolId)}/avatar`)
  },

  /**
   * Get file tree for a directory-based tool.
   * GET /api/v1/tools/{id}/files
   */
  async getFileTree(toolId: string): Promise<SkillFileTreeResponse> {
    const res = await apiClient.get<SkillFileTreeResponse>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/files`,
    )
    return res.data
  },

  /**
   * Get a single file's content.
   * GET /api/v1/tools/{id}/files/{path}
   */
  async getFileContent(toolId: string, filePath: string): Promise<SkillFile> {
    const res = await apiClient.get<SkillFile>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/files/${encodeURIComponent(filePath)}`,
    )
    return res.data
  },

  /**
   * Update a single file's content.
   * PUT /api/v1/tools/{id}/files/{path}
   */
  async updateFileContent(toolId: string, filePath: string, content: string): Promise<SkillFile> {
    const res = await apiClient.put<SkillFile>(
      `/api/v1/tools/${encodeURIComponent(toolId)}/files/${encodeURIComponent(filePath)}`,
      { content },
    )
    return res.data
  },
}

/* ─── Query key factory ─── */

export const toolKeys = {
  all: ['tools'] as const,
  lists: () => [...toolKeys.all, 'list'] as const,
  list: (params: ToolListParams) => [...toolKeys.lists(), params] as const,
  details: () => [...toolKeys.all, 'detail'] as const,
  detail: (id: string) => [...toolKeys.details(), id] as const,
  files: (id: string) => [...toolKeys.detail(id), 'files'] as const,
  fileContent: (id: string, path: string) => [...toolKeys.detail(id), 'file', path] as const,
}
