/**
 * Skill detail page — file tree + editor layout.
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Breadcrumb, Spin, Empty } from 'antd'
import { HomeOutlined, LeftOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { toolsApi, toolKeys } from '../services/tools-api'
import SkillFileTree from '../components/skill-file-tree'
import SkillFileEditor from '../components/skill-file-editor'

export default function SkillDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  // Fetch tool details
  const { data: tool, isLoading: toolLoading } = useQuery({
    queryKey: toolKeys.detail(id!),
    queryFn: () => toolsApi.get(id!),
    enabled: !!id,
  })

  // Fetch file tree
  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: toolKeys.files(id!),
    queryFn: () => toolsApi.getFileTree(id!),
    enabled: !!id,
  })

  if (toolLoading) {
    return (
      <div className="flex justify-center items-center h-[60vh]">
        <Spin size="large" />
      </div>
    )
  }

  if (!tool) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="工具不存在"
        className="py-20"
      >
        <button
          type="button"
          className="px-4 py-2 rounded border border-gray-300 bg-white hover:bg-gray-50 text-sm"
          onClick={() => navigate('/skills')}
        >
          返回列表
        </button>
      </Empty>
    )
  }

  const isDirectory = tool.files.length > 0

  return (
    <div className="animate-[fadeIn_0.3s_ease-out] h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 mb-4 border-b border-gray-200">
        <div className="flex-1">
          {/* Breadcrumb */}
          <Breadcrumb
            items={[
              { title: <HomeOutlined className="text-gray-400" /> },
              { title: <span onClick={() => navigate('/skills')} className="cursor-pointer hover:text-blue-500">Skill 管理</span> },
              { title: tool.name },
            ]}
            className="mb-3"
          />

          {/* Title + meta */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-[#0F172A] mb-2 truncate max-w-lg" title={tool.name}>{tool.name}</h1>
              <p className="text-sm text-[#64748B] mb-3">{tool.description}</p>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#94A3B8]">v{tool.version}</span>
                {isDirectory && (
                  <span className="text-xs text-[#94A3B8]">{tool.files.length} 文件</span>
                )}
              </div>
            </div>
            <button
              type="button"
              className="px-4 py-2 rounded border border-gray-300 bg-white hover:bg-gray-50 text-sm flex items-center gap-2"
              onClick={() => navigate('/skills')}
            >
              <LeftOutlined />
              返回列表
            </button>
          </div>
        </div>
      </div>

      {/* Main layout */}
      {isDirectory ? (
        // Directory mode: file tree + editor
        <div className="flex-1 flex overflow-hidden gap-4 min-h-0">
          {/* Left: file tree */}
          <div
            className="w-1/3 rounded-lg border border-gray-200 bg-white overflow-hidden flex flex-col"
            style={{ minHeight: '400px' }}
          >
            <div className="px-4 py-2 border-b border-gray-100 font-medium text-sm">文件目录</div>
            <SkillFileTree
              tree={treeData?.files}
              isLoading={treeLoading}
              selectedPath={selectedPath}
              onSelect={setSelectedPath}
            />
          </div>

          {/* Right: file editor */}
          <div
            className="flex-1 rounded-lg border border-gray-200 bg-white overflow-hidden flex flex-col"
            style={{ minHeight: '400px' }}
          >
            <SkillFileEditor toolId={tool.id} filePath={selectedPath} />
          </div>
        </div>
      ) : (
        // Single file mode: show instructions directly
        <div
          className="flex-1 rounded-lg border border-gray-200 bg-white p-6 overflow-auto"
          style={{ minHeight: '400px' }}
        >
          <h2 className="text-lg font-medium mb-4">工具说明</h2>
          <div className="prose prose-sm max-w-none">
            <pre className="whitespace-pre-wrap font-sans text-sm">{tool.instructions}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
