/**
 * KbTreeDetail — tree-type KB content (file tree + editor).
 *
 * Pure content component (no Drawer shell) — rendered inside
 * KnowledgeDetailPage. Layout: left file tree, right editor.
 *
 * - File tree via getFileTree (antd Tree)
 * - Click a file → load content (getFileContent) into the editor
 * - Save (updateFileContent) / delete (deleteFile)
 * - Upload .md files (preserves relative paths)
 *
 * The editor is isolated in <FileEditor key={path} /> so switching files
 * remounts it — the draft state resets cleanly without effect-setState.
 */
import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Tree, Input, Button, Space, Upload, Empty, Spin, Popconfirm, App as AntdApp,
} from 'antd'
import type { UploadProps } from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  FolderOutlined, FileTextOutlined, SaveOutlined, DeleteOutlined, UploadOutlined,
} from '@ant-design/icons'
import {
  knowledgeApi, knowledgeKeys,
  type KnowledgeBase, type KbFileTreeNode,
} from '../../services/knowledge-api'

/* ─── Convert backend tree → antd DataNode ─── */
function toTreeData(nodes: KbFileTreeNode[]): DataNode[] {
  return nodes.map((n) => ({
    key: n.key,
    title: (
      <Space size={4}>
        {n.is_leaf ? <FileTextOutlined className="text-gray-400" /> : <FolderOutlined className="text-amber-500" />}
        <span>{n.title}</span>
      </Space>
    ),
    isLeaf: n.is_leaf,
    children: n.children ? toTreeData(n.children) : undefined,
  }))
}

function firstLeafPath(nodes: KbFileTreeNode[]): string | null {
  for (const n of nodes) {
    if (n.is_leaf) return n.key
    if (n.children) {
      const found = firstLeafPath(n.children)
      if (found) return found
    }
  }
  return null
}

/* ─── Editor for one file (remounts on path change via key) ─── */
function FileEditor({
  kbId,
  path,
  initialContent,
}: {
  kbId: string
  path: string
  initialContent: string
}) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(initialContent)
  const dirty = draft !== initialContent

  const saveM = useMutation({
    mutationFn: () => knowledgeApi.updateFileContent(kbId, path, draft),
    onSuccess: () => {
      message.success('已保存')
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.fileContent(kbId, path) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kbId) })
      // Re-sync: the mutation left draft as-is; content cache is invalidated so
      // a re-read would return the saved value. dirty is derived, so once the
      // cache refetches initialContent matches draft again.
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '保存失败'),
  })

  const deleteFileM = useMutation({
    mutationFn: () => knowledgeApi.deleteFile(kbId, path),
    onSuccess: () => {
      message.success('文件已删除')
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kbId) })
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
  })

  return (
    <div className="flex-1 flex flex-col rounded-lg border border-gray-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
        <span className="text-xs font-mono text-gray-600 truncate">{path}</span>
        <Space size={4}>
          <Button
            size="small" type="primary" icon={<SaveOutlined />}
            loading={saveM.isPending} disabled={!dirty}
            onClick={() => saveM.mutate()}
          >
            保存
          </Button>
          <Popconfirm
            title="删除文件" description={`删除 ${path}？`}
            okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
            onConfirm={() => deleteFileM.mutate()}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      </div>
      <Input.TextArea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="flex-1 !resize-none !border-0 !rounded-none font-mono text-xs"
        style={{ height: '100%' }}
        spellCheck={false}
      />
    </div>
  )
}

/* ─── Main ─── */
export default function KbTreeDetail({ kb }: { kb: KnowledgeBase }) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  const treeQ = useQuery({
    queryKey: knowledgeKeys.files(kb.id),
    queryFn: () => knowledgeApi.getFileTree(kb.id),
  })

  // Effective selected path: explicit user selection, else auto-pick the first
  // leaf once the tree loads (derived — no effect-setState needed).
  const effectivePath = useMemo(() => {
    if (selectedPath) return selectedPath
    return treeQ.data?.files?.length ? firstLeafPath(treeQ.data.files) : null
  }, [selectedPath, treeQ.data])

  const contentQ = useQuery({
    queryKey: knowledgeKeys.fileContent(kb.id, effectivePath ?? ''),
    queryFn: () => knowledgeApi.getFileContent(kb.id, effectivePath!),
    enabled: !!effectivePath,
  })

  const uploadM = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.uploadDocuments(kb.id, files),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kb.id) })
      if (res.errors.length) message.warning(`${res.errors.length} 个文件上传失败`)
      else if (res.created.length) message.success(`已上传 ${res.created.length} 个文件`)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '上传失败'),
  })

  const uploadProps: UploadProps = {
    accept: '.md,.markdown',
    multiple: true,
    showUploadList: false,
    customRequest: ({ file, onSuccess: onOk }) => {
      uploadM.mutate([file as File])
      onOk?.({}, file as File)
    },
  }

  return (
    <div>
      {/* Upload bar */}
      <div className="mb-3">
        <Upload {...uploadProps}>
          <Button icon={<UploadOutlined />} loading={uploadM.isPending}>上传 .md 文件</Button>
        </Upload>
      </div>

      <div className="flex gap-3" style={{ height: 'calc(100vh - 220px)' }}>
        {/* File tree */}
        <div className="w-56 shrink-0 overflow-auto rounded-lg border border-gray-200 p-2 bg-white">
          {treeQ.isLoading ? (
            <div className="flex justify-center py-8"><Spin /></div>
          ) : treeQ.data?.files?.length ? (
            <Tree
              treeData={toTreeData(treeQ.data.files)}
              selectedKeys={effectivePath ? [effectivePath] : []}
              onSelect={(keys) => keys[0] && setSelectedPath(String(keys[0]))}
              defaultExpandAll
              blockNode
            />
          ) : (
            <Empty description="暂无文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </div>

        {/* Editor (remounts per file via key) */}
        {effectivePath ? (
          contentQ.isLoading ? (
            <div className="flex-1 flex items-center justify-center"><Spin /></div>
          ) : contentQ.data ? (
            <FileEditor
              key={effectivePath}
              kbId={kb.id}
              path={effectivePath}
              initialContent={contentQ.data.content}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
              文件不存在
            </div>
          )
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
            选择左侧文件查看内容
          </div>
        )}
      </div>
    </div>
  )
}
