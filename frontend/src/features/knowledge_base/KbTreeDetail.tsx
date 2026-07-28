/**
 * KbTreeDetail — tree-type KB content (file tree + editor).
 *
 * Layout mirrors frontend-studio's KbDetailPage:
 *   - Left: file tree (native recursive render) with an inline upload tag.
 *   - Right: file editor (bare textarea + inline toolbar: undo / delete / save).
 *
 * Pure content component rendered inside KnowledgeDetailPage.
 */
import { useMemo, useState, type FC } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Popconfirm, App as AntdApp } from 'antd'
import {
  FolderOutlined as FolderIcon, FileTextOutlined as FileTextIcon, SaveOutlined as SaveIcon,
  DeleteOutlined as TrashIcon, UndoOutlined as UndoIcon, UploadOutlined as UploadIcon,
  LoadingOutlined as LoaderIcon,
} from '@ant-design/icons'
import {
  knowledgeApi, knowledgeKeys,
  type KnowledgeBase, type KbFileTreeNode,
} from '../../services/knowledge-api'

/* ─── tree helpers ─── */

function firstLeaf(nodes: KbFileTreeNode[]): string | null {
  for (const n of nodes) {
    if (n.is_leaf) return n.key
    if (n.children) {
      const f = firstLeaf(n.children)
      if (f) return f
    }
  }
  return null
}

/** Recursive tree row (folders expandable, files selectable). */
const KbTreeRow: FC<{
  node: KbFileTreeNode
  depth: number
  selected: string | null
  onSelect: (path: string) => void
}> = ({ node, depth, selected, onSelect }) => {
  const [open, setOpen] = useState(true)
  const pad = { paddingLeft: `${depth * 12 + 8}px` }

  if (!node.is_leaf) {
    return (
      <div>
        <button
          onClick={() => setOpen((o) => !o)}
          style={pad}
          className="w-full flex items-center gap-1.5 py-1 text-xs text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded transition cursor-pointer"
        >
          <FolderIcon style={{ fontSize: 14, color: '#f59e0b' }} />
          <span className="truncate font-medium">{node.title}</span>
        </button>
        {open && node.children?.map((child) => (
          <KbTreeRow key={child.key} node={child} depth={depth + 1} selected={selected} onSelect={onSelect} />
        ))}
      </div>
    )
  }

  const isSel = selected === node.key
  return (
    <button
      onClick={() => onSelect(node.key)}
      style={pad}
      className={`w-full flex items-center gap-1.5 py-1 text-xs rounded transition cursor-pointer ${
        isSel ? 'bg-blue-50 text-blue-600' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
      }`}
    >
      <FileTextIcon style={{ fontSize: 14, color: '#9ca3af' }} />
      <span className="truncate font-mono">{node.title}</span>
    </button>
  )
}

/* ─── file editor (remounts per file via key) ─── */

function KbFileEditor({ kbId, filePath, initialContent }: {
  kbId: string
  filePath: string
  initialContent: string
}) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [local, setLocal] = useState(initialContent)
  const isDirty = local !== initialContent

  const saveM = useMutation({
    mutationFn: () => knowledgeApi.updateFileContent(kbId, filePath, local),
    onSuccess: () => {
      message.success('已保存')
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.fileContent(kbId, filePath) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kbId) })
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '保存失败'),
  })

  const deleteM = useMutation({
    mutationFn: () => knowledgeApi.deleteFile(kbId, filePath),
    onSuccess: () => {
      message.success('文件已删除')
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kbId) })
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  })

  return (
    <div className="h-full flex flex-col rounded-xl border border-gray-200 bg-white overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100 shrink-0">
        <span className="text-[11px] font-mono text-gray-500 truncate">{filePath}</span>
        <div className="flex items-center gap-2">
          {isDirty && <span className="text-[10px] text-amber-500 font-semibold">未保存</span>}
          <button
            onClick={() => setLocal(initialContent)}
            disabled={!isDirty}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-gray-500 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-40 transition cursor-pointer"
          >
            <UndoIcon style={{ fontSize: 12 }} /> 撤销
          </button>
          <Popconfirm
            title="删除文件" description={`删除 ${filePath}？`}
            okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
            onConfirm={() => deleteM.mutate()}
          >
            <button
              disabled={deleteM.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-red-500 hover:bg-red-50 disabled:opacity-40 transition cursor-pointer"
            >
              {deleteM.isPending ? <LoaderIcon spin style={{ fontSize: 12 }} /> : <TrashIcon style={{ fontSize: 12 }} />}
              删除
            </button>
          </Popconfirm>
          <button
            onClick={() => saveM.mutate()}
            disabled={!isDirty || saveM.isPending}
            className="flex items-center gap-1 px-3 py-1 rounded-lg text-[11px] bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40 transition cursor-pointer font-semibold"
          >
            {saveM.isPending ? <LoaderIcon spin style={{ fontSize: 12 }} /> : <SaveIcon style={{ fontSize: 12 }} />}
            保存
          </button>
        </div>
      </div>

      {/* Editor */}
      <textarea
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        className="flex-1 w-full p-4 bg-transparent text-gray-800 font-mono text-xs leading-relaxed resize-none focus:outline-none"
        spellCheck={false}
      />
      <div className="px-4 py-1.5 border-t border-gray-100 text-[10px] text-gray-400 shrink-0">{local.length} 字符</div>
    </div>
  )
}

