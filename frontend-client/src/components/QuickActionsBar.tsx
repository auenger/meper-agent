import { ShrinkOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { RecommendedItem } from '../types'

const QUICK_ACTIONS_GAP = 6

interface QuickActionsBarProps {
  items: RecommendedItem[]
  disabled: boolean
  onSelect: (item: RecommendedItem) => void
}

/**
 * 快捷操作条：默认单行展示，放不下的按钮收纳进行尾「⋯」；
 * 点击「⋯」在容器内展开为多行平铺（无弹出层），点击行尾收缩图标还原单行。
 * 单行可容纳数量由隐藏量尺行按真实按钮宽度测得，随容器宽度自适应。
 */
export function QuickActionsBar({ items, disabled, onSelect }: QuickActionsBarProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const ghostRefs = useRef(new Map<number, HTMLButtonElement>())
  const [expanded, setExpanded] = useState(false)
  const [visibleCount, setVisibleCount] = useState(items.length)

  const registerGhost = useCallback((index: number) => {
    return (el: HTMLButtonElement | null) => {
      if (el) ghostRefs.current.set(index, el)
      else ghostRefs.current.delete(index)
    }
  }, [])

  const measure = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const total = items.length
    const moreGhost = ghostRefs.current.get(-1)
    const moreWidth = moreGhost ? moreGhost.offsetWidth + QUICK_ACTIONS_GAP : 0
    const available = container.clientWidth
    let used = 0
    let fit = 0
    for (let index = 0; index < total; index++) {
      const ghost = ghostRefs.current.get(index)
      if (!ghost) break
      const width = ghost.offsetWidth
      const nextUsed = fit === 0 ? width : used + QUICK_ACTIONS_GAP + width
      // 后面还有按钮放不下时，行尾要给「⋯」预留位置
      const reserve = index < total - 1 ? moreWidth : 0
      if (nextUsed + reserve > available) break
      used = nextUsed
      fit = index + 1
    }
    setVisibleCount(fit)
  }, [items])

  useEffect(() => {
    setExpanded(false)
  }, [items])

  useLayoutEffect(() => {
    if (!expanded) measure()
  }, [expanded, measure])

  useEffect(() => {
    const container = containerRef.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (!expanded) measure()
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [expanded, measure])

  if (items.length === 0) return null

  const collapsed = !expanded
  const hiddenCount = items.length - visibleCount

  return (
    <div
      ref={containerRef}
      className="composer-quick-actions"
      data-collapsed={collapsed || undefined}
    >
      <div className="quick-actions-measure" aria-hidden>
        {items.map((item, index) => (
          <Button
            key={`measure:${index}:${item.label}`}
            className="quick-action"
            ref={registerGhost(index)}
            tabIndex={-1}
          >
            {item.label}
          </Button>
        ))}
        <Button className="quick-action quick-action-more" ref={registerGhost(-1)} tabIndex={-1}>
          ⋯
        </Button>
      </div>
      {items.map((item, index) => {
        if (collapsed && index >= visibleCount) return null
        return (
          <Button
            key={`${index}:${item.label}`}
            className="quick-action"
            disabled={disabled}
            onClick={() => {
              setExpanded(false)
              onSelect(item)
            }}
          >
            {item.label}
          </Button>
        )
      })}
      {collapsed && hiddenCount > 0 ? (
        <Button
          className="quick-action quick-action-more"
          aria-label="展开更多快捷操作"
          disabled={disabled}
          onClick={() => setExpanded(true)}
        >
          ⋯
        </Button>
      ) : null}
      {expanded ? (
        <Button
          className="quick-action quick-action-collapse"
          aria-label="收起快捷操作"
          title="收起"
          disabled={disabled}
          onClick={() => setExpanded(false)}
        >
          <ShrinkOutlined />
        </Button>
      ) : null}
    </div>
  )
}
