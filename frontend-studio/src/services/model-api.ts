/**
 * Model API service — wraps backend /models endpoints.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type ModelStatus = 'active' | 'inactive'

export type CompatibilityType = 'openai' | 'anthropic'

export type AuthType = 'bearer' | 'x_api_key' | 'api_key_header' | 'custom'

export interface ModelDefaultParams {
  temperature?: number
  max_tokens?: number
  context_window?: number
}

export interface Model {
  id: string
  model_id: string
  name: string
  base_url: string
  api_key: string // Masked (e.g. 'sk-****abcd')
  compatibility_type: CompatibilityType
  auth_type: AuthType
  auth_header_format?: string
  default_params?: ModelDefaultParams
  status: ModelStatus
  /** Outcome of the most recent connectivity test. null/undefined = never tested. */
  last_test_success?: boolean | null
  /** ISO timestamp of the most recent test. ''/undefined = never tested. */
  last_test_at?: string
  provider_tag?: string
  version: number
  created_at: string
  updated_at: string
}

export interface ModelCreateInput {
  model_id: string
  name: string
  base_url: string
  api_key: string // Plaintext input
  compatibility_type?: CompatibilityType
  auth_type?: AuthType
  auth_header_format?: string
  default_params?: ModelDefaultParams
  provider_tag?: string
}

export type ModelUpdateInput = ModelCreateInput

export interface ModelListParams {
  page?: number
  page_size?: number
  status?: ModelStatus
  provider_tag?: string
}

export interface ModelListResponse {
  items: Model[]
  total: number
  page: number
  page_size: number
}

export interface ModelTestResult {
  success: boolean
  latency_ms: number
  reply: string
  error: string
  error_code: string
  tested_at: string
}

/* ─── API methods ─── */

export const modelApi = {
  /**
   * List models with optional pagination, status, provider_tag filters.
   * GET /api/v1/models
   */
  async list(params: ModelListParams = {}): Promise<ModelListResponse> {
    const res = await apiClient.get<ModelListResponse>('/api/v1/models', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.status ? { status: params.status } : {}),
        ...(params.provider_tag ? { provider_tag: params.provider_tag } : {}),
      },
    })
    return res.data
  },

  /**
   * Get a single model by ID.
   * GET /api/v1/models/{id}
   */
  async get(modelId: string): Promise<Model> {
    const res = await apiClient.get<Model>(`/api/v1/models/${encodeURIComponent(modelId)}`)
    return res.data
  },

  /**
   * Create a new model.
   * POST /api/v1/models — returns 201 on success.
   */
  async create(input: ModelCreateInput): Promise<Model> {
    const res = await apiClient.post<Model>('/api/v1/models', input)
    return res.data
  },

  /**
   * Update a model (full PUT, version auto-increments).
   * PUT /api/v1/models/{id}
   */
  async update(modelId: string, input: ModelUpdateInput): Promise<Model> {
    const res = await apiClient.put<Model>(`/api/v1/models/${encodeURIComponent(modelId)}`, input)
    return res.data
  },

  /**
   * Delete a model. Returns 204 No Content on success.
   * DELETE /api/v1/models/{id}
   */
  async remove(modelId: string): Promise<void> {
    await apiClient.delete(`/api/v1/models/${encodeURIComponent(modelId)}`)
  },

  /**
   * Test model connectivity by sending a minimal probe request.
   * POST /api/v1/models/{id}/test
   *
   * Returns success/latency/reply or a curated error message.
   */
  async test(modelId: string): Promise<ModelTestResult> {
    const res = await apiClient.post<ModelTestResult>(
      `/api/v1/models/${encodeURIComponent(modelId)}/test`,
    )
    return res.data
  },
}

/* ─── Query key factory ─── */

export const modelKeys = {
  all: ['models'] as const,
  lists: () => [...modelKeys.all, 'list'] as const,
  list: (params: ModelListParams) => [...modelKeys.lists(), params] as const,
  details: () => [...modelKeys.all, 'detail'] as const,
  detail: (id: string) => [...modelKeys.details(), id] as const,
}
