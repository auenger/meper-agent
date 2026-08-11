/**
 * ClarificationFormCard — ask_clarification 向导模式渲染卡片。
 *
 * 当 ask_clarification 提供 fields 时，渲染为向导：一次展示一个问题，
 * 答完跳下一个，全部答完才提交。
 *
 * 每个字段的展示规则：
 * - select + options → 显示选项按钮 + 底部自由输入框（输入覆盖选项选择）
 * - text → 只显示输入框
 * - number → 只显示数字输入框
 * - boolean → 显示"是/否"按钮
 *
 * boolean 字段不占向导步骤（用默认值，不出现在向导流程中）。
 * 用 client 原生 CSS（不依赖 Tailwind）。
 */

import { useState } from 'react'
import { Input, InputNumber, Button } from 'antd'
import { LeftOutlined, SendOutlined } from '@ant-design/icons'

export interface ClarificationField {
  name: string
  label: string
  field_type: 'text' | 'number' | 'boolean' | 'select'
  required: boolean
  options?: string[] | null
  default?: string | number | boolean | null
  description?: string | null
}

interface ClarificationFormCardProps {
  question: string
  context?: string | null
  fields: ClarificationField[]
  answered: boolean
  result?: string
  onSubmit: (jsonStr: string) => void
}

export function ClarificationFormCard({ question, context, fields, answered, result, onSubmit }: ClarificationFormCardProps) {
  // 已答态：渲染摘要
  if (answered) {
    let parsed: Record<string, unknown> = {}
    if (result) {
      try { parsed = JSON.parse(result) } catch { /* not JSON */ }
    }
    return (
      <div className="clarification-form-answered">
        {Object.entries(parsed).length > 0 ? (
          Object.entries(parsed).map(([key, val]) => {
            const field = fields.find(f => f.name === key)
            const displayVal = typeof val === 'boolean' ? (val ? '是' : '否') : String(val)
            return (
              <div key={key} className="clarification-form-row">
                <span className="clarification-form-key">{field?.label || key}</span>
                <span className="clarification-form-val">{displayVal}</span>
              </div>
            )
          })
        ) : result ? (
          <div className="clarification-form-row"><span className="clarification-form-val">{result}</span></div>
        ) : null}
      </div>
    )
  }

  // 未答态：向导表单
  // boolean 字段不占步骤（用默认值），只把 text/number/select 排进步骤
  const wizardFields = fields.filter(f => f.field_type !== 'boolean')
  const total = wizardFields.length
  const [currentIdx, setCurrentIdx] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string | number>>({})

  // 初始化 boolean 字段默认值
  const [boolInit, setBoolInit] = useState(false)
  if (!boolInit) {
    setBoolInit(true)
    const init: Record<string, string | number> = {}
    fields.forEach(f => {
      if (f.field_type === 'boolean') {
        init[f.name] = f.default === true ? 'true' : 'false'
      }
    })
    setAnswers(prev => ({ ...init, ...prev }))
  }

  if (total === 0) {
    // 全是 boolean 字段，直接提交默认值
    return (
      <Button type="primary" icon={<SendOutlined />} onClick={() => {
        const result: Record<string, unknown> = {}
        fields.forEach(f => { result[f.name] = answers[f.name] === 'true' })
        onSubmit(JSON.stringify(result))
      }}>确认</Button>
    )
  }

  const field = wizardFields[currentIdx]
  const isLast = currentIdx === total - 1
  const currentVal = answers[field.name] ?? ''

  // select 字段：选项选中的值（不含输入框值）
  const selectedOption = field.field_type === 'select' && field.options?.includes(currentVal as string)
    ? currentVal as string : ''
  // 输入框的值（不含选项选中的值）
  const inputVal = selectedOption ? '' : (currentVal as string)
  // 能否进入下一题：有选项选中 或 有输入值
  const canProceed = currentVal !== '' && currentVal !== undefined && currentVal !== null

  const handleNext = () => {
    if (isLast) {
      // 提交：合并 boolean + 填写的值
      const result: Record<string, unknown> = {}
      fields.forEach(f => {
        if (f.field_type === 'boolean') {
          result[f.name] = answers[f.name] === 'true'
        } else if (answers[f.name] !== undefined && answers[f.name] !== '') {
          result[f.name] = f.field_type === 'number' ? Number(answers[f.name]) : answers[f.name]
        }
      })
      onSubmit(JSON.stringify(result))
    } else {
      setCurrentIdx(currentIdx + 1)
    }
  }

  const handlePrev = () => {
    if (currentIdx > 0) setCurrentIdx(currentIdx - 1)
  }

  return (
    <div className="clarification-form-wizard">
      {/* 进度 + 问题 */}
      <div className="clarification-form-header">
        {total > 1 && <span className="clarification-form-progress">{currentIdx + 1} / {total}</span>}
        <span className="clarification-form-label">{field.label}</span>
      </div>

      {/* 字段描述 */}
      {field.description && <div className="clarification-form-desc">{field.description}</div>}

      {/* 输入区 */}
      <div className="clarification-form-field">
        {/* select 型：选项按钮 + 自由输入（各自独立） */}
        {field.field_type === 'select' && field.options && field.options.length > 0 ? (
          <>
            <div className="clarification-form-options">
              {field.options.map(opt => (
                <Button
                  key={opt}
                  type={currentVal === opt ? 'primary' : 'default'}
                  onClick={() => setAnswers({ ...answers, [field.name]: opt })}
                >{opt}</Button>
              ))}
            </div>
            <div className="clarification-form-input" style={{ marginTop: 8 }}>
              <Input
                value={inputVal as string}
                onChange={e => setAnswers({ ...answers, [field.name]: e.target.value })}
                placeholder="或直接输入"
              />
            </div>
          </>
        ) : field.field_type === 'number' ? (
          <div className="clarification-form-input">
            <InputNumber
              value={currentVal as number || ''}
              onChange={v => setAnswers({ ...answers, [field.name]: v ?? '' })}
              placeholder="请输入数字"
              style={{ width: '100%' }}
            />
          </div>
        ) : (
          /* text 型：自由输入 */
          <div className="clarification-form-input">
            <Input
              value={currentVal as string}
              onChange={e => setAnswers({ ...answers, [field.name]: e.target.value })}
              placeholder="请输入"
            />
          </div>
        )}
      </div>

      {/* 按钮 */}
      <div className="clarification-form-actions">
        {currentIdx > 0 && (
          <Button type="text" icon={<LeftOutlined />} onClick={handlePrev}>上一题</Button>
        )}
        <Button
          type="primary"
          icon={isLast ? <SendOutlined /> : undefined}
          disabled={!canProceed}
          onClick={handleNext}
        >
          {isLast ? '提交' : '下一题'}
        </Button>
      </div>
    </div>
  )
}
