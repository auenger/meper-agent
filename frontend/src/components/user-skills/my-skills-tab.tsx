/**
 * 我的技能 / 我的记忆 Tab（§4/§6.5/§7.6）。
 *
 * 我的技能 = 统一卡片墙（与技能广场同风格）：
 * - 管理员：官方技能卡（皇冠标，编辑/删除/创建）在前 + 个人技能卡在后——
 *   管理员的"我的技能"即官方管理台（单一管理入口，广场只读）
 * - 普通用户：个人技能卡（会话创建/手动新建/编辑/发布/启停/删除/卸载，fork 溯源标签）
 * 我的记忆：条目编辑 + 用量 + 清空。
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Progress,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CrownOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth-store'
import { useTheme } from '../../contexts/ThemeContext'
import {
  userSkillsApi,
  userSkillKeys,
} from '../../services/user-skills-api'
import { toolsApi, toolKeys } from '../../services/tools-api'
import { agentKeys } from '../../services/agent-api'
import SkillUploadModal from '../skill-upload-modal'

const { Text, Paragraph } = Typography

const STATUS_TAG: Record<string, { label: string; color: string }> = {
  private: { label: '私有', color: 'default' },
  submitted: { label: '审核中', color: 'processing' },
  published: { label: '已发布', color: 'success' },
  hidden: { label: '已隐藏', color: 'warning' },
}

/** 手动创建的 SKILL.md 模板（含 description 规则提示） */
const DEFAULT_TEMPLATE = `---
name: my_skill
description: Use when <触发条件>. <一行行为说明>（前60字符须自包含触发条件）
---

# 技能标题

## 适用场景
（什么情况下应该使用这个技能）

## 步骤
1. ……

## 避坑
- ……
`

