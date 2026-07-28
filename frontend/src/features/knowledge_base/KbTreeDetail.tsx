/**
 * KbTreeDetail — Drawer for a tree-type KB (Markdown file tree).
 *
 * Left: file tree (getFileTree), click a file to load content.
 * Right: editor (TextArea), save (updateFileContent) / delete file.
 * Top: upload .md files (preserves relative paths).
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Drawer, Tree, Input, Button, Space, Upload, Empty, Spin, Popconfirm, App as AntdApp,
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

/* ─── Convert backend tree → antd TreeDataNode ─── */
function toTreeData(nodes: KbFileTreeNode[]): DataNode[] {
  return nodes.map((n) => ({
    key: n.key,
    title: (
      <Space size={4}>
        {n.is_leaf ? <FileTextOutlined className="text-slate-400" /> : <FolderOutlined className="text-amber-500" />}
        <span>{n.title}</span>
      </Space>
    ),
    isLeaf: n.is_leaf,
    children: n.children ? toTreeData(n.children) : undefined,
  }))
}

/* ─── Collect all leaf paths (for selection default) ─── */
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

export default function KbTreeDetail({
  kb,
  onClose,
}: {
  kb: KnowledgeBase
  onClose: () => void
}) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)

  /* ─── File tree ─── */
  const treeQ = useQuery({
    queryKey: knowledgeKeys.files(kb.id),
    queryFn: () => knowledgeApi.getFileTree(kb.id),
  })

  // Auto-select first leaf on load
  if (!selectedPath && treeQ.data?.files?.length) {
    const first = firstLeafPath(treeQ.data.files)
    if (first) setSelectedPath(first)
  }

  /* ─── File content ─── */
  const contentQ = useQuery({
    queryKey: knowledgeKeys.fileContent(kb.id, selectedPath ?? ''),
    queryFn: () => knowledgeApi.getFileContent(kb.id, selectedPath!),
    enabled: !!selectedPath,
  })

  // Sync loaded content into draft (when selection changes or content loads)
  if (selectedPath && contentQ.data && !dirty) {
    if (draft !== contentQ.data.content) setDraft(contentQ.data.content)
  }

  /* ─── Mutations ─── */
  const saveM = useMutation({
    mutationFn: () => knowledgeApi.updateFileContent(kb.id, selectedPath!, draft),
    onSuccess: () => {
      message.success('已保存')
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.fileContent(kb.id, selectedPath!) })
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kb.id) })
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '保存失败'),
  })

  const deleteFileM = useMutation({
    mutationFn: (path: string) => knowledgeApi.deleteFile(kb.id, path),
    onSuccess: () => {
      message.success('文件已删除')
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.files(kb.id) })
      setSelectedPath(null)
      setDraft('')
      setDirty(false)
    },
    onError: (e: unknown) => message.error(e instanceof Error ? e.message : '删除失败'),
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

  const handleSelect = (path: string) => {
    if (dirty && selectedPath !== path) {
      // Discard unsaved changes silently on switch (could prompt, but keep simple)
      setDirty(false)
    }
    setSelectedPath(path)
    setDirty(false)
  }

  return (
    <Drawer
      title={
        <Space>
          <span>{kb.name}</span>
          <span className="text-xs text-slate-400">文档树</span>
        </Space>
      }
      placement="right"
      width={760}
      open
      onClose={onClose}
    >
      {/* Upload bar */}
      <div className="mb-3">
        <Upload {...uploadProps}>
          <Button icon={<UploadOutlined />} loading={uploadM.isPending}>
            上传 .md 文件
          </Button>
        </Upload>
      </div>

      <div className="flex gap-3" style={{ height: 'calc(100vh - 160px)' }}>
        {/* File tree */}
        <div className="w-56 shrink-0 overflow-auto rounded-lg border border-gray-200 p-2 bg-white">
          {treeQ.isLoading ? (
            <div className="flex justify-center py-8"><Spin /></div>
          ) : treeQ.data?.files?.length ? (
            <Tree
              treeData={toTreeData(treeQ.data.files)}
              selectedKeys={selectedPath ? [selectedPath] : []}
              onSelect={(keys) => keys[0] && handleSelect(String(keys[0]))}
              defaultExpandAll
              blockNode
            />
          ) : (
            <Empty description="暂无文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </div>

        {/* Editor */}
        <div className="flex-1 flex flex-col rounded-lg border border-gray-200 bg-white overflow-hidden">
          {selectedPath ? (
            <>
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
                <span className="text-xs font-mono text-slate-600 truncate">{selectedPath}</span>
                <Space size={4}>
                  <Button
                    size="small"
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={saveM.isPending}
                    disabled={!dirty}
                    onClick={() => saveM.mutate()}
                  >
                    保存
                  </Button>
                  <Popconfirm
                    title="删除文件"
                    description={`删除 ${selectedPath}？`}
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    cancelText="取消"
                    onConfirm={() => deleteFileM.mutate(selectedPath)}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>
              <Input.TextArea
                value={draft}
                onChange={(e) => { setDraft(e.target.value); setDirty(true) }}
                className="flex-1 !resize-none !border-0 !rounded-none font-mono text-xs"
                style={{ height: '100%' }}
                spellCheck={false}
              />
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
              选择左侧文件查看内容
            </div>
          )}
        </div>
      </div>
    </Drawer>
  )
}
