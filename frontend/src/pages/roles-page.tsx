/**
 * RolesPage — dynamic role management UI.
 *
 * Lists all roles (system + custom) with permission counts.
 * Supports CRUD operations for custom roles and permission editing for system roles.
 */
import { useCallback, useEffect, useState } from 'react'
import { Button, Table, Tag, Popconfirm, message, Typography, Space } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { Role } from '../types/permission'
import { roleApi } from '../services/role-api'
import { type NormalizedApiError } from '../services/api-client'
import { RoleFormModal } from '../components/role-form-modal'

export default function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRole, setEditingRole] = useState<Role | null>(null)

  const fetchRoles = useCallback(async () => {
    setLoading(true)
    try {
      const res = await roleApi.list()
      setRoles(res.data)
    } catch {
      message.error('加载角色列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchRoles()
  }, [fetchRoles])

  const handleCreate = () => {
    setEditingRole(null)
    setModalOpen(true)
  }

  const handleEdit = (role: Role) => {
    setEditingRole(role)
    setModalOpen(true)
  }

  const handleDelete = async (roleId: string) => {
    try {
      await roleApi.delete(roleId)
      message.success('角色已删除')
      fetchRoles()
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '删除失败')
    }
  }

  const handleModalSuccess = () => {
    setModalOpen(false)
    setEditingRole(null)
    fetchRoles()
  }

  const columns: ColumnsType<Role> = [
    {
      title: '角色名称',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      render: (name: string) => <span className="font-mono text-sm">{name}</span>,
    },
    {
      title: '显示名称',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 120,
    },
    {
      title: '类型',
      dataIndex: 'role_type',
      key: 'role_type',
      width: 100,
      render: (type: string) =>
        type === 'system' ? (
          <Tag color="blue">系统</Tag>
        ) : (
          <Tag color="green">自定义</Tag>
        ),
    },
    {
      title: '权限数',
      dataIndex: 'permissions',
      key: 'permissions',
      width: 80,
      align: 'center',
      render: (perms: string[]) => perms?.length ?? 0,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (desc: string) => desc || <span className="text-gray-400">—</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: unknown, record: Role) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            {record.role_type === 'system' ? '编辑权限' : '编辑'}
          </Button>
          {record.role_type === 'custom' && (
            <Popconfirm
              title="确认删除"
              description="删除后不可恢复，使用该角色的用户将失去权限"
              okText="确认删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => handleDelete(record.id)}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <Typography.Title level={4} className="!mb-0">
          角色管理
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          创建角色
        </Button>
      </div>

      <Table<Role>
        columns={columns}
        dataSource={roles}
        rowKey="id"
        loading={loading}
        pagination={false}
        size="middle"
      />

      <RoleFormModal
        open={modalOpen}
        role={editingRole}
        onClose={() => {
          setModalOpen(false)
          setEditingRole(null)
        }}
        onSuccess={handleModalSuccess}
      />
    </div>
  )
}
