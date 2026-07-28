/**
 * Knowledge page — knowledge base management (tree + vector types).
 *
 * Powered by TanStack Query + knowledge-api.ts service.
 * Backend contract: snake_case fields, paginated list.
 *
 * Features:
 * - KB list (table) with name / type tag / description / stats
 * - Create KB modal (choose type: tree 文档树 / vector 向量库)
 * - Click a KB → open detail Drawer (tree: file tree + editor; vector: docs + retrieval test)
 * - Delete KB (with agent-reference guard on backend)
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Table, Tag, Modal, Input, Radio, Button, Space, Tooltip, Popconfirm, App as AntdApp } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  PlusOutlined,
  DeleteOutlined,
  FileTextOutlined,
  SearchOutlined,
  BookOutlined,
} from '@ant-design/icons'
import {
  knowledgeApi,
  knowledgeKeys,
  type KnowledgeBase,
  type KbType,
} from '../services/knowledge-api'
import KbTreeDetail from '../features/knowledge_base/KbTreeDetail'
import KbVectorDetail from '../features/knowledge_base/KbVectorDetail'

/* ─── Helpers ─── */

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(iso: string): string {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

function TypeTag({ type }: { type: KbType }) {
  if (type === 'vector') {
    return (
      <Tag color="green" style={{ marginInlineEnd: 0 }}>
        <SearchOutlined /> 向量库
      </Tag>
    )
  }
  return (
    <Tag color="blue" style={{ marginInlineEnd: 0 }}>
      <BookOutlined /> 文档树
    </Tag>
  )
}

/* ─── Page ─── */

export default function KnowledgePage() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()

  /* ─── Create modal state ─── */
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDesc, setCreateDesc] = useState('')
  const [createType, setCreateType] = useState<KbType>('tree')

  /* ─── Detail drawer state ─── */
  const [activeKb, setActiveKb] = useState<KnowledgeBase | null>(null)

  /* ─── Query: KB list ─── */
  const { data, isLoading } = useQuery({
    queryKey: knowledgeKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => knowledgeApi.list({ page: 1, page_size: 100 }),
  })
  const kbs = data?.items ?? []

  /* ─── Mutations ─── */
  const createM = useMutation({
    mutationFn: () =>
      knowledgeApi.create({
        name: createName.trim(),
        description: createDesc.trim() || undefined,
        type: createType,
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
      message.success('知识库已创建')
      setCreateOpen(false)
      setCreateName('')
      setCreateDesc('')
      setCreateType('tree')
      setActiveKb(created)
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : '创建失败'
      message.error(msg)
    },
  })

  const deleteM = useMutation({
    mutationFn: (kbId: string) => knowledgeApi.remove(kbId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
      message.success('知识库已删除')
      setActiveKb(null)
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : '删除失败'
      message.error(msg)
    },
  })

  /* ─── Table columns ─── */
  const columns: ColumnsType<KnowledgeBase> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: KnowledgeBase) => (
        <Space>
          <span className="font-medium text-[#0F172A]">{name}</span>
          <TypeTag type={(record.type === 'vector' ? 'vector' : 'tree') as KbType} />
        </Space>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (desc: string) => <span className="text-[#64748B]">{desc || '—'}</span>,
    },
    {
      title: '内容量',
      key: 'stats',
      width: 140,
      render: (_: unknown, record: KnowledgeBase) => (
        <span className="text-xs text-[#64748B]">
          {record.type === 'vector' ? '向量库' : `${record.file_count} 文件`}
          {' · '}
          {formatSize(record.total_size)}
        </span>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      render: (iso: string) => <span className="text-xs text-[#94A3B8]">{formatTime(iso)}</span>,
    },
    {
      title: '',
      key: 'action',
      width: 60,
      render: (_: unknown, record: KnowledgeBase) => (
        <Popconfirm
          title="删除知识库"
          description="知识库内所有内容将被清除，被 Agent 引用时将拒绝删除。"
          okText="删除"
          okButtonProps={{ danger: true }}
          cancelText="取消"
          onConfirm={() => deleteM.mutate(record.id)}
        >
          <Tooltip title="删除">
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Tooltip>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-semibold text-[#0F172A]">知识库</h1>
          <p className="text-sm text-[#64748B] mt-0.5">
            文档树（Agent 用 kb_glob/grep/read 探索）与向量库（Agent/工作流 用 kb_search 检索）
          </p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          创建知识库
        </Button>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <Table<KnowledgeBase>
          rowKey="id"
          columns={columns}
          dataSource={kbs}
          loading={isLoading}
          size="middle"
          pagination={false}
          onRow={(record) => ({
            onClick: () => setActiveKb(record),
            style: { cursor: 'pointer' },
          })}
          locale={{ emptyText: '暂无知识库，点击右上角创建' }}
        />
      </div>

      {/* Create modal */}
      <Modal
        title="创建知识库"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createName.trim() && createM.mutate()}
        okText="创建"
        cancelText="取消"
        confirmLoading={createM.isPending}
        okButtonProps={{ disabled: !createName.trim() }}
      >
        <div className="space-y-4 py-2">
          <div>
            <label className="block text-xs text-slate-500 mb-1">名称 *</label>
            <Input
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="如：产品手册"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">描述</label>
            <Input
              value={createDesc}
              onChange={(e) => setCreateDesc(e.target.value)}
              placeholder="这个知识库涵盖哪些内容…"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-2">类型</label>
            <Radio.Group
              value={createType}
              onChange={(e) => setCreateType(e.target.value as KbType)}
            >
              <Space direction="vertical">
                <Radio value="tree">
                  <Space>
                    <FileTextOutlined className="text-blue-500" />
                    <span className="font-medium">文档树</span>
                    <span className="text-xs text-slate-400">.md 文件，Agent 用 glob/grep/read 探索</span>
                  </Space>
                </Radio>
                <Radio value="vector">
                  <Space>
                    <SearchOutlined className="text-green-500" />
                    <span className="font-medium">向量库</span>
                    <span className="text-xs text-slate-400">PDF/Word/MD，语义检索 + 重排</span>
                  </Space>
                </Radio>
              </Space>
            </Radio.Group>
          </div>
          {createType === 'vector' && (
            <p className="text-xs text-amber-600 bg-amber-50 rounded p-2">
              向量库需后端配置 KB_EMBEDDING_* 环境变量后方可索引文档。
            </p>
          )}
        </div>
      </Modal>

      {/* Detail drawer */}
      {activeKb && (
        activeKb.type === 'vector' ? (
          <KbVectorDetail
            kb={activeKb}
            onClose={() => setActiveKb(null)}
          />
        ) : (
          <KbTreeDetail
            kb={activeKb}
            onClose={() => setActiveKb(null)}
          />
        )
      )}
    </div>
  )
}
