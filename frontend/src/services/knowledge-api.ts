/**
 * Knowledge Base API service — wraps backend /knowledge-bases endpoints.
 *
 * Two KB types coexist:
 *   - tree:   Markdown file tree, agent explores via kb_glob/grep/read
 *   - vector: RAG over Qdrant, agent/workflow search via kb_search
 *
 * Uses the shared apiClient (auto auth header + 401 refresh). Response fields
 * are snake_case per backend contract.
 */
import { apiClient } from './api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type KbType = 'tree' | 'vector'

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  type: KbType
  embedding_model_id: string
  owner_user_id: string
  status: string
  file_count: number
  total_size: number
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseCreateInput {
  name: string
  description?: string
  type?: KbType
}

export interface KnowledgeBaseUpdateInput {
  name?: string
  description?: string
}

export interface KnowledgeBaseListParams {
  page?: number
  page_size?: number
  name?: string
  status?: string
}

export interface KnowledgeBaseListResponse {
  items: KnowledgeBase[]
  total: number
  page: number
  page_size: number
}

/* ─── Tree KB: file tree ─── */

export interface KbFileTreeNode {
  key: string
  title: string
  is_leaf: boolean
  children?: KbFileTreeNode[]
  size: number
}

export interface KbFileTreeResponse {
  kb_id: string
  files: KbFileTreeNode[]
}

export interface KbFile {
  path: string
  content: string
  size: number
}

export interface KbUploadError {
  filename: string
  error: string
}

export interface KbUploadResult {
  created: string[]
  errors: KbUploadError[]
  document_ids: string[]
}

/* ─── Vector KB: documents + retrieval ─── */

export type KbDocStatus = 'pending' | 'parsing' | 'embedding' | 'completed' | 'failed'

