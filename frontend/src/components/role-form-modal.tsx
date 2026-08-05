/**
 * RoleFormModal — create/edit role dialog.
 *
 * - Create mode: name field is editable (lowercase identifier)
 * - Edit mode (system role): name/display_name/description are disabled, only permissions editable
 * - Edit mode (custom role): display_name/description/permissions editable
 */
import { useEffect } from 'react'
import { Modal, Form, Input, message } from 'antd'
import type { Role } from '../types/permission'
import { roleApi, type RoleCreatePayload, type RoleUpdatePayload } from '../services/role-api'
import { type NormalizedApiError } from '../services/api-client'
import { PermissionCheckboxGroup } from './permission-checkbox-group'

interface RoleFormModalProps {
  open: boolean
  role: Role | null // null = create mode
  onClose: () => void
  onSuccess: () => void
}

export function RoleFormModal({ open, role, onClose, onSuccess }: RoleFormModalProps) {
  const [form] = Form.useForm()
  const isEdit = role !== null
  const isSystemRole = role?.role_type === 'system'

  useEffect(() => {
    if (open) {
      if (role) {
        form.setFieldsValue({
          name: role.name,
          display_name: role.display_name,
          description: role.description,
          permissions: role.permissions,
        })
      } else {
        form.resetFields()
      }
    }
  }, [open, role, form])

  const handleOk = async () => {
    try {
      const values = await form.validateFields()

      if (isEdit && role) {
        const payload: RoleUpdatePayload = {}
        if (!isSystemRole && values.display_name !== role.display_name) {
          payload.display_name = values.display_name
        }
        if (!isSystemRole && values.description !== role.description) {
          payload.description = values.description
        }
        if (values.permissions) {
          payload.permissions = values.permissions
        }
        await roleApi.update(role.id, payload)
        message.success('角色已更新')
      } else {
        const payload: RoleCreatePayload = {
          name: values.name,
          display_name: values.display_name,
          description: values.description || '',
          permissions: values.permissions || [],
        }
        await roleApi.create(payload)
        message.success('角色已创建')
      }
      onSuccess()
    } catch (err: unknown) {
      // Form validation errors are handled by AntD
      if (err && typeof err === 'object' && 'errorFields' in err) return
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '操作失败')
    }
  }

  const title = isEdit
    ? isSystemRole
      ? `编辑系统角色 — ${role.display_name}`
      : `编辑角色 — ${role.display_name}`
    : '创建自定义角色'

  return (
    <Modal
      title={title}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      width={640}
      okText={isEdit ? '保存' : '创建'}
      cancelText="取消"
      destroyOnHidden
      styles={{
        body: { maxHeight: 'calc(100vh - 260px)', overflowY: 'auto' },
      }}
    >
      <Form
        form={form}
        layout="vertical"
        className="mt-4"
      >
        <Form.Item
          name="name"
          label="角色标识"
          rules={[
            { required: true, message: '请输入角色标识' },
            { pattern: /^[a-z][a-z0-9_]*$/, message: '仅允许小写字母、数字和下划线，且以字母开头' },
          ]}
          tooltip="角色的唯一标识，创建后不可修改"
        >
          <Input
            placeholder="例如: content_editor"
            disabled={isEdit}
            maxLength={50}
          />
        </Form.Item>

        <Form.Item
          name="display_name"
          label="显示名称"
          rules={[{ required: true, message: '请输入显示名称' }]}
        >
          <Input
            placeholder="例如: 内容编辑"
            disabled={isSystemRole}
            maxLength={100}
          />
        </Form.Item>

        <Form.Item
          name="description"
          label="描述"
        >
          <Input.TextArea
            placeholder="角色描述（可选）"
            disabled={isSystemRole}
            maxLength={500}
            rows={2}
          />
        </Form.Item>

        <Form.Item
          name="permissions"
          label="权限"
          rules={[{ required: true, message: '请至少选择一个权限' }]}
        >
          <PermissionCheckboxGroup />
        </Form.Item>
      </Form>
    </Modal>
  )
}
