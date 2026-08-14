/**
 * PermissionCheckboxGroup — grouped checkbox UI for selecting permissions.
 *
 * Displays permissions organized by module (user, agent, workflow, etc.)
 * with group-level select-all/deselect-all functionality.
 */
import { Checkbox, Collapse } from 'antd'
import { PERMISSION_GROUPS } from '../types/permission'

interface PermissionCheckboxGroupProps {
  value?: string[]
  onChange?: (value: string[]) => void
  disabled?: boolean
}

export function PermissionCheckboxGroup({
  value = [],
  onChange,
  disabled = false,
}: PermissionCheckboxGroupProps) {
  // antd Checkbox.Group 的 onChange 只返回"本 Group 内"勾选的项——
  // 每个 Collapse 面板里是一个独立的 Group，只渲染自己模块的权限。
  // 直接把 checkedValues 当全量 value 用会丢掉其他模块的勾选，
  // 所以这里按"本模块替换、其他模块保留"合并。
  const makeGroupChangeHandler = (groupPerms: string[]) => {
    return (checkedValues: (string | number | boolean)[]) => {
      const checkedSet = new Set(checkedValues.map(String))
      const next = [
        // 其他模块已勾选的项保持不变
        ...value.filter((p) => !groupPerms.includes(p)),
        // 本模块按最新勾选状态替换
        ...groupPerms.filter((p) => checkedSet.has(p)),
      ]
      onChange?.(next)
    }
  }

  const handleGroupToggle = (groupPerms: string[], checked: boolean) => {
    const currentSet = new Set(value)
    if (checked) {
      groupPerms.forEach((p) => currentSet.add(p))
    } else {
      groupPerms.forEach((p) => currentSet.delete(p))
    }
    onChange?.(Array.from(currentSet))
  }

  const collapseItems = Object.entries(PERMISSION_GROUPS).map(
    ([groupName, perms]) => {
      const checkedCount = perms.filter((p) => value.includes(p)).length
      const allChecked = checkedCount === perms.length
      const someChecked = checkedCount > 0

      return {
        key: groupName,
        label: (
          <div className="flex items-center gap-2">
            <Checkbox
              checked={allChecked}
              indeterminate={someChecked && !allChecked}
              disabled={disabled}
              onChange={(e) => handleGroupToggle(perms, e.target.checked)}
              onClick={(e) => e.stopPropagation()}
            />
            <span className="font-medium text-sm">{groupName}</span>
            <span className="text-xs text-gray-400">
              ({checkedCount}/{perms.length})
            </span>
          </div>
        ),
        children: (
          <Checkbox.Group
            value={value}
            onChange={makeGroupChangeHandler(perms)}
            disabled={disabled}
            className="flex flex-col gap-2"
          >
            {perms.map((perm) => (
              <Checkbox key={perm} value={perm}>
                <span className="text-sm font-mono">{perm}</span>
              </Checkbox>
            ))}
          </Checkbox.Group>
        ),
      }
    },
  )

  return (
    <div className="permission-checkbox-group">
      <Collapse
        items={collapseItems}
        defaultActiveKey={Object.keys(PERMISSION_GROUPS)}
        size="small"
        bordered={false}
      />
    </div>
  )
}
