/**
 * KbVectorDetail — vector-type KB content (documents + retrieval test).
 *
 * Layout mirrors frontend-studio's KbVectorDetailPage: a two-column grid
 * where the document list and retrieval test render as flat card rows
 * (no nested bordered panels). Pure content component.
 *
 * - Document list: flat rows (name / type / status badge / progress / actions)
 *   with polling while any doc is in-flight.
 * - Upload (pdf/docx/md/txt) via inline label tag.
 * - Retrieval test: input + result cards (text + score + source).
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Popconfirm, Modal, Spin, App as AntdApp, Segmented, Image as AntdImage } from 'antd'
import {
  UploadOutlined, ReloadOutlined, DeleteOutlined, SearchOutlined, FileTextOutlined,
  EyeOutlined, LoadingOutlined as LoaderIcon, CheckCircleFilled, CloseCircleFilled,
} from '@ant-design/icons'
import {
  knowledgeApi, knowledgeKeys,
  type KnowledgeBase, type KbDocument, type KbDocStatus, type KbChunkStrategy,
  type KbSearchResultItem, type KbChunkItem,
} from '../../services/knowledge-api'
import { useAuthStore } from '../../stores/auth-store'

/* ─── helpers ─── */

/** Build the download URL for a stored image FileRef. */
function imageUrl(fileId: string): string {
  return `/api/v1/files/${encodeURIComponent(fileId)}/download`
}

/* ─── helpers ─── */

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const STATUS_META: Record<KbDocStatus, { label: string; cls: string; icon: typeof CheckCircleFilled }> = {
  pending: { label: '排队中', cls: 'text-gray-400', icon: LoaderIcon },
  parsing: { label: '解析中', cls: 'text-amber-500', icon: LoaderIcon },
  embedding: { label: '向量化', cls: 'text-blue-500', icon: LoaderIcon },
  completed: { label: '已完成', cls: 'text-emerald-500', icon: CheckCircleFilled },
  failed: { label: '失败', cls: 'text-rose-500', icon: CloseCircleFilled },
}

const IN_FLIGHT: KbDocStatus[] = ['pending', 'parsing', 'embedding']

/* ─── doc row ─── */

