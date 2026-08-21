/**
 * 个人技能详情页 — 与官方详情页（skill-detail-page.tsx）完全同构（§7.6）。
 * 同样的面包屑 + 标题区 + 文件树 + SkillFileEditor（skillKind="personal"）。
 */
import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Breadcrumb, Popconfirm, Spin, Empty, Button, Tag } from 'antd'
import { DeleteOutlined, HomeOutlined, LeftOutlined, SendOutlined } from '@ant-design/icons'
import { userSkillsApi, userSkillKeys } from '../services/user-skills-api'
import type { SkillFileTreeNode } from '../services/tools-api'
import SkillFileTree from '../components/skill-file-tree'
import SkillFileEditor from '../components/skill-file-editor'

export default function UserSkillDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [selectedPath, setSelectedPath] = useState<string | null>('SKILL.md')

  const { data: skill, isLoading } = useQuery({
    queryKey: userSkillKeys.detail(id!),
    queryFn: () => userSkillsApi.get(id!),
    enabled: !!id,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: userSkillKeys.detail(id!) })
    queryClient.invalidateQueries({ queryKey: userSkillKeys.list() })
  }

  const publishMutation = useMutation({
    mutationFn: () => userSkillsApi.submit(id!),
    onSuccess: () => {
      message.success('已提交审核（管理员通过后进入技能广场）')
      invalidate()
    },
    onError: (e: Error) => message.error(`提交失败：${e.message}`),
  })

  const deleteMutation = useMutation({
    mutationFn: () => userSkillsApi.remove(id!),
    onSuccess: () => {
      message.success('已删除')
      navigate('/skills', { state: { tab: 'mine' } })
    },
  })

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-[60vh]">
        <Spin size="large" />
      </div>
    )
  }

  if (!skill) {
    return (
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="技能不存在" className="py-20">
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

  const isOwn = skill.source === 'own'
  // 个人技能单文件模型：文件树恒为一个 SKILL.md 节点
  const fileTree: SkillFileTreeNode[] = [
    { key: 'SKILL.md', title: 'SKILL.md', is_leaf: true, size: skill.content?.length ?? 0 },
  ]
  const statusLabel =
    skill.status === 'private'
      ? '私有'
      : skill.status === 'submitted'
        ? '审核中'
        : skill.status === 'published'
          ? '已发布'
          : '已隐藏'

  return (
    <div className="animate-[fadeIn_0.3s_ease-out] h-full flex flex-col">
      {/* Header —— 与官方详情页同构 */}
      <div className="flex items-center justify-between pb-4 mb-4 border-b border-gray-200">
        <div className="flex-1">
          {/* Breadcrumb */}
          <Breadcrumb
            items={[
              { title: <HomeOutlined className="text-gray-400" /> },
              {
                title: (
                  <span
                    onClick={() => navigate('/skills', { state: { tab: 'mine' } })}
                    className="cursor-pointer hover:text-blue-500"
                  >
                    我的技能
                  </span>
                ),
              },
              { title: skill.name },
            ]}
            className="mb-3"
          />

          {/* Title + meta */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <h1 className="text-2xl font-bold text-[#0F172A] truncate max-w-lg" title={skill.name}>{skill.name}</h1>
                <Tag color="geekblue">{statusLabel}</Tag>
                {skill.derived_from_name && <Tag color="purple">fork 自 {skill.derived_from_name}</Tag>}
                {skill.source === 'installed' && <Tag>安装</Tag>}
              </div>
              <p className="text-sm text-[#64748B] mb-3">{skill.description || '（无描述）'}</p>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#94A3B8]">{skill.stats?.load_count ?? 0} 次真实加载</span>
                <span className="text-xs text-[#94A3B8]">
                  {isOwn ? '个人技能 · 跨 Agent 生效' : '安装的技能（随作者更新）'}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {skill.status === 'private' && isOwn && (
                <Popconfirm
                  title="提交发布审核？"
                  description="管理员审核通过后将进入技能广场。"
                  onConfirm={() => publishMutation.mutate()}
                >
                  <Button icon={<SendOutlined />}>发布</Button>
                </Popconfirm>
              )}
              {isOwn && (
                <Popconfirm
                  title="删除该技能？"
                  description="磁盘文件与元数据将一并删除，不可恢复。"
                  onConfirm={() => deleteMutation.mutate()}
                >
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              )}
              <Button
                icon={<LeftOutlined />}
                onClick={() => navigate('/skills', { state: { tab: 'mine' } })}
              >
                返回列表
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Main layout —— 与官方详情页同构：文件树 + 编辑器 */}
      <div className="flex-1 flex overflow-hidden gap-4 min-h-0">
        {/* Left: file tree（个人技能单文件：SKILL.md） */}
        <div
          className="w-1/3 rounded-lg border border-gray-200 bg-white overflow-hidden flex flex-col"
          style={{ minHeight: '400px' }}
        >
          <div className="px-4 py-2 border-b border-gray-100 font-medium text-sm">文件目录</div>
          <SkillFileTree
            tree={fileTree}
            isLoading={false}
            selectedPath={selectedPath}
            onSelect={setSelectedPath}
          />
        </div>

        {/* Right: file editor（personal 源：保存走 user-skills API） */}
        <div
          className="flex-1 rounded-lg border border-gray-200 bg-white overflow-hidden flex flex-col"
          style={{ minHeight: '400px' }}
        >
          <SkillFileEditor toolId={id!} filePath={selectedPath} skillKind="personal" />
        </div>
      </div>
    </div>
  )
}