/* ─── main ─── */

export default function KbTreeDetail({ kb }: { kb: KnowledgeBase }) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  const { data: treeData, isLoading } = useQuery({
    queryKey: knowledgeKeys.files(kb.id),
    queryFn: () => knowledgeApi.getFileTree(kb.id),
  })

  // Derived initial selection (no effect-setState).
  const effectivePath = useMemo(() => {
    if (selectedPath) return selectedPath
    return treeData?.files?.length ? firstLeaf(treeData.files) : null
  }, [selectedPath, treeData])

  const contentQ = useQuery({
    queryKey: knowledgeKeys.fileContent(kb.id, effectivePath ?? ''),
    queryFn: () => knowledgeApi.getFileContent(kb.id, effectivePath!),
    enabled: !!effectivePath,
  })

  const uploadM = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.uploadDocuments(kb.id, files),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kb.id) })
      if (res.errors.length) message.warning(`${res.created.length} 成功 / ${res.errors.length} 失败：${res.errors[0].error}`)
      else message.success(`已上传 ${res.created.length} 个文件`)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '上传失败'),
  })

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length) uploadM.mutate(files)
    e.currentTarget.value = ''
  }

  return (
    <div className="flex gap-4 h-[calc(100vh-180px)] min-h-[400px]">
      {/* File tree + upload */}
      <div className="w-72 shrink-0 rounded-xl border border-gray-200 bg-white overflow-y-auto p-3">
        <div className="flex items-center justify-between mb-2 px-1">
          <span className="text-[11px] text-gray-400 font-semibold">文件</span>
          <label className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] bg-blue-600 hover:bg-blue-500 text-white cursor-pointer font-semibold transition">
            {uploadM.isPending ? <LoaderIcon spin style={{ fontSize: 12 }} /> : <UploadIcon style={{ fontSize: 12 }} />}
            上传
            <input type="file" accept=".md,.markdown" multiple className="hidden" onChange={handleUpload} />
          </label>
        </div>
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-gray-400">
            <LoaderIcon spin style={{ fontSize: 16, marginRight: 8 }} /> 加载文件…
          </div>
        ) : (treeData?.files ?? []).length === 0 ? (
          <p className="text-[11px] text-gray-400 px-1 py-4 text-center">还没有文件，点击「上传」添加 .md</p>
        ) : (
          <div className="space-y-0.5">
            {(treeData?.files ?? []).map((node) => (
              <KbTreeRow key={node.key} node={node} depth={0} selected={effectivePath} onSelect={setSelectedPath} />
            ))}
          </div>
        )}
      </div>

      {/* Editor */}
      <div className="flex-1 min-w-0">
        {effectivePath ? (
          contentQ.isLoading ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              <LoaderIcon spin style={{ fontSize: 16, marginRight: 8 }} /> 加载…
            </div>
          ) : contentQ.data ? (
            <KbFileEditor key={effectivePath} kbId={kb.id} filePath={effectivePath} initialContent={contentQ.data.content} />
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm border border-gray-200 rounded-xl bg-white">
              文件不存在
            </div>
          )
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm border border-gray-200 rounded-xl bg-white">
            选择左侧文件查看内容
          </div>
        )}
      </div>
    </div>
  )
}