export function MySkillsTab() {
  const { message } = App.useApp()
  const { t } = useTheme()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const role = useAuthStore((s) => s.user?.role)
  const isAdmin = role === 'admin' || role === 'developer'

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: userSkillKeys.list() })
    queryClient.invalidateQueries({ queryKey: toolKeys.lists() })
  }

  /* ── 数据：个人 + （管理员）官方 ── */
  const { data: skills = [], isLoading } = useQuery({
    queryKey: userSkillKeys.list(),
    queryFn: userSkillsApi.list,
  })
  const { data: officialData, isLoading: officialLoading } = useQuery({
    queryKey: toolKeys.list({ page_size: 100, source: 'markdown' }),
    queryFn: () => toolsApi.list({ page_size: 100, source: 'markdown' }),
    enabled: isAdmin,
  })
  const officials = officialData?.items ?? []
  const loading = isLoading || (isAdmin && officialLoading)

  /* ── 创建（§7.6 单一入口按角色分流）──
     admin → SkillUploadModal（文件上传，原官方创建方式——支持多文件/目录包）
     用户  → 文本 Modal（名称 + SKILL.md 模板） */
  const [createOpen, setCreateOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newContent, setNewContent] = useState('')

  const openCreate = () => {
    if (isAdmin) {
      setUploadOpen(true)
      return
    }
    setNewName('')
    setNewContent(DEFAULT_TEMPLATE)
    setCreateOpen(true)
  }

  const createMutation = useMutation({
    mutationFn: () => userSkillsApi.create(newName.trim(), newContent),
    onSuccess: (r) => {
      message.success(`已创建「${r.name}」（下一轮对话生效）`)
      setCreateOpen(false)
      invalidate()
    },
    onError: (e: Error) => message.error(`创建失败：${e.message}`),
  })

  /* ── 个人技能操作 ── */
  const toggleMutation = useMutation({
    mutationFn: (v: { id: string; enabled: boolean }) =>
      userSkillsApi.update(v.id, { binding_enabled: v.enabled }),
    onSuccess: () => invalidate(),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => userSkillsApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      invalidate()
    },
  })
  const submitMutation = useMutation({
    mutationFn: (id: string) => userSkillsApi.submit(id),
    onSuccess: () => {
      message.success('已提交审核（管理员通过后进入技能广场）')
      invalidate()
    },
    onError: (e: Error) => message.error(`提交失败：${e.message}`),
  })
  const uninstallMutation = useMutation({
    mutationFn: (id: string) => userSkillsApi.uninstall(id),
    onSuccess: () => {
      message.success('已卸载')
      invalidate()
    },
  })

  /* ── 官方技能操作（管理员）── */
  const deleteOfficialMutation = useMutation({
    mutationFn: (id: string) => toolsApi.remove(id),
    onSuccess: () => {
      message.success('已删除官方技能')
      queryClient.invalidateQueries({ queryKey: toolKeys.lists() })
      queryClient.invalidateQueries({ queryKey: agentKeys.all })
    },
  })

  const officialCount = officials.length
  const personalCount = skills.length

  return (
    <Card variant="outlined">
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <Text type="secondary">
          {isAdmin
            ? `官方技能 ${officialCount}（绑定 Agent 全员生效）——管理员没有个人技能，会话内保存也会产出官方技能`
            : '在会话里对 Agent 说「把这个保存成技能」来创建，或手动新建；发布后进入技能广场。'}
        </Text>
        <Space>
          {/* 单一创建入口，按角色分流：admin=官方技能 / 用户=个人技能（§7.6） */}
          <Button type="primary" icon={isAdmin ? <CrownOutlined /> : <PlusOutlined />} onClick={openCreate}>
            {isAdmin ? '新建官方技能' : '新建技能'}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => invalidate()}>
            刷新
          </Button>
        </Space>
      </div>

      {loading ? (
        <div className="flex justify-center py-20">
          <Spin size="large" />
        </div>
      ) : (isAdmin ? officialCount : personalCount) === 0 ? (
        <Empty
          description={isAdmin ? '暂无官方技能' : '还没有个人技能'}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          className="py-20"
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {/* ── 官方技能卡（管理员可见）── */}
          {isAdmin &&
            officials.map((o) => (
              <div
                key={`official-${o.id}`}
                className="rounded-xl border border-line bg-canvas p-4 shadow-sm hover:shadow-md hover:border-txt-muted transition-all duration-200 flex flex-col"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center text-sm shrink-0"
                      style={{ background: '#FEF3C7', color: '#D97706' }}
                    >
                      <CrownOutlined />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-txt truncate">{o.name}</div>
                      <div className="text-xs text-txt-3 truncate">官方 · 绑定 Agent 生效</div>
                    </div>
                  </div>
                  <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="gold">
                    官方
                  </Tag>
                </div>
                <Paragraph
                  type="secondary"
                  style={{ marginBottom: 12, fontSize: 12, flex: 1 }}
                  ellipsis={{ rows: 2 }}
                >
                  {o.description || '（无描述）'}
                </Paragraph>
                <div className="text-[11px] text-txt-muted mb-3">
                  {(o as { stats?: { load_count?: number } }).stats?.load_count ?? 0} 次真实加载
                  {o.files?.length ? ` · ${o.files.length} 文件` : ''}
                </div>
                <div className="flex items-center justify-end pt-3 border-t border-line-2">
                  <Space size={4}>
                    <Button
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => navigate(`/skills/${o.id}`)}
                    >
                      编辑
                    </Button>
                    <Popconfirm
                      title="删除官方技能？"
                      description="绑定了该技能的 Agent 将失去此技能。"
                      onConfirm={() => deleteOfficialMutation.mutate(o.id)}
                    >
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                </div>
              </div>
            ))}

          {/* ── 个人技能卡（§7.6 管理员无个人技能，仅普通用户渲染）── */}
          {!isAdmin &&
            skills.map((r) => {
            const st = STATUS_TAG[r.status] ?? STATUS_TAG.private
            return (
              <div
                key={r.id}
                className="rounded-xl border border-line bg-canvas p-4 shadow-sm hover:shadow-md hover:border-txt-muted transition-all duration-200 flex flex-col"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center text-sm shrink-0"
                      style={{ background: t.bg, color: t.primary }}
                    >
                      <UserOutlined />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-txt truncate">{r.alias || r.name}</div>
                      <div className="text-xs text-txt-3 truncate">
                        {r.source === 'installed' ? '安装的技能' : '个人技能'}
                      </div>
                    </div>
                  </div>
                  <Space size={4} wrap style={{ justifyContent: 'flex-end' }}>
                    {r.source === 'installed' && (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]">安装</Tag>
                    )}
                    {r.derived_from_name && (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="purple">
                        fork 自 {r.derived_from_name}
                      </Tag>
                    )}
                    <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color={st.color}>
                      {st.label}
                    </Tag>
                  </Space>
                </div>

                <Paragraph
                  type="secondary"
                  style={{ marginBottom: 12, fontSize: 12, flex: 1 }}
                  ellipsis={{ rows: 2 }}
                >
                  {r.description || '（无描述）'}
                </Paragraph>

                <div className="text-[11px] text-txt-muted mb-3">
                  {r.stats?.load_count ?? 0} 次真实加载
                </div>

                <div
                  className="flex items-center justify-between pt-3 border-t border-line-2"
                  style={{ gap: 4 }}
                >
                  <Tooltip title={r.binding_enabled ? '已启用（跨 Agent 生效）' : '已停用'}>
                    <Switch
                      size="small"
                      checked={r.binding_enabled}
                      onChange={(checked) => toggleMutation.mutate({ id: r.id, enabled: checked })}
                    />
                  </Tooltip>
                  <Space size={4}>
                    {r.source === 'own' ? (
                      <>
                        <Tooltip title="编辑全文">
                          <Button
                            size="small"
                            type="text"
                            aria-label="edit"
                            icon={<EditOutlined />}
                            onClick={() => navigate(`/my-skills/${r.id}`)}
                          />
                        </Tooltip>
                        {r.status === 'private' && (
                          <Popconfirm
                            title="提交发布审核？"
                            description="管理员审核通过后将进入技能广场，供所有用户安装使用。"
                            onConfirm={() => submitMutation.mutate(r.id)}
                          >
                            <Button size="small" type="text" icon={<SendOutlined />}>
                              发布
                            </Button>
                          </Popconfirm>
                        )}
                        <Popconfirm
                          title="删除该技能？"
                          description="磁盘文件与元数据将一并删除，不可恢复。"
                          onConfirm={() => deleteMutation.mutate(r.id)}
                        >
                          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </>
                    ) : (
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DownloadOutlined style={{ transform: 'rotate(180deg)' }} />}
                        onClick={() => uninstallMutation.mutate(r.id)}
                      >
                        卸载
                      </Button>
                    )}
                  </Space>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 手动创建 Modal */}
      <Modal
        title="新建个人技能"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createMutation.mutate()}
        okText="创建"
        okButtonProps={{ disabled: !newName.trim() || !newContent.trim(), loading: createMutation.isPending }}
        width={720}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              技能名（字母/数字/中划线/下划线，个人空间内唯一）
            </Text>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="如 weekly_report"
              maxLength={64}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              SKILL.md 全文——description 前 60 字符须自包含触发条件（决定 agent 何时加载它）
            </Text>
            <Input.TextArea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              autoSize={{ minRows: 14, maxRows: 26 }}
              style={{ fontFamily: 'monospace' }}
            />
          </div>
        </Space>
      </Modal>

      {/* 管理员创建官方技能：文件上传 Modal（原有方式，支持多文件/目录包） */}
      <SkillUploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: toolKeys.lists() })
          queryClient.invalidateQueries({ queryKey: agentKeys.all })
        }}
      />
    </Card>
  )
}

