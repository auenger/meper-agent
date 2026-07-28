/**
 * KbVectorDetail — vector-type KB content (documents + retrieval test).
 *
 * Pure content component (no Drawer shell) — rendered inside
 * KnowledgeDetailPage. Layout: left document list, right retrieval test.
 *
 * - Document list (name / status / progress / chunks) with polling while any
 *   doc is in-flight (pending/parsing/embedding).
 * - Upload (pdf/docx/md/txt) → fires async indexing on backend.
 * - Retrieval test (query → top-k chunks with score + source).
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Table, Tag, Progress, Button, Space, Upload, Input, List,
  Empty, Popconfirm, App as AntdApp, Tooltip,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { UploadProps } from 'antd'
import {
  UploadOutlined, ReloadOutlined, DeleteOutlined, SearchOutlined, FileTextOutlined,
} from '@ant-design/icons'
import {
  knowledgeApi, knowledgeKeys,
  type KnowledgeBase, type KbDocument, type KbDocStatus, type KbSearchResultItem,
} from '../../services/knowledge-api'

/* ─── Helpers ─── */

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const STATUS_META: Record<KbDocStatus, { label: string; color: string }> = {
  pending: { label: '排队中', color: 'default' },
  parsing: { label: '解析中', color: 'gold' },
  embedding: { label: '向量化', color: 'blue' },
  completed: { label: '已完成', color: 'green' },
  failed: { label: '失败', color: 'red' },
}

const IN_FLIGHT: KbDocStatus[] = ['pending', 'parsing', 'embedding']

export default function KbVectorDetail({ kb }: { kb: KnowledgeBase }) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KbSearchResultItem[] | null>(null)

  /* ─── Documents (poll while indexing) ─── */
  const docsQ = useQuery({
    queryKey: knowledgeKeys.documents(kb.id),
    queryFn: () => knowledgeApi.listDocuments(kb.id),
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => IN_FLIGHT.includes(d.parse_status)) ? 3000 : false,
  })
  const docs = docsQ.data?.items ?? []

  /* ─── Mutations ─── */
  const uploadM = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.uploadDocuments(kb.id, files),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kb.id) })
      if (res.errors.length) message.warning(`${res.errors.length} 个文件上传失败`)
      else if (res.created.length) message.success(`已上传 ${res.created.length} 个文件，后台索引中…`)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '上传失败'),
  })

  const deleteDocM = useMutation({
    mutationFn: (docId: string) => knowledgeApi.deleteDocument(kb.id, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kb.id) })
      message.success('文档已删除')
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  })

  const reindexM = useMutation({
    mutationFn: (docId: string) => knowledgeApi.reindexDocument(kb.id, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kb.id) })
      message.success('已重新派发索引任务')
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '重试失败'),
  })

  const searchM = useMutation({
    mutationFn: () => knowledgeApi.search(kb.id, query, 5),
    onSuccess: (res) => setSearchResults(res.results),
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '检索失败'),
  })

  const uploadProps: UploadProps = {
    accept: '.pdf,.docx,.md,.markdown,.txt',
    multiple: true,
    showUploadList: false,
    customRequest: ({ file, onSuccess: onOk }) => {
      uploadM.mutate([file as File])
      onOk?.({}, file as File)
    },
  }

  /* ─── Doc table columns ─── */
  const columns: ColumnsType<KbDocument> = [
    {
      title: '文件名',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, r) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <FileTextOutlined className="text-gray-400" />
            <span className="font-medium text-gray-900 text-sm">{name}</span>
          </Space>
          <span className="text-xs text-gray-400">
            {r.file_type.toUpperCase()} · {formatSize(r.file_size)}
            {r.chunk_count > 0 && ` · ${r.chunk_count} 切片`}
          </span>
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 180,
      render: (_: unknown, r: KbDocument) => {
        const meta = STATUS_META[r.parse_status] ?? STATUS_META.pending
        const inFlight = IN_FLIGHT.includes(r.parse_status)
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.label}</Tag>
            {inFlight && <Progress percent={r.parse_progress} size="small" showInfo={false} />}
            {r.parse_status === 'failed' && r.parse_error && (
              <Tooltip title={r.parse_error}>
                <span className="text-xs text-red-500 truncate max-w-[160px] inline-block">
                  {r.parse_error}
                </span>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: '',
      key: 'action',
      width: 80,
      render: (_: unknown, r: KbDocument) => (
        <Space size={2}>
          {r.parse_status === 'failed' && (
            <Tooltip title="重新索引">
              <Button
                size="small" type="text"
                icon={<ReloadOutlined spin={reindexM.isPending} />}
                onClick={() => reindexM.mutate(r.id)}
              />
            </Tooltip>
          )}
          <Popconfirm
            title="删除文档" description={`删除 ${r.name}？该文档的所有切片和向量将被清除。`}
            okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
            onConfirm={() => deleteDocM.mutate(r.id)}
          >
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="flex gap-5" style={{ height: 'calc(100vh - 220px)' }}>
      {/* Documents */}
      <div className="flex-1 flex flex-col rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50 shrink-0">
          <span className="font-medium text-gray-900 text-sm">文档 ({docs.length})</span>
          <Space>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => docsQ.refetch()}>刷新</Button>
            <Upload {...uploadProps}>
              <Button type="primary" size="small" icon={<UploadOutlined />} loading={uploadM.isPending}>
                上传
              </Button>
            </Upload>
          </Space>
        </div>
        <div className="flex-1 overflow-auto">
          <Table<KbDocument>
            rowKey="id"
            columns={columns}
            dataSource={docs}
            loading={docsQ.isLoading}
            size="small"
            pagination={false}
            showHeader={false}
            locale={{ emptyText: <Empty description="暂无文档，上传 PDF/Word/Markdown 开始" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          />
        </div>
      </div>

      {/* Retrieval test */}
      <div className="flex-1 flex flex-col rounded-lg border border-gray-200 bg-white overflow-hidden">
        <div className="px-3 py-2 border-b border-gray-100 bg-gray-50 shrink-0">
          <span className="font-medium text-gray-900 text-sm flex items-center gap-1.5">
            <SearchOutlined className="text-gray-400" /> 检索测试
          </span>
        </div>
        <div className="p-3 shrink-0">
          <Input.Search
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入查询文本…"
            enterButton="检索"
            loading={searchM.isPending}
            onSearch={() => query.trim() && searchM.mutate()}
          />
        </div>
        <div className="flex-1 overflow-auto px-3 pb-3">
          {searchResults !== null && (
            <List<KbSearchResultItem>
              size="small"
              locale={{ emptyText: '无匹配结果（可能文档尚未索引完成或阈值过高）' }}
              dataSource={searchResults}
              renderItem={(item) => (
                <List.Item className="!px-0">
                  <div className="w-full rounded-lg border border-gray-100 p-2.5 bg-gray-50/50">
                    <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                      <span className="truncate">{item.source_file}{item.page ? ` · P${item.page}` : ''}</span>
                      <Tag color="green" style={{ marginInlineEnd: 0 }}>{item.score.toFixed(3)}</Tag>
                    </div>
                    <p className="text-sm text-gray-700 leading-relaxed line-clamp-4">{item.text}</p>
                  </div>
                </List.Item>
              )}
            />
          )}
        </div>
      </div>
    </div>
  )
}