export interface KbDocument {
  id: string
  name: string
  file_type: string
  file_size: number
  parse_status: KbDocStatus
  parse_progress: number
  parse_error: string
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface KbDocumentListResponse {
  items: KbDocument[]
  total: number
  page: number
  page_size: number
}

export interface KbSearchResultItem {
  text: string
  score: number
  doc_id: string
  source_file: string
  page: number | null
  kb_id?: string
}

export interface KbSearchResponse {
  query: string
  results: KbSearchResultItem[]
}

/* ─── API methods ─── */

const BASE = '/api/v1/knowledge-bases'

export const knowledgeApi = {
  /** GET /knowledge-bases */
  async list(params: KnowledgeBaseListParams = {}): Promise<KnowledgeBaseListResponse> {
    const res = await apiClient.get<KnowledgeBaseListResponse>(BASE, {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.name ? { name: params.name } : {}),
        ...(params.status ? { status: params.status } : {}),
      },
    })
    return res.data
  },

  /** GET /knowledge-bases/{id} */
  async get(kbId: string): Promise<KnowledgeBase> {
    const res = await apiClient.get<KnowledgeBase>(`${BASE}/${encodeURIComponent(kbId)}`)
    return res.data
  },

  /** POST /knowledge-bases */
  async create(input: KnowledgeBaseCreateInput): Promise<KnowledgeBase> {
    const res = await apiClient.post<KnowledgeBase>(BASE, input)
    return res.data
  },

  /** PUT /knowledge-bases/{id} */
  async update(kbId: string, input: KnowledgeBaseUpdateInput): Promise<KnowledgeBase> {
    const res = await apiClient.put<KnowledgeBase>(`${BASE}/${encodeURIComponent(kbId)}`, input)
    return res.data
  },

  /** DELETE /knowledge-bases/{id} */
  async remove(kbId: string): Promise<void> {
    await apiClient.delete(`${BASE}/${encodeURIComponent(kbId)}`)
  },

  /* ── Tree KB: files ── */

  /** GET /knowledge-bases/{id}/files */
  async getFileTree(kbId: string): Promise<KbFileTreeResponse> {
    const res = await apiClient.get<KbFileTreeResponse>(`${BASE}/${encodeURIComponent(kbId)}/files`)
    return res.data
  },

  /** GET /knowledge-bases/{id}/files/{path} */
  async getFileContent(kbId: string, filePath: string): Promise<KbFile> {
    const res = await apiClient.get<KbFile>(
      `${BASE}/${encodeURIComponent(kbId)}/files/${encodeURIComponent(filePath)}`,
    )
    return res.data
  },

  /** PUT /knowledge-bases/{id}/files/{path} */
  async updateFileContent(kbId: string, filePath: string, content: string): Promise<KbFile> {
    const res = await apiClient.put<KbFile>(
      `${BASE}/${encodeURIComponent(kbId)}/files/${encodeURIComponent(filePath)}`,
      { content },
    )
    return res.data
  },

  /** DELETE /knowledge-bases/{id}/files/{path} */
  async deleteFile(kbId: string, filePath: string): Promise<void> {
    await apiClient.delete(
      `${BASE}/${encodeURIComponent(kbId)}/files/${encodeURIComponent(filePath)}`,
    )
  },

  /**
   * Upload files into a KB.
   * POST /knowledge-bases/{id}/documents
   *
   * Folder upload supported: each File carries webkitRelativePath (e.g.
   * "notes/api.md") passed as the 3rd append() arg to preserve path.
   */
  async uploadDocuments(kbId: string, files: File[]): Promise<KbUploadResult> {
    const formData = new FormData()
    files.forEach((f) => {
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      formData.append('files', f, rel)
    })
    const res = await apiClient.post<KbUploadResult>(
      `${BASE}/${encodeURIComponent(kbId)}/documents`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    return res.data
  },

  /* ── Vector KB: documents + retrieval ── */

  /** GET /knowledge-bases/{id}/documents */
  async listDocuments(kbId: string, page = 1, pageSize = 100): Promise<KbDocumentListResponse> {
    const res = await apiClient.get<KbDocumentListResponse>(
      `${BASE}/${encodeURIComponent(kbId)}/documents`,
      { params: { page, page_size: pageSize } },
    )
    return res.data
  },

  /** DELETE /knowledge-bases/{id}/documents/{docId} */
  async deleteDocument(kbId: string, docId: string): Promise<void> {
    await apiClient.delete(
      `${BASE}/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`,
    )
  },

  /** POST /knowledge-bases/{id}/documents/{docId}/reindex */
  async reindexDocument(kbId: string, docId: string): Promise<{ status: string }> {
    const res = await apiClient.post<{ status: string }>(
      `${BASE}/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/reindex`,
    )
    return res.data
  },

  /** PUT /knowledge-bases/{id}/documents/{docId} — replace source file + re-index */
  async replaceDocument(kbId: string, docId: string, file: File): Promise<{ status: string }> {
    const formData = new FormData()
    formData.append('file', file)
    const res = await apiClient.put<{ status: string }>(
      `${BASE}/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    return res.data
  },

  /** POST /knowledge-bases/{id}/search */
  async search(kbId: string, query: string, topK = 5): Promise<KbSearchResponse> {
    const res = await apiClient.post<KbSearchResponse>(
      `${BASE}/${encodeURIComponent(kbId)}/search`,
      { query, top_k: topK },
    )
    return res.data
  },
}

/* ─── Query key factory ─── */

export const knowledgeKeys = {
  all: ['knowledge-bases'] as const,
  lists: () => [...knowledgeKeys.all, 'list'] as const,
  list: (params: KnowledgeBaseListParams) => [...knowledgeKeys.lists(), params] as const,
  details: () => [...knowledgeKeys.all, 'detail'] as const,
  detail: (id: string) => [...knowledgeKeys.details(), id] as const,
  files: (id: string) => [...knowledgeKeys.detail(id), 'files'] as const,
  fileContent: (id: string, path: string) => [...knowledgeKeys.detail(id), 'file', path] as const,
  documents: (id: string) => [...knowledgeKeys.detail(id), 'documents'] as const,
}
