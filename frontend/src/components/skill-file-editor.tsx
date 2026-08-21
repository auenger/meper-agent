/**
 * Skill file editor — TextArea-based Markdown editor with save functionality.
 *
 * 双源（§7.6 物理双库）：skillKind="official"（默认）走 tools API；
 * skillKind="personal"（usk_*）走 user-skills API——个人技能单文件模型，
 * filePath 固定 SKILL.md，保存为整体 content 更新。
 */
import { useState, useEffect } from 'react'
import { Button, Input, message, Spin } from 'antd'
import { SaveOutlined, UndoOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toolsApi, toolKeys } from '../services/tools-api'
import { userSkillsApi, userSkillKeys } from '../services/user-skills-api'

const { TextArea } = Input

interface SkillFileEditorProps {
  toolId: string
  filePath: string | null
  /** official=tools 表（默认）| personal=user_skills（usk_*，§7.6） */
  skillKind?: 'official' | 'personal'
}

interface FileContentShape {
  content: string
  path: string
}

export default function SkillFileEditor({
  toolId,
  filePath,
  skillKind = 'official',
}: SkillFileEditorProps) {
  const queryClient = useQueryClient()
  const [localContent, setLocalContent] = useState('')
  const [isDirty, setIsDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  const isPersonal = skillKind === 'personal'

  // Fetch file content（个人技能走 user-skills 详情的 content 字段）
  const { data: fileData, isLoading } = useQuery<FileContentShape>({
    queryKey: isPersonal
      ? userSkillKeys.detail(toolId)
      : toolKeys.fileContent(toolId, filePath ?? ''),
    queryFn: async () => {
      if (isPersonal) {
        const detail = await userSkillsApi.get(toolId)
        return { content: detail.content ?? '', path: filePath ?? 'SKILL.md' }
      }
      return toolsApi.getFileContent(toolId, filePath!)
    },
    enabled: isPersonal ? !!toolId : !!filePath,
  })

  // Sync local state when data loads or filePath changes
  useEffect(() => {
    if (fileData?.content !== undefined) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalContent(fileData.content)
      setIsDirty(false)
    }
  }, [fileData?.content, filePath])

  const handleSave = async () => {
    if (!filePath && !isPersonal) return

    setSaving(true)
    try {
      if (isPersonal) {
        await userSkillsApi.update(toolId, { content: localContent })
      } else {
        await toolsApi.updateFileContent(toolId, filePath!, localContent)
      }
      message.success('文件保存成功（下一轮对话生效）')
      setIsDirty(false)
      // Invalidate queries to refresh data
      if (isPersonal) {
        queryClient.invalidateQueries({ queryKey: userSkillKeys.detail(toolId) })
        queryClient.invalidateQueries({ queryKey: userSkillKeys.list() })
      } else {
        queryClient.invalidateQueries({ queryKey: toolKeys.detail(toolId) })
        queryClient.invalidateQueries({ queryKey: toolKeys.fileContent(toolId, filePath!) })
      }
    } catch (err: unknown) {
      const error = err as { message?: string }
      message.error(error?.message ?? '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    if (fileData) {
      setLocalContent(fileData.content)
      setIsDirty(false)
    }
  }

  if (!filePath && !isPersonal) {
    return (
      <div className="flex items-center justify-center h-full text-[#94A3B8]">
        <p>请从左侧选择一个文件查看</p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spin />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100">
        <span className="text-sm font-medium text-[#0F172A]">{filePath ?? 'SKILL.md'}</span>
        <div className="flex gap-2">
          <Button
            size="small"
            icon={<UndoOutlined />}
            onClick={handleReset}
            disabled={!isDirty}
          >
            撤销修改
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={saving}
            disabled={!isDirty}
          >
            保存
          </Button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 p-4 overflow-hidden">
        <TextArea
          value={localContent}
          onChange={(e) => {
            setLocalContent(e.target.value)
            setIsDirty(true)
          }}
          className="h-full !resize-none !font-mono !text-sm"
          style={{ minHeight: '100%' }}
          placeholder="文件内容..."
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100 text-xs text-[#94A3B8]">
        <span>{localContent.length} 字符</span>
        {isDirty && <span className="text-amber-500">有未保存的修改</span>}
      </div>
    </div>
  )
}