function DocRow({
  doc,
  onReindex,
  onDelete,
  onView,
  reindexing,
  canWrite,
}: {
  doc: KbDocument
  onReindex: () => void
  onDelete: () => void
  onView: () => void
  reindexing: boolean
  canWrite: boolean
}) {
  const meta = STATUS_META[doc.parse_status] ?? STATUS_META.pending
  const inFlight = IN_FLIGHT.includes(doc.parse_status)
  const Icon = meta.icon
  return (
    <div className="bg-surface rounded-lg p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <FileTextOutlined style={{ fontSize: 14 }} className="text-gray-400" />
            <span className="text-xs font-semibold text-gray-900 truncate">{doc.name}</span>
            <span className={`inline-flex items-center gap-1 text-[10px] font-bold ${meta.cls} shrink-0`}>
              <Icon spin={inFlight} style={{ fontSize: 11 }} />
              {meta.label}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono mt-0.5 ml-5">
            <span>{doc.file_type.toUpperCase()}</span>
            <span>{formatSize(doc.file_size)}</span>
            {doc.chunk_count > 0 && <span>{doc.chunk_count} 切片</span>}
            <span className="text-indigo-500">
              {doc.chunk_strategy === 'structure' ? '结构切分' : '递归切分'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {doc.parse_status === 'completed' && (
            <button
              onClick={onView}
              className="p-1 rounded text-gray-400 hover:text-blue-500 hover:bg-blue-50 transition cursor-pointer"
              title="查看切片内容"
            >
              <EyeOutlined style={{ fontSize: 13 }} />
            </button>
          )}
          {canWrite && doc.parse_status === 'failed' && (
            <button
              onClick={onReindex}
              className="p-1 rounded text-amber-500 hover:text-amber-600 hover:bg-amber-50 transition cursor-pointer"
              title="重新索引"
            >
              <ReloadOutlined spin={reindexing} style={{ fontSize: 13 }} />
            </button>
          )}
          {canWrite && (
            <Popconfirm
              title="删除文档" description={`删除 ${doc.name}？该文档的所有切片和向量将被清除。`}
              okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
              onConfirm={onDelete}
            >
              <button
                className="p-1 rounded text-rose-500 hover:text-rose-600 hover:bg-rose-50 transition cursor-pointer"
                title="删除"
              >
                <DeleteOutlined style={{ fontSize: 13 }} />
              </button>
            </Popconfirm>
          )}
        </div>
      </div>
      {inFlight && (
        <div className="h-1 bg-line-2 rounded-full overflow-hidden ml-5">
          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${doc.parse_progress}%` }} />
        </div>
      )}
      {doc.parse_status === 'failed' && doc.parse_error && (
        <p className="text-[10px] text-rose-500 break-all ml-5">{doc.parse_error}</p>
      )}
    </div>
  )
}

/* ─── main ─── */

export default function KbVectorDetail({ kb }: { kb: KnowledgeBase }) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KbSearchResultItem[] | null>(null)
  const [viewDoc, setViewDoc] = useState<KbDocument | null>(null)
  const [chunkStrategy, setChunkStrategy] = useState<KbChunkStrategy>('recursive')
  // §7.6 权限原则：只读用户不显示上传/删除/重索引（切分策略仅上传时生效，一并隐藏）
  const canWrite = useAuthStore((s) => (s.user?.permissions ?? []).includes('knowledge:write'))

  // Chunks for the currently-viewed document (read-only viewer).
  const chunksQ = useQuery({
    queryKey: ['kb-doc-chunks', viewDoc?.id],
    queryFn: () => knowledgeApi.getDocumentChunks(kb.id, viewDoc!.id),
    enabled: !!viewDoc,
  })

  const docsQ = useQuery({
    queryKey: knowledgeKeys.documents(kb.id),
    queryFn: () => knowledgeApi.listDocuments(kb.id),
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => IN_FLIGHT.includes(d.parse_status)) ? 3000 : false,
  })
  const docs = docsQ.data?.items ?? []

  const uploadM = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.uploadDocuments(kb.id, files, chunkStrategy),
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

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length) uploadM.mutate(files)
    e.currentTarget.value = ''
  }

  return (
    <div className="flex gap-4 h-full min-h-0">
      {/* Documents card */}
      <div className="flex-1 flex flex-col rounded-xl bg-white shadow-sm overflow-hidden min-w-0">
        <div className="flex items-center justify-between px-3 py-2.5 bg-surface/60 shrink-0">
          <h3 className="text-xs font-bold text-gray-900 flex items-center gap-2">
            <FileTextOutlined style={{ fontSize: 14 }} className="text-emerald-500" />
            文档 ({docs.length})
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => docsQ.refetch()}
              className="p-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition cursor-pointer"
              title="刷新"
            >
              <ReloadOutlined spin={docsQ.isFetching} style={{ fontSize: 14 }} />
            </button>
            {canWrite && (
              <>
                <Segmented
                  size="small"
                  value={chunkStrategy}
                  onChange={(v) => setChunkStrategy(v as KbChunkStrategy)}
                  options={[
                    { label: '递归切分', value: 'recursive' },
                    { label: '结构切分', value: 'structure' },
                  ]}
                />
                <label className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] bg-emerald-600 hover:bg-emerald-500 text-white font-semibold cursor-pointer transition">
                  {uploadM.isPending ? <LoaderIcon spin style={{ fontSize: 13 }} /> : <UploadOutlined style={{ fontSize: 13 }} />}
                  上传
                  <input type="file" accept=".pdf,.docx,.md,.markdown,.txt" multiple className="hidden" onChange={handleUpload} />
                </label>
              </>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {docs.length === 0 ? (
            <div className="text-center py-12 text-xs text-gray-400">
              还没有文档，点击右上角上传 PDF/Word/Markdown。
            </div>
          ) : (
            <div className="space-y-2">
              {docs.map((doc) => (
                <DocRow
                  key={doc.id}
                  doc={doc}
                  onReindex={() => reindexM.mutate(doc.id)}
                  onDelete={() => deleteDocM.mutate(doc.id)}
                  onView={() => setViewDoc(doc)}
                  reindexing={reindexM.isPending}
                  canWrite={canWrite}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Retrieval test card */}
      <div className="flex-1 flex flex-col rounded-xl bg-white shadow-sm overflow-hidden min-w-0">
        <div className="px-3 py-2.5 bg-surface/60 shrink-0">
          <h3 className="text-xs font-bold text-gray-900 flex items-center gap-2">
            <SearchOutlined style={{ fontSize: 14 }} className="text-blue-500" />
            检索测试
          </h3>
        </div>
        <div className="p-3 shrink-0">
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && query.trim()) searchM.mutate() }}
              placeholder="输入查询文本…"
              className="flex-1 px-3 py-2 bg-surface border border-line rounded-lg text-gray-800 text-xs focus:outline-none focus:border-blue-500 transition"
            />
            <button
              onClick={() => query.trim() && searchM.mutate()}
              disabled={searchM.isPending || !query.trim()}
              className="px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold cursor-pointer transition flex items-center gap-1"
            >
              {searchM.isPending ? <LoaderIcon spin style={{ fontSize: 13 }} /> : <SearchOutlined style={{ fontSize: 13 }} />}
              检索
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {searchResults !== null && (
            <div className="space-y-2">
              {searchResults.length === 0 ? (
                <p className="text-xs text-gray-400 py-6 text-center">无匹配结果（可能文档尚未索引完成或阈值过高）。</p>
              ) : (
                searchResults.map((r, i) => (
                  <div key={i} className="bg-surface rounded-lg p-3 space-y-1.5">
                    <div className="flex items-center justify-between text-[10px] font-mono">
                      <span className="text-gray-500 truncate">
                        {r.source_file}
                        {r.page ? ` · P${r.page}` : ''}
                        {r.section ? ` · ${r.section}` : ''}
                      </span>
                      <span className="text-emerald-500 shrink-0 ml-2">{r.score.toFixed(3)}</span>
                    </div>
                    <p className="text-xs text-gray-700 leading-relaxed line-clamp-4">{r.text}</p>
                    {r.image_ref_ids && r.image_ref_ids.length > 0 && (
                      <div className="flex gap-1.5 mt-1 flex-wrap">
                        {r.image_ref_ids.map((fid) => (
                          <AntdImage
                            key={fid}
                            src={imageUrl(fid)}
                            className="rounded border border-line object-cover"
                            width={48}
                            height={48}
                            preview={{ mask: false }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* View parsed chunks modal (read-only) */}
      <Modal
        title={`切片内容 — ${viewDoc?.name ?? ''}`}
        open={!!viewDoc}
        onCancel={() => setViewDoc(null)}
        footer={null}
        width={720}
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        {chunksQ.isLoading ? (
          <div className="flex justify-center py-8"><Spin /></div>
        ) : (chunksQ.data?.length ?? 0) === 0 ? (
          <p className="text-center text-gray-400 py-8 text-sm">暂无切片</p>
        ) : (
          <div className="space-y-3">
            {chunksQ.data!.map((c: KbChunkItem) => (
              <div key={c.chunk_index} className="bg-surface rounded-lg p-3">
                <div className="flex items-center justify-between text-[10px] font-mono text-gray-600 mb-1">
                  <span>切片 #{c.chunk_index + 1}{c.section ? ` · ${c.section}` : ''}</span>
                  {c.page && <span>第 {c.page} 页</span>}
                </div>
                <p className="text-xs text-gray-900 leading-relaxed whitespace-pre-wrap font-mono">
                  {c.text}
                </p>
                {c.image_ref_ids && c.image_ref_ids.length > 0 && (
                  <div className="flex gap-1.5 mt-2 flex-wrap">
                    <span className="text-[10px] text-gray-400 w-full mb-0.5">关联图片:</span>
                    {c.image_ref_ids.map((fid) => (
                      <AntdImage
                        key={fid}
                        src={imageUrl(fid)}
                        className="rounded border border-line object-cover"
                        width={64}
                        height={64}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
