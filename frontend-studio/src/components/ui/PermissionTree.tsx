/**
 * PermissionTree — 两层权限树选择器（模块 → 权限点）。
 *
 * 用于「新建/编辑角色」弹窗，替代扁平 checkbox 列表，让权限按"菜单资源"
 * 分组选择。数据源是 services/types.ts 的 PERMISSION_GROUPS（模块=菜单资源
 * → 权限点叶子），与后端 ALL_PERMISSION_KEYS 一致（见 adapters 注释）。
 *
 * - 父节点（模块）可展开/折叠，checkbox 父子联动：
 *     勾父 → 全选子；子全选 → 父选中；子部分选 → 父半选(indeterminate)。
 * - 受控：selected: Set<string> / onChange(next)。
 * - 配色复用项目暗色 hex 体系（#121214/#18181b/#27272a/slate-*），index.css
 *   的 .theme-light 覆写自动适配亮色；选中态用项目蓝 #1E5EFF + 白字。
 */
import { useState } from 'react'
import { ChevronRight, Check, Minus } from 'lucide-react'
import { PERMISSION_GROUPS } from '../../services/types'

interface PermissionTreeProps {
  selected: Set<string>
  onChange: (next: Set<string>) => void
}

/** 自定义 checkbox，支持半选(indeterminate)态。双主题。 */
function TriState({
  checked,
  indeterminate,
  onClick,
}: {
  checked: boolean
  indeterminate: boolean
  onClick: () => void
}) {
  const on = checked || indeterminate
  return (
    <button
      type="button"
      onClick={onClick}
      aria-checked={checked}
      className={`w-3.5 h-3.5 rounded-[4px] border flex items-center justify-center shrink-0 cursor-pointer transition-colors ${
        on
          ? 'bg-[#1E5EFF] border-[#1E5EFF] text-white'
          : 'bg-[#121214] border-[#27272a] hover:border-[#52525b]'
      }`}
    >
      {indeterminate ? <Minus className="w-2.5 h-2.5" /> : checked ? <Check className="w-2.5 h-2.5" /> : null}
    </button>
  )
}

/** 单个模块节点：展开/折叠 + 全选 checkbox + 叶子权限列表。 */
function ModuleNode({
  label,
  keys,
  selected,
  onToggleMany,
}: {
  label: string
  keys: string[]
  selected: Set<string>
  onToggleMany: (keys: string[], on: boolean) => void
}) {
  const [open, setOpen] = useState(true)
  const checkedCount = keys.filter((k) => selected.has(k)).length
  const allChecked = checkedCount === keys.length
  const someChecked = checkedCount > 0 && !allChecked

  const toggleParent = () => onToggleMany(keys, !allChecked)

  return (
    <div className="rounded-md border border-[#27272a] bg-[#121214] overflow-hidden">
      <div className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-[#18181b] transition-colors">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="text-[#71717a] hover:text-[#fafafa] cursor-pointer transition-colors"
        >
          <ChevronRight className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-90' : ''}`} />
        </button>
        <TriState checked={allChecked} indeterminate={someChecked} onClick={toggleParent} />
        <button
          type="button"
          onClick={toggleParent}
          className="flex-1 text-left text-[11px] font-semibold text-slate-300 cursor-pointer hover:text-[#fafafa] transition-colors"
        >
          {label}
        </button>
        <span className="text-[10px] text-[#71717a] font-mono">{checkedCount}/{keys.length}</span>
      </div>
      {open && (
        <div className="px-2 pb-2 pl-8 grid grid-cols-2 gap-x-2 gap-y-1">
          {keys.map((k) => {
            const on = selected.has(k)
            return (
              <label
                key={k}
                className="flex items-center gap-1.5 text-[10px] text-slate-400 cursor-pointer hover:text-slate-300 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onToggleMany([k], !on)}
                  className="accent-[#1E5EFF] cursor-pointer w-3 h-3"
                />
                <span className="font-mono truncate">{k}</span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function PermissionTree({ selected, onChange }: PermissionTreeProps) {
  const toggleMany = (keys: string[], on: boolean) => {
    const next = new Set(selected)
    for (const k of keys) {
      if (on) next.add(k)
      else next.delete(k)
    }
    onChange(next)
  }

  const groups = Object.entries(PERMISSION_GROUPS)
  const total = groups.reduce((n, [, ks]) => n + ks.length, 0)

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between px-1">
        <span className="text-[10px] text-[#71717a]">按菜单资源分组</span>
        <span className="text-[10px] text-[#71717a] font-mono">已选 {selected.size}/{total}</span>
      </div>
      <div className="max-h-56 overflow-y-auto space-y-1.5 pr-0.5 scrollbar-custom">
        {groups.map(([label, keys]) => (
          <ModuleNode
            key={label}
            label={label}
            keys={keys}
            selected={selected}
            onToggleMany={toggleMany}
          />
        ))}
      </div>
    </div>
  )
}

export default PermissionTree
