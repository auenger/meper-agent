/**
 * 技能广场 Tab — 只读橱窗（§7.6 逻辑单市·物理双库）。
 * 后端 marketplace 单查询返回官方（tools 表）+ 用户发布（user_skills 表）；
 * 卡片操作仅 查看/投票/安装(卸载)/fork——创建/编辑/删除/审核一律在"我的技能"。
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  CrownOutlined,
  DownloadOutlined,
  EyeOutlined,
  LikeOutlined,
  ReloadOutlined,
  SearchOutlined,
  SoundOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useAuthStore } from '../../stores/auth-store'
import { useTheme } from '../../contexts/ThemeContext'
import {
  userSkillsApi,
  userSkillKeys,
  type MarketplaceItem,
  type UserSkillItem,
} from '../../services/user-skills-api'
import { toolKeys } from '../../services/tools-api'

const { Text, Paragraph } = Typography

/** 统一市场条目：官方（tools 表）或用户发布（user_skills 表） */
interface UnifiedItem {
  key: string
  kind: 'official' | 'user'
  id: string
  name: string
  description: string
  author: string
  loadCount: number
  score?: number
  installed?: boolean
  isOwn?: boolean
  myVote?: number
  fileCount?: number
}

export function MarketplaceTab() {
  const { message } = App.useApp()
  const { t } = useTheme()
  const queryClient = useQueryClient()
  const role = useAuthStore((s) => s.user?.role)
  const isAdmin = role === 'admin' || role === 'developer'

  const [search, setSearch] = useState('')
  const [aliasTarget, setAliasTarget] = useState<UnifiedItem | null>(null)
  const [aliasValue, setAliasValue] = useState('')

  // 查看预览（后端 GET /user-skills/{id} 统一支持 usk_ 与 tool_ 两类 id）
  const [previewTarget, setPreviewTarget] = useState<UnifiedItem | null>(null)
  const { data: previewDetail, isLoading: previewLoading } = useQuery({
    queryKey: userSkillKeys.detail(previewTarget?.id ?? ''),
    queryFn: () => userSkillsApi.get(previewTarget!.id),
    enabled: !!previewTarget,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: userSkillKeys.marketplace(search) })
    queryClient.invalidateQueries({ queryKey: userSkillKeys.list() })
    // admin fork = 收录写 tools 表（§7.6）——必须同步失效官方墙查询，
    // 否则"我的技能"拿旧缓存看不到新收录的官方技能
    queryClient.invalidateQueries({ queryKey: toolKeys.lists() })
  }

  /* 逻辑单市：后端 marketplace 单查询已合并官方+用户（§7.6） */
  const { data: marketItems = [], isLoading: loading } = useQuery({
    queryKey: userSkillKeys.marketplace(search),
    queryFn: () => userSkillsApi.marketplace(search),
  })

  const items: UnifiedItem[] = marketItems.map<UnifiedItem>((u: MarketplaceItem & { _id?: string }) => ({
    key: `${u.kind}-${u._id ?? u.id}`,
    kind: u.kind,
    id: u._id ?? u.id, // 防御：marketplace 返回 Mongo 裸文档（_id）
    name: u.name,
    description: u.description,
    author: u.author_name,
    loadCount: u.stats?.load_count ?? 0,
    score: u.score,
    installed: u.installed,
    isOwn: u.is_own,
    myVote: u.my_vote,
  }))

  const installMutation = useMutation({
    mutationFn: (v: { id: string; alias?: string }) => userSkillsApi.install(v.id, v.alias),
    onSuccess: (r) => {
      message.success(`已安装为「${r.effective_name}」，下一轮对话生效`)
      setAliasTarget(null)
      invalidate()
    },
    onError: (e: Error) => {
      const msg = e.message || ''
      if (msg.includes('conflict') || msg.includes('重名')) {
        const target = items.find((it) => it.kind === 'user' && !it.installed && !it.isOwn)
        if (target) {
          setAliasTarget(target)
          setAliasValue(`${target.name}-mine`)
        }
        message.info('存在同名技能，请填写安装别名')
      } else {
        message.error(`安装失败：${msg}`)
      }
    },
  })

  const uninstallMutation = useMutation({
    mutationFn: (id: string) => userSkillsApi.uninstall(id),
    onSuccess: () => {
      message.success('已卸载')
      invalidate()
    },
  })

  const forkMutation = useMutation({
    mutationFn: (id: string) => userSkillsApi.fork(id),
    onSuccess: (r) => {
      // 管理员 fork = 收录为官方（后端按角色路由，§7.6）
      if ((r as { kind?: string }).kind === 'official') {
        message.success(`已收录为官方技能「${r.name}」（进入广场官方区，可绑定 Agent）`)
      } else {
        message.success(`已 fork 为「${r.name}」（私有副本，可在"我的技能"编辑）`)
      }
      invalidate()
    },
    onError: (e: Error) => message.error(`Fork 失败：${e.message}`),
  })

  return (
    <div>
      {role === 'admin' && <ReviewPanel />}

      <Card variant="outlined">
        <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: '#94A3B8' }} />}
            placeholder="搜索技能（名称 / 描述）"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ maxWidth: 360 }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => invalidate()}>
            刷新
          </Button>
          <Text type="secondary" style={{ marginLeft: 'auto', alignSelf: 'center' }}>
            {items.length} 个技能 · 官方 {items.filter((i) => i.kind === 'official').length} / 用户发布{' '}
            {items.filter((i) => i.kind === 'user').length}
          </Text>
        </div>

        {loading ? (
          <div className="flex justify-center py-20">
            <Spin size="large" />
          </div>
        ) : items.length === 0 ? (
          <Empty description="暂无技能" image={Empty.PRESENTED_IMAGE_SIMPLE} className="py-20" />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {items.map((item) => (
              <div
                key={item.key}
                className="rounded-xl border border-line bg-canvas p-4 shadow-sm hover:shadow-md hover:border-txt-muted transition-all duration-200 flex flex-col cursor-pointer"
                onClick={() => setPreviewTarget(item)}
              >
                {/* 头部：图标 + 名称 + 作者 + 标签（点击卡片查看全文） */}
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center text-sm shrink-0"
                      style={
                        item.kind === 'official'
                          ? { background: '#FEF3C7', color: '#D97706' }
                          : { background: t.bg, color: t.primary }
                      }
                    >
                      {item.kind === 'official' ? <CrownOutlined /> : <UserOutlined />}
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-txt truncate">{item.name}</div>
                      <div className="text-xs text-txt-3 truncate">by {item.author}</div>
                    </div>
                  </div>
                  <Space size={4} wrap style={{ justifyContent: 'flex-end' }}>
                    {item.kind === 'official' ? (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="gold">
                        官方
                      </Tag>
                    ) : (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="geekblue">
                        用户
                      </Tag>
                    )}
                    {item.isOwn && (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="blue">
                        我的
                      </Tag>
                    )}
                    {item.installed && (
                      <Tag className="!m-0 !px-2 !py-0.5 !text-[11px]" color="green">
                        已安装
                      </Tag>
                    )}
                  </Space>
                </div>

                {/* 描述 */}
                <Paragraph
                  type="secondary"
                  style={{ marginBottom: 12, fontSize: 12, flex: 1 }}
                  ellipsis={{ rows: 2 }}
                >
                  {item.description || '（无描述）'}
                </Paragraph>

                {/* 统计行（官方与用户统一：加载次数 + 积分，§7.6）——徽标式，数字加粗 */}
                <div className="flex items-center gap-3 mb-3">
                  <span
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface-muted text-[12px]"
                    title="对话中真实 load 过的次数"
                  >
                    <ThunderboltOutlined className="text-emerald-500" style={{ fontSize: 12 }} />
                    <b className="text-txt">{item.loadCount}</b>
                    <span className="text-txt-muted">次加载</span>
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[12px] ${
                      (item.score ?? 0) > 0 ? 'bg-blue-50' : 'bg-surface-muted'
                    }`}
                    title="用户点赞回复的派生积分（👍 − 👎）"
                  >
                    <LikeOutlined className={(item.score ?? 0) >= 0 ? 'text-blue-500' : 'text-rose-500'} style={{ fontSize: 12 }} />
                    <b className={(item.score ?? 0) >= 0 ? 'text-blue-600' : 'text-rose-600'}>
                      {(item.score ?? 0) > 0 ? `+${item.score}` : item.score ?? 0}
                    </b>
                    <span className="text-txt-muted">积分</span>
                  </span>
                  {item.kind === 'official' && (
                    <span className="text-[11px] text-txt-muted">绑定 Agent 全员生效</span>
                  )}
                </div>

                {/* 底部操作区：广场只读橱窗（§7.6）。官方绑定即生效无需安装；
                    自己的技能无需安装/fork；管理员对官方无 fork（可直接编辑）。
                    点击 stopPropagation 防止触发卡片预览 */}
                <div
                  className="flex items-center justify-between pt-3 border-t border-line-2"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Space size={4}>
                    <Tooltip title="查看全文">
                      <Button
                        size="small"
                        type="text"
                        aria-label="view"
                        icon={<EyeOutlined />}
                        onClick={() => setPreviewTarget(item)}
                      />
                    </Tooltip>
                  </Space>

                  <Space size={4}>
                    {item.kind === 'user' && !item.isOwn &&
                      (item.installed ? (
                        // 卸载对所有人保留（含 admin 清理历史绑定）
                        <Button size="small" danger onClick={() => uninstallMutation.mutate(item.id)}>
                          卸载
                        </Button>
                      ) : !isAdmin ? (
                        <Button
                          size="small"
                          type="primary"
                          icon={<DownloadOutlined />}
                          onClick={() => installMutation.mutate({ id: item.id })}
                        >
                          安装
                        </Button>
                      ) : null /* §7.6 admin 无个人技能：不安装，走 fork 收录 */)}
                    {/* fork：用户技能（非自己的）；官方技能仅非管理员（管理员 fork 官方
                        无意义——可直接编辑；管理员 fork 用户技能=收录为官方，§7.6） */}
                    {(item.kind === 'user' && !item.isOwn) ||
                      (item.kind === 'official' && !isAdmin) ? (
                      <Tooltip
                        title={
                          item.kind === 'user' && isAdmin
                            ? '收录为官方技能（进入 tools 表，可绑定 Agent）'
                            : '复制为己用（可自由修改）'
                        }
                      >
                        <Button
                          size="small"
                          type="text"
                          aria-label="fork"
                          icon={<CopyOutlined />}
                          onClick={() => forkMutation.mutate(item.id)}
                        />
                      </Tooltip>
                    ) : null}
                  </Space>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Modal
        title={`安装为别名（原名「${aliasTarget?.name ?? ''}」已被占用）`}
        open={!!aliasTarget}
        onOk={() => aliasTarget && installMutation.mutate({ id: aliasTarget.id, alias: aliasValue })}
        onCancel={() => setAliasTarget(null)}
        okText="安装"
        destroyOnHidden
      >
        <Input
          value={aliasValue}
          onChange={(e) => setAliasValue(e.target.value)}
          placeholder="如 weekly_report@zhangsan（字母/数字/中划线/下划线）"
        />
      </Modal>

      {/* 查看全文（只读预览，官方与用户统一，§7.6）—— 标题防挤压：Tag+名一行，by 一行 */}
      <Drawer
        title={
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, paddingRight: 24 }}>
            <Space wrap={false}>
              {previewTarget?.kind === 'official' ? (
                <Tag color="gold" style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                  官方
                </Tag>
              ) : (
                <Tag color="geekblue" style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                  用户
                </Tag>
              )}
              <Text strong ellipsis style={{ minWidth: 0 }}>
                {previewTarget?.name}
              </Text>
            </Space>
            <Text type="secondary" ellipsis style={{ fontSize: 12, maxWidth: '100%' }}>
              by {previewTarget?.author}
            </Text>
          </div>
        }
        width={720}
        open={!!previewTarget}
        onClose={() => setPreviewTarget(null)}
        destroyOnHidden
      >
        {previewLoading ? (
          <div className="flex justify-center py-20">
            <Spin />
          </div>
        ) : (
          <pre
            className="whitespace-pre-wrap font-mono text-xs leading-relaxed"
            style={{ margin: 0 }}
          >
            {previewDetail?.content || '（无内容）'}
          </pre>
        )}
      </Drawer>
    </div>
  )
}

/* ─── 管理员审核面板（§8.1）——紧凑条目 + 查看 Drawer 内审─── */

function ReviewPanel() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  /* 查看中的待审技能；驳回理由在 Drawer 内联 */
  const [viewTarget, setViewTarget] = useState<(UserSkillItem & { id: string }) | null>(null)
  const [reason, setReason] = useState('')
  const [rejecting, setRejecting] = useState(false)

  const { data: pending = [] } = useQuery({
    queryKey: userSkillKeys.review('submitted'),
    queryFn: () => userSkillsApi.reviewList('submitted'),
  })

  const { data: viewDetail, isLoading: viewLoading } = useQuery({
    queryKey: userSkillKeys.detail(viewTarget?.id ?? ''),
    queryFn: () => userSkillsApi.get(viewTarget!.id),
    enabled: !!viewTarget,
  })

  const reviewMutation = useMutation({
    mutationFn: (v: { id: string; action: 'approve' | 'reject'; reason?: string }) =>
      userSkillsApi.review(v.id, v.action, v.reason),
    onSuccess: (_d, v) => {
      message.success(v.action === 'approve' ? '已通过并发布' : '已驳回')
      setViewTarget(null)
      setRejecting(false)
      setReason('')
      queryClient.invalidateQueries({ queryKey: userSkillKeys.review('submitted') })
      queryClient.invalidateQueries({ queryKey: userSkillKeys.all })
    },
    onError: (e: Error) => message.error(`审核失败：${e.message}`),
  })

  if (!pending.length) return null

  const normalized = pending.map((raw) => ({
    ...raw,
    id: (raw as { _id?: string })._id ?? raw.id, // Mongo 裸文档防御
  }))

  return (
    <>
      {/* 紧凑面板：单行条目 + 查看入口（通过/驳回在查看 Drawer 内） */}
      <Card
        size="small"
        variant="outlined"
        style={{ marginBottom: 12, borderColor: '#F59E0B' }}
        styles={{ body: { padding: '4px 12px' } }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
          <SoundOutlined style={{ color: '#F59E0B' }} />
          <Text strong style={{ fontSize: 13 }}>
            待审核 {normalized.length}
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            点击查看内容后审核——检查诱导指令 / 数据外泄 / 重复
          </Text>
        </div>
        {normalized.map((item) => (
          <div
            key={item.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '2px 0',
              fontSize: 13,
            }}
          >
            <Text strong style={{ fontSize: 13 }}>
              {item.name}
            </Text>
            <Text
              type="secondary"
              ellipsis
              style={{ flex: 1, fontSize: 12 }}
              title={item.description}
            >
              {item.description || '（无描述）'}
            </Text>
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => {
                setViewTarget(item)
                setRejecting(false)
                setReason('')
              }}
            >
              查看
            </Button>
          </div>
        ))}
      </Card>

      {/* 查看 + 审核 Drawer —— 标题两行布局防挤压（Tag+名一行，描述独立一行省略） */}
      <Drawer
        title={
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, paddingRight: 24 }}>
            <Space wrap={false}>
              <Tag color="processing" style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                审核中
              </Tag>
              <Text strong ellipsis style={{ minWidth: 0 }}>
                {viewTarget?.name}
              </Text>
            </Space>
            <Text
              type="secondary"
              ellipsis
              style={{ fontSize: 12, maxWidth: '100%' }}
              title={viewDetail?.description}
            >
              {viewDetail?.description}
            </Text>
          </div>
        }
        width={720}
        open={!!viewTarget}
        onClose={() => {
          setViewTarget(null)
          setRejecting(false)
        }}
        destroyOnHidden
        footer={
          rejecting ? (
            <div style={{ display: 'flex', gap: 8, width: '100%' }}>
              <Input.TextArea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="驳回理由（展示给作者）"
                autoSize={{ minRows: 1, maxRows: 3 }}
                style={{ flex: 1 }}
              />
              <Button
                danger
                type="primary"
                loading={reviewMutation.isPending}
                disabled={!reason.trim()}
                onClick={() =>
                  viewTarget && reviewMutation.mutate({ id: viewTarget.id, action: 'reject', reason })
                }
              >
                确认驳回
              </Button>
            </div>
          ) : (
            <Space style={{ float: 'right' }}>
              <Button danger icon={<CloseCircleOutlined />} onClick={() => setRejecting(true)}>
                驳回
              </Button>
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={reviewMutation.isPending}
                onClick={() => viewTarget && reviewMutation.mutate({ id: viewTarget.id, action: 'approve' })}
              >
                通过并发布
              </Button>
            </Space>
          )
        }
      >
        {viewLoading ? (
          <div className="flex justify-center py-20">
            <Spin />
          </div>
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed" style={{ margin: 0 }}>
            {viewDetail?.content || '（无内容）'}
          </pre>
        )}
      </Drawer>
    </>
  )
}
