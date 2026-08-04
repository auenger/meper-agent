/**
 * AvatarRender — 统一的 agent 头像渲染入口。
 *
 * - 图片 URL（以 `/` 或 `http(s)://` 开头）→ <img object-cover>，填满容器
 * - emoji / 文本字符（旧数据兼容）→ <span>，按 textClassName 控制字号
 * - 空 → 默认 logo /AFLogo.png（object-contain，留白居中）
 *
 * 覆盖：BotAvatar（对话消息/输入框/会话头）、AgentDetailPage / AgentSpace
 * （列表 tile）、AvatarField（编辑页预览）。
 */
import type { FC } from 'react'

export function isAvatarImageUrl(a?: string | null): boolean {
  return !!a && (a.startsWith('/') || a.startsWith('http://') || a.startsWith('https://'))
}

interface AvatarRenderProps {
  value?: string | null
  /** 容器/尺寸 class，套到所有分支（如 "w-full h-full"）。 */
  className?: string
  /** emoji 文本字号 class（仅 emoji 分支生效，如 "text-2xl"）。 */
  textClassName?: string
}

export const AvatarRender: FC<AvatarRenderProps> = ({ value, className = '', textClassName = '' }) => {
  if (isAvatarImageUrl(value)) {
    return <img src={value!} alt="avatar" className={`${className} object-cover`} draggable={false} />
  }
  if (value && value.length > 0) {
    return (
      <span className={`${className} inline-flex items-center justify-center leading-none ${textClassName}`}>
        {value}
      </span>
    )
  }
  return (
    <img src="/AFLogo.png" alt="bot" className={`${className} object-contain select-none`} draggable={false} />
  )
}

export default AvatarRender
