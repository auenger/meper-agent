/**
 * ParallelNodeConfig — 并行节点配置面板。
 */
import { Input, InputNumber, Select } from 'antd'

interface Props {
  config: Record<string, unknown>
  onChange: (c: Record<string, unknown>) => void
}

export default function ParallelNodeConfig({ config, onChange }: Props) {
  const joinStrategy = (config.join_strategy as string) ?? 'all'
  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-[#64748B] mb-1">合并策略</label>
        <Select
          className="w-full"
          value={joinStrategy}
          onChange={(val) => onChange({ ...config, join_strategy: val })}
          options={[
            { value: 'all', label: '等待所有分支完成' },
            { value: 'any', label: '任一分支完成即可（竞速）' },
            { value: 'n-of-m', label: 'N 个分支完成即可' },
          ]}
        />
      </div>
      {joinStrategy === 'n-of-m' && (
        <div>
          <label className="block text-xs text-[#64748B] mb-1">完成数量 N</label>
          <InputNumber
            className="w-full"
            min={1}
            value={(config.join_count as number) ?? 1}
            onChange={(val) => onChange({ ...config, join_count: val ?? 1 })}
          />
        </div>
      )}
      <div>
        <label className="block text-xs text-[#64748B] mb-1">变量作用域</label>
        <Select
          className="w-full"
          value={(config.scope as string) ?? 'shared'}
          onChange={(val) => onChange({ ...config, scope: val })}
          options={[
            { value: 'shared', label: '共享作用域（所有分支共享变量池）' },
          ]}
        />
        <div className="text-[10px] text-[#94A3B8] mt-1">隔离作用域暂未实现，所有分支共享同一变量池</div>
      </div>
      <div>
        <label className="block text-xs text-[#64748B] mb-1">分支配置 (JSON)</label>
        <Input.TextArea
          value={JSON.stringify(config.branches ?? [], null, 2)}
          onChange={(e) => {
            try { onChange({ ...config, branches: JSON.parse(e.target.value) }) }
            catch { /* allow editing invalid JSON */ }
          }}
          rows={4}
          className="font-mono text-xs"
          placeholder='[{"id": "branch_1", "start_node": "node_a"}]'
        />
      </div>
    </div>
  )
}