export function MyMemoryTab() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const invalidateMemory = () => queryClient.invalidateQueries({ queryKey: userSkillKeys.memory() })

  const { data: memory, isLoading } = useQuery({
    queryKey: userSkillKeys.memory(),
    queryFn: userSkillsApi.getMemory,
  })

  const [draft, setDraft] = useState<{ base: unknown; entries: string[] } | null>(null)
  const draftActive = draft !== null && draft.base === memory
  const entries = draftActive ? draft.entries : (memory?.entries ?? [])
  const dirty = draftActive && JSON.stringify(draft.entries) !== JSON.stringify(memory?.entries ?? [])
  const updateEntries = (next: string[]) => setDraft({ base: memory, entries: next })

  const [newEntry, setNewEntry] = useState('')

  const saveMutation = useMutation({
    mutationFn: (list: string[]) => userSkillsApi.setMemory(list),
    onSuccess: (data) => {
      message.success(`已保存（${data.usage}）`)
      setDraft(null)
      invalidateMemory()
    },
    onError: (e: Error) => message.error(`保存失败：${e.message}`),
  })

  const clearMutation = useMutation({
    mutationFn: userSkillsApi.clearMemory,
    onSuccess: () => {
      message.success('已清空')
      setDraft(null)
      invalidateMemory()
    },
  })

  const usagePercent = (() => {
    const m = memory?.usage?.match(/^(\d+)\/(\d+)/)
    if (!m) return 0
    return Math.min(100, Math.round((Number(m[1]) / Math.max(1, Number(m[2]))) * 100))
  })()

  return (
    <Card variant="outlined" loading={isLoading}>
      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
        <Progress
          percent={usagePercent}
          size="small"
          style={{ flex: 1, margin: 0 }}
          format={() => memory?.usage ?? '0/1375 chars'}
        />
        <Popconfirm
          title="清空全部记忆？"
          description="Agent 将忘记所有已记录的偏好。"
          onConfirm={() => clearMutation.mutate()}
        >
          <Button danger size="small" icon={<DeleteOutlined />}>
            一键清空
          </Button>
        </Popconfirm>
      </div>

      <List
        dataSource={entries}
        locale={{ emptyText: <Empty description="暂无记忆条目" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        renderItem={(entry, idx) => (
          <List.Item
            actions={[
              <Button
                key="del"
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => updateEntries(entries.filter((_, i) => i !== idx))}
              />,
            ]}
          >
            <Input
              value={entry}
              variant="borderless"
              maxLength={200}
              onChange={(e) => {
                const next = [...entries]
                next[idx] = e.target.value
                updateEntries(next)
              }}
            />
          </List.Item>
        )}
      />

      <Space.Compact style={{ width: '100%', marginTop: 12 }}>
        <Input
          placeholder="新增一条记忆（≤200 字符，如：偏好中文回答）"
          value={newEntry}
          maxLength={200}
          onChange={(e) => setNewEntry(e.target.value)}
          onPressEnter={() => {
            if (newEntry.trim()) {
              updateEntries([...entries, newEntry.trim()])
              setNewEntry('')
            }
          }}
        />
        <Button
          icon={<PlusOutlined />}
          onClick={() => {
            if (newEntry.trim()) {
              updateEntries([...entries, newEntry.trim()])
              setNewEntry('')
            }
          }}
        />
      </Space.Compact>

      {dirty && (
        <div style={{ marginTop: 12 }}>
          <Space>
            <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate(entries)}>
              保存修改
            </Button>
            <Button onClick={() => setDraft(null)}>放弃</Button>
          </Space>
        </div>
      )}
    </Card>
  )
}
