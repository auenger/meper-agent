/**
 * Native Tailwind UI primitives — drop-in replacements for the antd components
 * used by the migrated workflow-editor module.
 *
 * Kept intentionally small: each mirrors only the props actually consumed by
 * the workflow editor (Input / Input.TextArea / Select / Button / Modal /
 * Table / Switch / Tag / Tooltip / Popover / Badge / Spin / Popconfirm).
 *
 * Styling is pure Tailwind className + inline styles; no antd design tokens.
 *
 * Dark-native: surfaces use the AgentForge dark palette (bg-[#18181b] /
 * bg-[#121214] / border-[#27272a] / text-[#fafafa] / text-slate-*). Light mode
 * is handled by the .theme-light override block in index.css, which maps these
 * same classes to light values — so no per-component theme branching is needed.
 */
import {
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  useCallback,
  forwardRef,
  type ReactNode,
  type CSSProperties,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
  type ButtonHTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { X } from 'lucide-react'
import { createPortal } from 'react-dom'

/* ─── Input ─── */

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  size?: 'small' | 'middle' | 'large'
  status?: 'error' | undefined
}

const SIZE_CLASS: Record<NonNullable<InputProps['size']>, string> = {
  small: 'h-7 text-xs px-2',
  middle: 'h-8 text-xs px-2.5',
  large: 'h-10 text-sm px-3',
}

function InputInner({ size = 'middle', status, className = '', ...rest }: InputProps) {
  return (
    <input
      {...rest}
      className={`w-full rounded-md border bg-[#121214] text-[#fafafa] placeholder:text-[#52525b]
        focus:outline-none focus:border-[#1E5EFF] focus:ring-1 focus:ring-[#1E5EFF]/30 transition-colors
        ${status === 'error' ? 'border-red-400' : 'border-[#27272a]'}
        ${SIZE_CLASS[size]} ${className}`}
    />
  )
}

/* Input.TextArea — accessed as <Input.TextArea>, supports ref forwarding */
interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'size'> {
  rows?: number
}

const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { rows = 3, className = '', ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      {...rest}
      rows={rows}
      className={`w-full rounded-md border border-[#27272a] bg-[#121214] text-[#fafafa]
        placeholder:text-[#52525b] px-2.5 py-1.5 text-xs leading-relaxed resize-y
        focus:outline-none focus:border-[#1E5EFF] focus:ring-1 focus:ring-[#1E5EFF]/30 transition-colors
        ${className}`}
    />
  )
})

/**
 * Input — styled text input. Also exposes Input.TextArea.
 * Typed as a callable function with a TextArea static property.
 */
type InputComponent = typeof InputInner & {
  TextArea: typeof TextArea
}

const Input = InputInner as InputComponent
Input.TextArea = TextArea

export { Input }

/* ─── Select ─── */

export interface SelectOption {
  value: string
  label: ReactNode
  /** Optional leading icon. */
  icon?: ReactNode
  /** Optional subtitle / description shown beneath the label. */
  description?: string
  disabled?: boolean
}

export interface SelectOptionGroup {
  label: string
  options: SelectOption[]
}

interface SelectProps {
  value?: string | null
  onChange?: (value: string | null) => void
  /** Flat option list (mutually exclusive with `groups`). */
  options?: SelectOption[]
  /** Grouped options (mutually exclusive with `options`). */
  groups?: SelectOptionGroup[]
  placeholder?: string
  size?: 'small' | 'middle'
  className?: string
  allowClear?: boolean
  /** Inline search box in the dropdown. Defaults to `true` (post-upgrade behavior). */
  showSearch?: boolean
  filterOption?: (input: string, option: SelectOption) => boolean
  loading?: boolean
  disabled?: boolean
}

/** Default filter: case-insensitive label substring match. */
const defaultFilter = (input: string, opt: SelectOption): boolean => {
  const label = typeof opt.label === 'string' ? opt.label : String(opt.label ?? '')
  const hay = (label + ' ' + (opt.description ?? '') + ' ' + opt.value).toLowerCase()
  return hay.includes(input.trim().toLowerCase())
}

