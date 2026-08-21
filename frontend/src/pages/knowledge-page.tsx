/**
 * Knowledge page — knowledge base card grid (tree + vector types).
 *
 * Layout inspired by frontend-studio: responsive card grid where each card
 * shows the KB icon / name / type badge / description / stats. Hover reveals
 * a delete button. Click a card → navigate to /knowledge/:id detail page.
 *
 * Create modal picks the KB type (tree 文档树 / vector 向量库).
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Modal, Input, Radio, Button, Space, Popconfirm, App as AntdApp, Empty, Spin } from 'antd'
import {
  PlusOutlined, DeleteOutlined, FileTextOutlined, SearchOutlined, BookOutlined,
} from '@ant-design/icons'
import {
  knowledgeApi, knowledgeKeys,
  type KnowledgeBase, type KbType,
} from '../services/knowledge-api'
import { useAuthStore } from '../stores/auth-store'

/* ─── Helpers ─── */

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function TypeBadge({ type }: { type: KbType }) {
  if (type === 'vector') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold bg-green-50 border border-green-200 text-green-600">
        <SearchOutlined style={{ fontSize: 9 }} />向量库
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold bg-blue-50 border border-blue-200 text-blue-600">
      <BookOutlined style={{ fontSize: 9 }} />文档树
    </span>
  )
}

/* ─── KB card ─── */

function KbCard({
  kb,
  onDelete,
  onOpen,
  canWrite,
}: {
  kb: KnowledgeBase
  onDelete: (kb: KnowledgeBase) => void
  onOpen: (kb: KnowledgeBase) => void
  canWrite: boolean
}) {
  const type = (kb.type === 'vector' ? 'vector' : 'tree') as KbType
  return (
    <div
      onClick={() => onOpen(kb)}
      className="group relative bg-white border border-gray-200 rounded-xl overflow-hidden hover:border-gray-300 hover:shadow-sm transition cursor-pointer"
    >
      <div className="p-5 space-y-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <div className={`w-11 h-11 rounded-xl flex items-center justify-center border shrink-0 ${
              type === 'vector'
                ? 'bg-green-50 border-green-100 text-green-500'
                : 'bg-blue-50 border-blue-100 text-blue-500'
            }`}>
              {type === 'vector'
                ? <SearchOutlined style={{ fontSize: 20 }} />
                : <BookOutlined style={{ fontSize: 20 }} />}
            </div>
            <div className="space-y-0.5 min-w-0">
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-semibold text-gray-900 truncate">{kb.name}</h4>
                <TypeBadge type={type} />
              </div>
              <span className="text-[10px] text-gray-400 font-mono">{kb.id}</span>
            </div>
          </div>
          {/* delete on hover —— 仅 knowledge:write（§7.6 权限原则） */}
          {canWrite && (
            <Popconfirm
              title="删除知识库"
              description="知识库内所有内容将被清除，被 Agent 引用时将拒绝删除。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={(e) => { e?.stopPropagation(); onDelete(kb) }}
            >
            <button
              onClick={(e) => e.stopPropagation()}
              className="p-1 px-1.5 bg-gray-50 border border-gray-200 rounded-lg text-red-400 hover:text-red-500 hover:bg-red-50 opacity-0 group-hover:opacity-100 transition cursor-pointer"
              title="删除"
            >
              <DeleteOutlined style={{ fontSize: 13 }} />
            </button>
            </Popconfirm>
          )}
        </div>
        <p className="text-xs text-gray-500 leading-relaxed min-h-[32px] line-clamp-2">
          {kb.description || '（无描述）'}
        </p>
        <div className="flex items-center gap-3 text-[10px] text-gray-400 font-mono">
          <span className="flex items-center gap-1">
            <FileTextOutlined style={{ fontSize: 11 }} />
            {type === 'vector' ? '向量库' : `${kb.file_count} 文件`}
          </span>
          <span>{formatSize(kb.total_size)}</span>
        </div>
      </div>
    </div>
  )
}

/* ─── Page ─── */

export default function KnowledgePage() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  // §7.6 权限原则：只读用户（knowledge:read）不显示写操作
  const canWrite = useAuthStore((s) => (s.user?.permissions ?? []).includes('knowledge:write'))

  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDesc, setCreateDesc] = useState('')
  const [createType, setCreateType] = useState<KbType>('tree')

  const { data, isLoading } = useQuery({
    queryKey: knowledgeKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => knowledgeApi.list({ page: 1, page_size: 100 }),
  })
  const kbs = data?.items ?? []

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
      navigate(`/knowledge/${created.id}`)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '创建失败'),
  })

  const deleteM = useMutation({
    mutationFn: (kbId: string) => knowledgeApi.remove(kbId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
      message.success('知识库已删除')
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  })

  const handleDelete = (kb: KnowledgeBase) => deleteM.mutate(kb.id)
  const handleOpen = (kb: KnowledgeBase) => navigate(`/knowledge/${kb.id}`)

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">知识库</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            文档树（Agent 用 kb_glob/grep/read 探索）与向量库（Agent/工作流 用 kb_search 检索）
          </p>
        </div>
        {/* 创建仅 knowledge:write（§7.6 权限原则：只读用户不显示写入口） */}
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建知识库
          </Button>
        )}
      </div>

      {/* Card grid */}
      {isLoading ? (
        <div className="flex justify-center py-16"><Spin /></div>
      ) : kbs.length === 0 ? (
        <Empty description={canWrite ? '还没有知识库，点击右上角创建' : '还没有知识库'} />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {kbs.map((kb) => (
            <KbCard key={kb.id} kb={kb} onDelete={handleDelete} onOpen={handleOpen} canWrite={canWrite} />
          ))}
        </div>
      )}

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
            <label className="block text-xs text-gray-500 mb-1">名称 *</label>
            <Input
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="如：产品手册"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">描述</label>
            <Input
              value={createDesc}
              onChange={(e) => setCreateDesc(e.target.value)}
              placeholder="这个知识库涵盖哪些内容…"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-2">类型</label>
            <Radio.Group
              value={createType}
              onChange={(e) => setCreateType(e.target.value as KbType)}
            >
              <Space direction="vertical">
                <Radio value="tree">
                  <Space>
                    <BookOutlined className="text-blue-500" />
                    <span className="font-medium">文档树</span>
                    <span className="text-xs text-gray-400">.md 文件，Agent 用 glob/grep/read 探索</span>
                  </Space>
                </Radio>
                <Radio value="vector">
                  <Space>
                    <SearchOutlined className="text-green-500" />
                    <span className="font-medium">向量库</span>
                    <span className="text-xs text-gray-400">PDF/Word/MD，语义检索 + 重排</span>
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
    </div>
  )
}
