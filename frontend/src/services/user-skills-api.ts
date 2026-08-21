/**
 * User skills & memory API — "我的技能 / 我的记忆"（v6 用户级经验资产，先行试点）。
 *
 * 后端：app/api/v1/user_skills.py（Phase 1）
 * 设计：docs/planning-artifacts/agent-self-learning-design.md
 */
import { apiClient } from './api-client'

export interface UserSkillItem {
  id: string
  name: string
  description: string
  status: 'private' | 'submitted' | 'published' | 'hidden'
  source: 'own' | 'installed'
  binding_enabled: boolean
  alias?: string
  derived_from?: string | null
  /** fork 来源名快照（fork 时的源名，展示用，§7.6） */
  derived_from_name?: string | null
  stats: { load_count?: number; up?: number; down?: number }
  created_at: string
  updated_at: string
}

export interface MarketplaceItem extends UserSkillItem {
  /** official=官方（tools 表，§7.6 逻辑单市·物理双库）| user=用户发布 */
  kind: 'official' | 'user'
  author_name: string
  installed: boolean
  is_own: boolean
  my_vote: number
  score: number
}

export interface UserSkillDetail extends UserSkillItem {
  content: string
}

/** 会话各轮反馈态（消息级 👍/👎，§8.2 v2） */
export interface SessionFeedbackItem {
  request_id: string
  /** 0=未投 */
  value: 0 | 1 | -1
  skills: { skill_id: string; name: string; kind: 'official' | 'user' }[]
}
export interface MemoryState {
  entries: string[]
  usage: string
}

/** Mongo 原始文档（_id）→ 前端模型（id）归一化——marketplace/reviewList 返回裸文档 */
function normalizeId<T>(raw: T): T & { id: string } {
  const r = raw as Record<string, unknown>
  return { ...raw, id: (r._id as string) ?? (r.id as string) ?? '' }
}

export const userSkillsApi = {
  list() {
    return apiClient.get<UserSkillItem[]>('/api/v1/user-skills').then((r) => r.data)
  },

  get(skillId: string) {
    return apiClient.get<UserSkillDetail>(`/api/v1/user-skills/${skillId}`).then((r) => r.data)
  },

  update(skillId: string, body: { content?: string; binding_enabled?: boolean }) {
    return apiClient
      .put<UserSkillItem>(`/api/v1/user-skills/${skillId}`, body)
      .then((r) => r.data)
  },

  remove(skillId: string) {
    return apiClient.delete(`/api/v1/user-skills/${skillId}`).then((r) => r.data)
  },

  getMemory() {
    return apiClient.get<MemoryState>('/api/v1/user-skills/memory').then((r) => r.data)
  },

  setMemory(entries: string[]) {
    return apiClient.put<MemoryState>('/api/v1/user-skills/memory', { entries }).then((r) => r.data)
  },

  clearMemory() {
    return apiClient.delete<MemoryState>('/api/v1/user-skills/memory').then((r) => r.data)
  },

  /* ── 我的技能：手动创建（§3.1 备选入口，与会话创建等价）── */

  create(name: string, content: string) {
    return apiClient
      .post<UserSkillItem>('/api/v1/user-skills', { name, content })
      .then((r) => r.data)
  },

  /** 管理员：从文本创建官方技能（§7.6 单一创建入口按角色分流） */
  createOfficial(name: string, content: string) {
    return apiClient
      .post<{ _id?: string; id?: string; name: string }>('/api/v1/user-skills/official', { name, content })
      .then((r) => r.data)
  },

  /* ── 技能广场（§7/§8）── */

  marketplace(q = '') {
    return apiClient
      .get<MarketplaceItem[]>('/api/v1/user-skills/marketplace', { params: q ? { q } : {} })
      .then((r) => r.data.map(normalizeId) as MarketplaceItem[])
  },

  submit(skillId: string) {
    return apiClient.post<UserSkillItem>(`/api/v1/user-skills/${skillId}/submit`).then((r) => r.data)
  },

  install(skillId: string, alias = '') {
    return apiClient
      .post<{ ok: boolean; effective_name: string }>(`/api/v1/user-skills/${skillId}/install`, { alias })
      .then((r) => r.data)
  },

  uninstall(skillId: string) {
    return apiClient.delete(`/api/v1/user-skills/${skillId}/install`).then((r) => r.data)
  },

  fork(skillId: string) {
    return apiClient.post<UserSkillItem>(`/api/v1/user-skills/${skillId}/fork`).then((r) => r.data)
  },

  /* ── 消息级反馈（§8.2 v2：赞回复 → 本轮技能派生加分）── */

  voteMessage(sessionId: string, requestId: string, value: 1 | -1) {
    return apiClient
      .post<{ ok: boolean; changed: boolean }>('/api/v1/user-skills/messages/vote', {
        session_id: sessionId,
        request_id: requestId,
        value,
      })
      .then((r) => r.data)
  },

  sessionFeedback(sessionId: string) {
    return apiClient
      .get<SessionFeedbackItem[]>(`/api/v1/user-skills/sessions/${sessionId}/feedback`)
      .then((r) => r.data)
  },

  /* ── 管理员审核台（§8.1）── */

  reviewList(status = 'submitted') {
    return apiClient
      .get<UserSkillItem[]>('/api/v1/user-skills/admin/review', { params: { status } })
      .then((r) => r.data.map(normalizeId) as UserSkillItem[])
  },

  review(skillId: string, action: 'approve' | 'reject', reason = '') {
    return apiClient
      .post<UserSkillItem>(`/api/v1/user-skills/admin/review/${skillId}`, { action, reason })
      .then((r) => r.data)
  },
}

export const userSkillKeys = {
  all: ['user-skills'] as const,
  list: () => [...userSkillKeys.all, 'list'] as const,
  detail: (id: string) => [...userSkillKeys.all, 'detail', id] as const,
  memory: () => [...userSkillKeys.all, 'memory'] as const,
  marketplace: (q: string) => [...userSkillKeys.all, 'marketplace', q] as const,
  review: (status: string) => [...userSkillKeys.all, 'review', status] as const,
}
