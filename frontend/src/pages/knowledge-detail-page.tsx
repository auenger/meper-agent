/**
 * KnowledgeDetailPage — KB detail (route /knowledge/:id).
 *
 * Loads the KB by id, then renders KbTreeDetail (tree) or KbVectorDetail
 * (vector). Header has a back button + breadcrumb + type-specific subtitle +
 * an edit button (rename / change description).
 *
 * Layout mirrors frontend-studio's detail page structure.
 */
import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Spin, Tag, Result, Modal, Input, App as AntdApp } from 'antd'
import {
  ArrowLeftOutlined, BookOutlined, SearchOutlined, EditOutlined,
} from '@ant-design/icons'
import { knowledgeApi, knowledgeKeys, type KbType } from '../services/knowledge-api'
import { useAuthStore } from '../stores/auth-store'
import KbTreeDetail from '../features/knowledge_base/KbTreeDetail'
import KbVectorDetail from '../features/knowledge_base/KbVectorDetail'

export default function KnowledgeDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()

  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')

  const { data: kb, isLoading, isError } = useQuery({
    queryKey: knowledgeKeys.detail(id ?? ''),
    queryFn: () => knowledgeApi.get(id!),
    enabled: !!id,
  })

  // §7.6 权限原则：只读用户（knowledge:read）不显示写操作
  const canWrite = useAuthStore((s) => (s.user?.permissions ?? []).includes('knowledge:write'))

  const editM = useMutation({
    mutationFn: () =>
      knowledgeApi.update(id!, {
        name: editName.trim(),
        description: editDesc.trim() || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.detail(id!) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.lists() })
      message.success('已更新')
      setEditOpen(false)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '更新失败'),
  })

  const openEdit = () => {
    setEditName(kb?.name ?? '')
    setEditDesc(kb?.description ?? '')
    setEditOpen(true)
  }

  if (isLoading) {
    return <div className="flex justify-center py-20"><Spin size="large" /></div>
  }

  if (isError || !kb) {
    return (
      <Result
        status="404"
        title="知识库不存在"
        subTitle={`找不到 id 为 ${id} 的知识库`}
        extra={<Button type="primary" onClick={() => navigate('/knowledge')}>返回列表</Button>}
      />
    )
  }

  const type = (kb.type === 'vector' ? 'vector' : 'tree') as KbType

  return (
    <div className="animate-[fadeIn_0.3s_ease-out] h-full flex flex-col">
      {/* Header: back + breadcrumb + edit */}
      <div className="flex items-center gap-3 mb-4 shrink-0">
        <Button
          type="text"
          shape="circle"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/knowledge')}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <span>知识库</span>
            <span>/</span>
            <span className="font-semibold text-gray-900 truncate">{kb.name}</span>
            {type === 'vector' ? (
              <Tag color="green" icon={<SearchOutlined />} style={{ marginInlineEnd: 0 }}>向量库</Tag>
            ) : (
              <Tag color="blue" icon={<BookOutlined />} style={{ marginInlineEnd: 0 }}>文档树</Tag>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-0.5 truncate">
            {kb.description || (type === 'vector'
              ? '上传文档自动解析→切片→向量化；Agent/工作流 可用 kb_search 检索'
              : '绑定到 Agent 后，可用 kb_glob / kb_grep / kb_read 探索')}
          </p>
        </div>
        {/* 编辑按 knowledge:write 渲染（只读用户不显示，§7.6 权限原则） */}
        {canWrite && (
          <Button type="text" icon={<EditOutlined />} onClick={openEdit} title="编辑">
            编辑
          </Button>
        )}
      </div>

      {/* Body — fills remaining height; cards scroll internally */}
      <div className="flex-1 min-h-0">
        {type === 'vector' ? <KbVectorDetail kb={kb} /> : <KbTreeDetail kb={kb} />}
      </div>

      {/* Edit modal */}
      <Modal
        title="编辑知识库"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editName.trim() && editM.mutate()}
        okText="保存"
        cancelText="取消"
        confirmLoading={editM.isPending}
        okButtonProps={{ disabled: !editName.trim() }}
      >
        <div className="space-y-4 py-2">
          <div>
            <label className="block text-xs text-gray-500 mb-1">名称 *</label>
            <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="知识库名称" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">描述</label>
            <Input.TextArea
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
              placeholder="这个知识库涵盖哪些内容…"
              rows={3}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