export function Select({
  value,
  onChange,
  options = [],
  groups,
  placeholder = '请选择',
  size = 'middle',
  className = '',
  allowClear = false,
  showSearch = true,
  filterOption = defaultFilter,
  disabled = false,
}: SelectProps) {
  // Normalize groups + flat options into a single grouped structure for rendering.
  const sourceGroups: SelectOptionGroup[] =
    groups ?? (options.length ? [{ label: '', options }] : [])

  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [hover, setHover] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1) // keyboard highlight over the flat filtered list
  // Panel position (fixed, portaled to the theme root). Starts off-screen
  // until measured to avoid a flash before useLayoutEffect runs.
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({ top: -9999, left: -9999, visibility: 'hidden' })
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Portal to the theme root (.theme-light/.theme-dark), not document.body:
  // (a) escapes any ancestor `overflow: hidden` (e.g. modal panels) so options
  // aren't clipped, and (b) stays inside the theme subtree so index.css's
  // `.theme-light .xxx` overrides can recolor it. Portaling to body would
  // escape the theme scope and leave the panel dark under the light theme.
  // Same approach as Tooltip/Popover below.
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null)
  useEffect(() => {
    setPortalTarget(document.querySelector<HTMLElement>('.theme-light, .theme-dark'))
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    setPanelStyle({
      position: 'fixed',
      top: rect.bottom + 4,
      left: rect.left,
      width: rect.width,
      zIndex: 9999,
    })
  }, [open])

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node)) return
      if (panelRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    // Close on scroll/resize: we position the panel once on open rather than
    // tracking the trigger, so any layout shift collapses it instead of
    // leaving a detached panel. But ignore scrolls that originate *inside*
    // the panel — that's the option list scrolling (user wheel, or
    // scrollIntoView moving the highlighted option into view). Closing on
    // those would make long option lists flicker and become unselectable.
    const onScroll = (e: Event) => {
      if (panelRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  // Reset transient state whenever the panel opens/closes.
  useEffect(() => {
    if (open) {
      setSearch('')
      const flat = sourceGroups.flatMap((g) => g.options)
      const idx = flat.findIndex((o) => o.value === value)
      setActiveIndex(idx >= 0 ? idx : flat.findIndex((o) => !o.disabled))
      // focus the search input on open
      requestAnimationFrame(() => searchRef.current?.focus())
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const filterFn = showSearch ? (filterOption ?? defaultFilter) : null
  const filteredGroups = filterFn
    ? sourceGroups
        .map((g) => ({ ...g, options: g.options.filter((o) => filterFn(search, o)) }))
        .filter((g) => g.options.length > 0)
    : sourceGroups
  const flatFiltered = filteredGroups.flatMap((g) => g.options)
  const hasMatch = flatFiltered.length > 0

  const selected = sourceGroups.flatMap((g) => g.options).find((o) => o.value === value)
  const displayNode = selected ? (
    <span className="flex items-center gap-1.5 min-w-0">
      {selected.icon && <span className="shrink-0">{selected.icon}</span>}
      <span className="truncate">{selected.label}</span>
    </span>
  ) : null

  const clamp = (i: number) => {
    if (flatFiltered.length === 0) return -1
    return (i + flatFiltered.length) % flatFiltered.length
  }

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      let next = clamp(activeIndex + 1)
      // skip disabled
      while (flatFiltered[next]?.disabled && next !== activeIndex) next = clamp(next + 1)
      setActiveIndex(next)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      let prev = clamp(activeIndex - 1)
      while (flatFiltered[prev]?.disabled && prev !== activeIndex) prev = clamp(prev - 1)
      setActiveIndex(prev)
    } else if (e.key === 'Enter' && open) {
      e.preventDefault()
      const opt = flatFiltered[activeIndex]
      if (opt && !opt.disabled) {
        onChange?.(opt.value)
        setOpen(false)
      }
    } else if (e.key === 'Escape' && open) {
      e.preventDefault()
      setOpen(false)
    }
  }

  // Scroll the active option into view as the highlight moves.
  useEffect(() => {
    if (!open || activeIndex < 0) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${activeIndex}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  return (
    <div ref={ref} className={`relative ${disabled ? 'opacity-50 pointer-events-none' : ''} ${className}`}>
      <div
        onClick={() => !disabled && setOpen((v) => !v)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onKeyDown={onKeyDown}
        tabIndex={0}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        className={`w-full ${size === 'small' ? 'h-7 text-xs' : 'h-8 text-xs'} px-2.5
          rounded-md border border-[#27272a] bg-[#121214] flex items-center justify-between gap-1 cursor-pointer
          text-[#fafafa] hover:border-[#1E5EFF] focus:outline-none focus:border-[#1E5EFF] focus:ring-1 focus:ring-[#1E5EFF]/30 transition-colors`}
      >
        <span className={`flex-1 min-w-0 truncate ${value ? '' : 'text-[#52525b]'}`}>
          {displayNode ?? placeholder}
        </span>
        {allowClear && hover && value ? (
          <X
            className="w-3 h-3 text-[#71717a] hover:text-[#fafafa] shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              onChange?.(null)
              setOpen(false)
            }}
          />
        ) : (
          <svg className={`w-3 h-3 text-[#71717a] shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} viewBox="0 0 12 12" fill="none">
            <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>
      {open && createPortal(
        <div ref={panelRef} style={panelStyle} className="rounded-md border border-[#27272a] bg-[#18181b] shadow-lg overflow-hidden">
          {showSearch && (
            <div className="p-1.5 border-b border-[#27272a]">
              <input
                ref={searchRef}
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setActiveIndex(flatFiltered.findIndex((o) => !o.disabled))
                }}
                onKeyDown={onKeyDown}
                placeholder="搜索…"
                className="w-full h-7 px-2 rounded bg-[#121214] border border-[#27272a] text-xs text-[#fafafa]
                  placeholder:text-[#52525b] focus:outline-none focus:border-[#1E5EFF]"
              />
            </div>
          )}
          <div ref={listRef} role="listbox" className="max-h-56 overflow-y-auto py-0.5">
            {!hasMatch ? (
              <div className="px-2.5 py-2 text-[11px] text-[#71717a] text-center">无匹配项</div>
            ) : (
              (() => {
                let idx = -1
                return filteredGroups.map((g, gi) => (
                  <div key={gi}>
                    {g.label && (
                      <div className="px-2.5 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wider font-bold text-[#71717a]">
                        {g.label}
                      </div>
                    )}
                    {g.options.map((opt) => {
                      idx += 1
                      const myIdx = idx
                      const isSelected = opt.value === value
                      const isActive = myIdx === activeIndex
                      return (
                        <div
                          key={opt.value}
                          data-idx={myIdx}
                          role="option"
                          aria-selected={isSelected}
                          onClick={() => {
                            if (opt.disabled) return
                            onChange?.(opt.value)
                            setOpen(false)
                          }}
                          onMouseEnter={() => setActiveIndex(myIdx)}
                          className={`px-2.5 py-1.5 text-xs cursor-pointer flex items-start gap-1.5 text-[#fafafa]
                            ${opt.disabled ? 'opacity-40 cursor-not-allowed' : ''}
                            ${isActive && !opt.disabled ? 'bg-[#1E5EFF]/10' : ''}
                            ${isSelected ? 'text-[#1E5EFF] font-medium' : ''}`}
                        >
                          {opt.icon && <span className="shrink-0 mt-0.5">{opt.icon}</span>}
                          <span className="min-w-0 flex-1">
                            <span className="block truncate">{opt.label}</span>
                            {opt.description && (
                              <span className="block text-[10px] text-[#71717a] truncate">{opt.description}</span>
                            )}
                          </span>
                          {isSelected && (
                            <svg className="w-3 h-3 text-[#1E5EFF] shrink-0 mt-0.5" viewBox="0 0 12 12" fill="none">
                              <path d="M2.5 6.5L5 9L9.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          )}
                        </div>
                      )
                    })}
                  </div>
                ))
              })()
            )}
          </div>
        </div>,
        portalTarget ?? document.body,
      )}
    </div>
  )
}

/* ─── Button ─── */

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  type?: 'default' | 'primary' | 'dashed' | 'link' | 'text'
  danger?: boolean
  size?: 'small' | 'middle' | 'large'
  icon?: ReactNode
  loading?: boolean
}

const BTN_SIZE: Record<NonNullable<ButtonProps['size']>, string> = {
  small: 'h-7 text-[11px] px-2 gap-1',
  middle: 'h-8 text-xs px-3 gap-1.5',
  large: 'h-10 text-sm px-4 gap-1.5',
}

const BTN_TYPE: Record<NonNullable<ButtonProps['type']>, string> = {
  default: 'bg-[#18181b] border border-[#27272a] text-[#fafafa] hover:border-[#1E5EFF] hover:text-[#1E5EFF]',
  primary: 'bg-[#1E5EFF] border border-[#1E5EFF] text-white hover:bg-[#1a4fd6]',
  dashed: 'bg-[#18181b]/40 border border-dashed border-[#27272a] text-[#fafafa] hover:border-[#1E5EFF] hover:text-[#1E5EFF]',
  link: 'bg-transparent border-0 text-[#1E5EFF] hover:underline px-1',
  text: 'bg-transparent border-0 text-slate-400 hover:bg-[#121214]/60 px-1.5',
}

export function Button({
  type = 'default',
  danger = false,
  size = 'middle',
  icon,
  loading = false,
  className = '',
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const dangerClass = danger
    ? type === 'text' || type === 'link'
      ? 'text-red-500 hover:text-red-600'
      : 'bg-red-500 border-red-500 text-white hover:bg-red-600 border-red-600'
    : ''
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center rounded-md font-medium cursor-pointer transition-colors
        disabled:opacity-50 disabled:cursor-not-allowed select-none
        ${BTN_SIZE[size]} ${danger ? dangerClass : BTN_TYPE[type]} ${className}`}
    >
      {loading ? (
        <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="40 60" />
        </svg>
      ) : (
        icon
      )}
      {children}
    </button>
  )
}

/* ─── Modal ─── */

interface ModalProps {
  open: boolean
  title?: ReactNode
  onOk?: () => void
  onCancel?: () => void
  okText?: string
  cancelText?: string
  width?: number
  /** okButtonProps.danger: 确定按钮用红色（如删除确认） */
  okButtonProps?: { disabled?: boolean; danger?: boolean }
  destroyOnClose?: boolean
  children?: ReactNode
}

export function Modal({
  open,
  title,
  onOk,
  onCancel,
  okText = '确定',
  cancelText = '取消',
  width = 420,
  okButtonProps,
  children,
}: ModalProps) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center "
      onClick={onCancel}
    >
      <div
        style={{ width }}
        className="bg-[#18181b] rounded-xl shadow-2xl max-h-[85vh] flex flex-col border border-[#27272a]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#27272a]">
          <span className="text-sm font-medium text-[#fafafa]">{title}</span>
          <X className="w-4 h-4 text-[#71717a] cursor-pointer hover:text-[#fafafa]" onClick={onCancel} />
        </div>
        <div className="px-5 py-4 overflow-y-auto flex-1">{children}</div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-[#27272a]">
          <Button onClick={onCancel}>{cancelText}</Button>
          <Button
            type="primary"
            onClick={onOk}
            disabled={okButtonProps?.disabled}
            danger={okButtonProps?.danger}
          >
            {okText}
          </Button>
        </div>
      </div>
    </div>
  )
}

/* ─── Switch ─── */

interface SwitchProps {
  size?: 'small' | 'default'
  checked?: boolean
  onChange?: (checked: boolean) => void
}

export function Switch({ size = 'default', checked = false, onChange }: SwitchProps) {
  const w = size === 'small' ? 'w-7 h-4' : 'w-9 h-5'
  const knob = size === 'small' ? 'w-3 h-3' : 'w-4 h-4'
  const offset = size === 'small' ? (checked ? 'translate-x-3' : 'translate-x-0.5') : (checked ? 'translate-x-4' : 'translate-x-0.5')
  return (
    <button
      type="button"
      onClick={() => onChange?.(!checked)}
      className={`${w} rounded-full transition-colors cursor-pointer flex items-center
        ${checked ? 'bg-[#1E5EFF]' : 'bg-[#27272a]'}`}
    >
      {/* Knob stays white for contrast on the colored track in both themes */}
      <span className={`${knob} bg-white rounded-full shadow-sm transition-transform ${offset}`} />
    </button>
  )
}

/* ─── Tag ─── */

interface TagProps {
  color?: string
  closable?: boolean
  onClose?: () => void
  className?: string
  children?: ReactNode
  onClick?: () => void
}

const TAG_PRESETS: Record<string, string> = {
  blue: 'bg-blue-500/10 text-blue-400',
  processing: 'bg-blue-500/10 text-blue-400',
  success: 'bg-green-500/10 text-green-400',
  green: 'bg-green-500/10 text-green-400',
  warning: 'bg-amber-500/10 text-amber-400',
  error: 'bg-red-500/10 text-red-400',
  red: 'bg-red-500/10 text-red-400',
  orange: 'bg-orange-500/10 text-orange-400',
  purple: 'bg-purple-500/10 text-purple-400',
  cyan: 'bg-cyan-500/10 text-cyan-400',
  default: 'bg-[#18181b] text-slate-400',
}

export function Tag({ color, closable, onClose, className = '', children, onClick }: TagProps) {
  const preset = color && TAG_PRESETS[color] ? TAG_PRESETS[color] : ''
  // Custom hex color → inline badge style
  const isHex = color && /^#[0-9A-Fa-f]{3,8}$/.test(color)
  return (
    <span
      onClick={onClick}
      className={`inline-flex items-center gap-0.5 px-1.5 py-0 rounded text-[11px] leading-tight
        ${onClick ? 'cursor-pointer' : ''} ${preset || 'bg-[#18181b] text-slate-400'} ${className}`}
      style={
        isHex
          ? { backgroundColor: `${color}1A`, color }
          : undefined
      }
    >
      {children}
      {closable && (
        <X
          className="w-2.5 h-2.5 hover:opacity-70"
          onClick={(e) => {
            e.stopPropagation()
            onClose?.()
          }}
        />
      )}
    </span>
  )
}

/* ─── Tooltip / Popover ─── */

interface TooltipProps {
  title: ReactNode
  placement?: string
  children: ReactNode
}

export function Tooltip({ title, children }: TooltipProps) {
  const [show, setShow] = useState(false)
  const triggerRef = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null)
  // 与 Popover 同理：portal 到主题根，脱离父级 overflow 裁剪并回到 .theme-light 作用域
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null)
  useEffect(() => {
    setPortalTarget(document.querySelector<HTMLElement>('.theme-light, .theme-dark'))
  }, [])

  const measure = useCallback(() => {
    const trig = triggerRef.current
    if (!trig) return
    const r = trig.getBoundingClientRect()
    const w = tipRef.current?.offsetWidth ?? 160
    const h = tipRef.current?.offsetHeight ?? 0
    const GAP = 6
    const M = 8
    // 默认在触发器上方居中
    let top = r.top - h - GAP
    let left = r.left + r.width / 2 - w / 2
    // 上方放不下且下方够 → 翻到下方
    if (top < M && r.bottom + h + GAP <= window.innerHeight - M) {
      top = r.bottom + GAP
    }
    if (top + h > window.innerHeight - M) top = Math.max(M, window.innerHeight - h - M)
    if (top < M) top = M
    left = Math.max(M, Math.min(left, window.innerWidth - w - M))
    setCoords({ top, left })
  }, [])

  useEffect(() => {
    if (!show) return
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [show, measure])

  useLayoutEffect(() => {
    if (!show) return
    measure()
  }, [show, measure, title])

  return (
    <span
      ref={triggerRef}
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show &&
        title &&
        createPortal(
          <div
            ref={tipRef}
            style={
              coords
                ? { position: 'fixed', top: coords.top, left: coords.left, zIndex: 210 }
                : { position: 'fixed', left: -9999, top: -9999, visibility: 'hidden', zIndex: 210 }
            }
            className="px-2.5 py-1.5 rounded bg-[#18181b] border border-[#27272a] text-[#fafafa]
              text-[11px] whitespace-pre-wrap max-w-xs shadow-lg leading-relaxed pointer-events-none"
          >
            {title}
          </div>,
          portalTarget ?? document.body,
        )}
    </span>
  )
}

interface PopoverProps {
  content: ReactNode
  title?: ReactNode
  trigger?: 'click' | 'hover'
  open?: boolean
  onOpenChange?: (open: boolean) => void
  placement?: string
  children: ReactNode
}

export function Popover({ content, title, trigger = 'hover', open, onOpenChange, children }: PopoverProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = open !== undefined
  const isOpen = isControlled ? open : internalOpen

  const triggerRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<number | null>(null)
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null)
  // portal 到主题根容器（App root 上的 .theme-light/.theme-dark），让浮层回到主题作用域：
  // 否则脱离 .theme-light 后，index.css 的亮色覆写对浮层失效（亮色下仍显示暗色）。
  // fixed 定位相对视口，不会被该容器的 overflow-hidden 裁剪。
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null)
  useEffect(() => {
    setPortalTarget(document.querySelector<HTMLElement>('.theme-light, .theme-dark'))
  }, [])

  const toggle = (next: boolean) => {
    if (isControlled) onOpenChange?.(next)
    else setInternalOpen(next)
  }

  const clearCloseTimer = () => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }
  const scheduleClose = () => {
    clearCloseTimer()
    closeTimer.current = window.setTimeout(() => toggle(false), 120)
  }

  // 浮层 fixed 定位渲染到 body，脱离配置面板等祖先的 overflow 裁剪与层叠上下文
  const measure = useCallback(() => {
    const trig = triggerRef.current
    if (!trig) return
    const r = trig.getBoundingClientRect()
    const popW = popoverRef.current?.offsetWidth ?? 240
    const popH = popoverRef.current?.offsetHeight ?? 0
    const GAP = 4
    const M = 8
    // 默认右下展开：浮层右边对齐触发器右边，顶部贴触发器下方
    let top = r.bottom + GAP
    let left = r.right - popW
    // 下方放不下且上方够 → 翻到上方
    if (top + popH > window.innerHeight - M && r.top - popH - GAP >= M) {
      top = r.top - popH - GAP
    }
    // 垂直夹在视口内
    if (top + popH > window.innerHeight - M) top = Math.max(M, window.innerHeight - popH - M)
    if (top < M) top = M
    // 水平夹在视口内
    left = Math.max(M, Math.min(left, window.innerWidth - popW - M))
    setCoords({ top, left })
  }, [])

  // 打开时：初始定位 + 监听滚动(capture)/resize 实时跟随
  useEffect(() => {
    if (!isOpen) return
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [isOpen, measure])

  // 浮层挂载/内容变化后拿到真实尺寸再做边界翻转（paint 前同步，避免闪烁）
  useLayoutEffect(() => {
    if (!isOpen) return
    measure()
  }, [isOpen, measure, content, title])

  useEffect(() => () => clearCloseTimer(), [])

  // click 模式：点外部关闭（portal 浮层纳入判断，避免点浮层被误判为 outside）
  useEffect(() => {
    if (!isOpen || trigger !== 'click') return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (
        (triggerRef.current && triggerRef.current.contains(t)) ||
        (popoverRef.current && popoverRef.current.contains(t))
      ) {
        return
      }
      if (isControlled) onOpenChange?.(false)
      else setInternalOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [isOpen, trigger, isControlled, onOpenChange])

  const isHover = trigger === 'hover'
  const triggerProps = isHover
    ? {
        onMouseEnter: () => {
          clearCloseTimer()
          toggle(true)
        },
        onMouseLeave: scheduleClose,
      }
    : { onClick: () => toggle(!isOpen) }

  return (
    <div ref={triggerRef} className="relative inline-flex" {...triggerProps}>
      {children}
      {isOpen &&
        createPortal(
          <div
            ref={popoverRef}
            style={
              coords
                ? { position: 'fixed', top: coords.top, left: coords.left, zIndex: 200 }
                : { position: 'fixed', left: -9999, top: -9999, visibility: 'hidden', zIndex: 200 }
            }
            className="min-w-[220px] max-w-[360px] bg-[#18181b] rounded-lg shadow-xl border border-[#27272a]"
            {...(isHover
              ? { onMouseEnter: clearCloseTimer, onMouseLeave: scheduleClose }
              : {})}
          >
            {title && (
              <div className="px-3 py-2 border-b border-[#27272a] text-xs font-medium text-[#fafafa]">
                {title}
              </div>
            )}
            <div className="p-2.5">{content}</div>
          </div>,
          portalTarget ?? document.body,
        )}
    </div>
  )
}

/* ─── Badge ─── */

interface BadgeProps {
  count?: number
  size?: 'small' | 'default'
  className?: string
  style?: CSSProperties
  children?: ReactNode
}

export function Badge({ count, size = 'default', className = '', style, children }: BadgeProps) {
  const dot = size === 'small' ? 'min-w-[14px] h-[14px] text-[9px]' : 'min-w-[18px] h-[18px] text-[10px]'
  return (
    <span className={`relative inline-flex ${className}`}>
      {children}
      {count !== undefined && count > 0 && (
        <span
          className={`absolute -top-1 -right-1 ${dot} px-1 flex items-center justify-center
            rounded-full text-white font-medium leading-none`}
          style={{ backgroundColor: '#3B82F6', ...style }}
        >
          {count > 99 ? '99+' : count}
        </span>
      )}
    </span>
  )
}

/* ─── Spin ─── */

interface SpinProps {
  size?: 'small' | 'default' | 'large'
  className?: string
}

export function Spin({ size = 'default', className = '' }: SpinProps) {
  const s = size === 'small' ? 'w-3.5 h-3.5' : size === 'large' ? 'w-7 h-7' : 'w-5 h-5'
  return (
    <svg className={`${s} animate-spin text-[#1E5EFF] ${className}`} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path d="M12 2a10 10 0 0110 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

/* ─── Popconfirm ─── */

interface PopconfirmProps {
  title: ReactNode
  description?: ReactNode
  onConfirm?: () => void
  okText?: string
  cancelText?: string
  okButtonProps?: { danger?: boolean }
  children: ReactNode
}

export function Popconfirm({
  title,
  description,
  onConfirm,
  okText = '确定',
  cancelText = '取消',
  okButtonProps,
  children,
}: PopconfirmProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} className="relative inline-flex w-full">
      <span onClick={() => setOpen((v) => !v)} className="inline-flex w-full">
        {children}
      </span>
      {open && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-40 w-56 bg-[#18181b] rounded-lg shadow-xl border border-[#27272a]">
          <div className="px-3.5 py-3">
            <div className="text-xs font-medium text-[#fafafa]">{title}</div>
            {description && <div className="text-[11px] text-slate-400 mt-1">{description}</div>}
            <div className="flex justify-end gap-2 mt-3">
              <Button size="small" onClick={() => setOpen(false)}>
                {cancelText}
              </Button>
              <Button
                size="small"
                type="primary"
                danger={okButtonProps?.danger}
                onClick={() => {
                  onConfirm?.()
                  setOpen(false)
                }}
              >
                {okText}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ─── Table ─── */

export interface TableColumn<T> {
  title: ReactNode
  dataIndex?: string
  key: string
  width?: number | string
  render?: (value: unknown, record: T, index: number) => ReactNode
}

interface TableProps<T> {
  dataSource: T[]
  columns: TableColumn<T>[]
  rowKey?: string | ((record: T) => string)
  pagination?: false
  size?: 'small' | 'middle'
  className?: string
  rowClassName?: (record: T, index: number) => string
}

export function Table<T extends Record<string, unknown>>({
  dataSource,
  columns,
  rowKey = 'id',
  size = 'middle',
  className = '',
  rowClassName,
}: TableProps<T>) {
  const pad = size === 'small' ? 'px-2 py-1.5' : 'px-3 py-2'
  const getKey = (record: T, index: number): string => {
    if (typeof rowKey === 'function') return rowKey(record)
    const v = record[rowKey]
    return v !== undefined && v !== null ? String(v) : String(index)
  }
  return (
    <div className={`overflow-x-auto border border-[#27272a] rounded-lg ${className}`}>
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-[#121214]/60 border-b border-[#27272a]">
            {columns.map((col) => (
              <th
                key={col.key}
                style={{ width: col.width }}
                className={`${pad} text-left font-medium text-slate-400 whitespace-nowrap`}
              >
                {col.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dataSource.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className={`${pad} text-center text-[#71717a]`}>
                暂无数据
              </td>
            </tr>
          ) : (
            dataSource.map((record, index) => (
              <tr
                key={getKey(record, index)}
                className={`border-b border-[#27272a]/60 last:border-0 hover:bg-[#121214]/40 ${
                  rowClassName ? rowClassName(record, index) : ''
                }`}
              >
                {columns.map((col) => {
                  const val = col.dataIndex ? record[col.dataIndex] : undefined
                  return (
                    <td key={col.key} className={`${pad} text-slate-300 align-top`}>
                      {col.render ? col.render(val, record, index) : (val as ReactNode)}
                    </td>
                  )
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
