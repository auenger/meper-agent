/**
 * UsersPage — user management with RBAC roles & lifecycle status.
 *
 * Connects to the real backend API for full CRUD operations.
 * Supports: list with pagination/filters, create, update role/status, delete, reset password.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Button, Tag, Avatar, Select, Table, Modal, Form, Input,
  message, Dropdown,
} from 'antd'
import type { MenuProps, TableProps } from 'antd'
import {
  SearchOutlined, PlusOutlined, UserOutlined, MoreOutlined,
  MailOutlined, ClockCircleOutlined, DeleteOutlined,
  EditOutlined, KeyOutlined, StopOutlined, CheckCircleOutlined,
} from '@ant-design/icons'
import { useTheme } from '../contexts/ThemeContext'
import { useAuthStore } from '../stores/auth-store'
import { userApi, type User } from '../services/user-api'
import { roleApi } from '../services/role-api'
import { type NormalizedApiError } from '../services/api-client'
import type { Role } from '../types/permission'
import { usePermission } from '../hooks/use-permission'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

/* Role colors */
const ROLE_COLORS: Record<string, string> = {
  admin: '#EF4444',
  developer: '#2563EB',
  operator: '#10B981',
  viewer: '#94A3B8',
}

function getRoleColor(role: string): string {
  return ROLE_COLORS[role] ?? '#6366F1'
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = dayjs(iso)
  if (!d.isValid()) return '—'
  return d.format('YYYY-MM-DD HH:mm')
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return '从未登录'
  const d = dayjs(iso)
  if (!d.isValid()) return '—'
  return d.fromNow()
}

export default function UsersPage() {
  const { t } = useTheme()
  const currentUser = useAuthStore((s) => s.user)
  const canWrite = usePermission('user:write')

  // List state
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [searchUsername, setSearchUsername] = useState('')
  const [filterRole, setFilterRole] = useState<string>('all')
  const [filterStatus, setFilterStatus] = useState<string>('all')

  // Roles list (for dropdowns)
  const [roles, setRoles] = useState<Role[]>([])

  // Modal state
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [resetPwdModalOpen, setResetPwdModalOpen] = useState(false)
  const [targetUser, setTargetUser] = useState<User | null>(null)
  const [createForm] = Form.useForm()
  const [resetPwdForm] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  // Fetch users
  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize }
      if (searchUsername) params.username = searchUsername
      if (filterRole !== 'all') params.role = filterRole
      if (filterStatus !== 'all') params.status = filterStatus

      const res = await userApi.list(params as Record<string, string>)
      setUsers(res.data.items)
      setTotal(res.data.total)
    } catch {
      message.error('加载用户列表失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, searchUsername, filterRole, filterStatus])

  // Fetch roles
  const fetchRoles = useCallback(async () => {
    try {
      const res = await roleApi.list()
      setRoles(res.data)
    } catch {
      // Non-critical — role dropdown will be empty
    }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { fetchUsers() }, [fetchUsers])
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { fetchRoles() }, [fetchRoles])

  // Role options for filters/dropdowns
  const roleOptions = [
    { value: 'all', label: '全部角色' },
    ...roles.map((r) => ({ value: r.name, label: r.display_name })),
  ]

  // Create user
  const handleCreate = async (values: { username: string; email: string; password: string; role: string }) => {
    setSubmitting(true)
    try {
      await userApi.create(values)
      message.success('用户已创建')
      setCreateModalOpen(false)
      createForm.resetFields()
      fetchUsers()
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  // Toggle user status
  const handleToggleStatus = async (user: User) => {
    const newStatus = user.status === 'active' ? 'disabled' : 'active'
    try {
      await userApi.update(user.id, { status: newStatus })
      message.success(newStatus === 'active' ? '用户已启用' : '用户已禁用')
      fetchUsers()
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '操作失败')
    }
  }

  // Delete user
  const handleDelete = async (userId: string) => {
    try {
      await userApi.delete(userId)
      message.success('用户已删除')
      fetchUsers()
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '删除失败')
    }
  }

  // Change role
  const handleChangeRole = async (user: User, newRole: string) => {
    try {
      await userApi.update(user.id, { role: newRole })
      message.success('角色已更新')
      fetchUsers()
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '更新角色失败')
    }
  }

  // Reset password
  const handleResetPassword = async (values: { new_password: string }) => {
    if (!targetUser) return
    setSubmitting(true)
    try {
      await userApi.resetPassword(targetUser.id, { new_password: values.new_password })
      message.success('密码已重置')
      setResetPwdModalOpen(false)
      resetPwdForm.resetFields()
      setTargetUser(null)
    } catch (err: unknown) {
      const apiErr = err as NormalizedApiError
      message.error(apiErr.message ?? '重置密码失败')
    } finally {
      setSubmitting(false)
    }
  }

  // Context menu for each user row
  const getUserMenuItems = (user: User): MenuProps['items'] => {
    const items: MenuProps['items'] = []

    if (canWrite) {
      items.push({
        key: 'toggle-status',
        icon: user.status === 'active' ? <StopOutlined /> : <CheckCircleOutlined />,
        label: user.status === 'active' ? '禁用' : '启用',
        onClick: () => handleToggleStatus(user),
      })
      items.push({
        key: 'reset-pwd',
        icon: <KeyOutlined />,
        label: '重置密码',
        onClick: () => {
          setTargetUser(user)
          setResetPwdModalOpen(true)
        },
      })
      if (user.id !== currentUser?.id) {
        items.push({ type: 'divider' })
        items.push({
          key: 'delete',
          icon: <DeleteOutlined />,
          label: '删除用户',
          danger: true,
          onClick: () => {
            Modal.confirm({
              title: '确认删除',
              content: `确定要删除用户 "${user.username}" 吗？此操作不可恢复。`,
              okText: '确认删除',
              okButtonProps: { danger: true },
              onOk: () => handleDelete(user.id),
            })
          },
        })
      }
    }

    return items
  }

  // Table columns
  const columns: TableProps<User>['columns'] = [
    {
      title: '用户',
      key: 'user',
      width: 280,
      render: (_: unknown, record: User) => (
        <div className="flex items-center gap-3 min-w-0">
          <Avatar size={32} icon={<UserOutlined />} style={{ background: t.bg, color: t.primary }} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[#0F172A]">{record.username}</span>
            </div>
            <div className="text-xs text-[#64748B] truncate flex items-center gap-1">
              <MailOutlined className="text-[10px]" />
              {record.email}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (role: string, record: User) => {
        const roleInfo = roles.find((r) => r.name === role)
        const displayName = roleInfo?.display_name ?? role
        const color = getRoleColor(role)

        if (canWrite && role !== currentUser?.role) {
          return (
            <Dropdown
              menu={{
                items: roles.map((r) => ({
                  key: r.name,
                  label: r.display_name,
                  onClick: () => handleChangeRole(record, r.name),
                })),
              }}
            >
              <Tag
                className="!m-0 !w-fit !px-2 !py-0.5 !text-xs !rounded cursor-pointer"
                style={{ color, background: `${color}15`, borderColor: 'transparent' }}
              >
                {displayName} <EditOutlined className="text-[9px] ml-0.5" />
              </Tag>
            </Dropdown>
          )
        }
        return (
          <Tag className="!m-0 !w-fit !px-2 !py-0.5 !text-xs !rounded" style={{ color, background: `${color}15`, borderColor: 'transparent' }}>
            {displayName}
          </Tag>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (status: string) => (
        <Tag className="!m-0 !w-fit !px-2 !py-0.5 !text-xs !rounded" style={{
          color: status === 'active' ? '#10B981' : '#94A3B8',
          background: status === 'active' ? '#D1FAE5' : '#F1F5F9',
          borderColor: 'transparent',
        }}>
          {status === 'active' ? '活跃' : '已禁用'}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      render: (v: string) => <span className="text-xs text-[#64748B]">{formatTime(v)}</span>,
    },
    {
      title: '上次登录',
      dataIndex: 'last_login_at',
      key: 'last_login_at',
      width: 120,
      render: (v: string) => (
        <span className="text-xs text-[#64748B] flex items-center gap-1">
          <ClockCircleOutlined className="text-[10px]" />
          {formatRelativeTime(v)}
        </span>
      ),
    },
    {
      title: '',
      key: 'actions',
      width: 40,
      render: (_: unknown, record: User) => {
        if (!canWrite) return null
        const items = getUserMenuItems(record)
        if (!items || items.length === 0) return null
        return (
          <Dropdown menu={{ items }}>
            <button className="border-0 bg-transparent w-8 h-8 flex items-center justify-center rounded-md text-[#64748B] hover:text-[#0F172A] hover:bg-gray-50 transition-colors duration-150">
              <MoreOutlined />
            </button>
          </Dropdown>
        )
      },
    },
  ]

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: '总用户', value: total.toString() },
          { label: '活跃用户', value: users.filter((u) => u.status === 'active').length.toString() },
          { label: '管理员', value: users.filter((u) => u.role === 'admin').length.toString() },
          { label: '角色类型', value: roles.length.toString() },
        ].map((s) => (
          <div key={s.label} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="text-2xl font-semibold text-[#0F172A] mb-0.5">{s.value}</div>
            <div className="text-xs text-[#64748B]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="relative">
            <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-[#94A3B8] text-sm" />
            <Input
              placeholder="搜索用户名..."
              className="!pl-9 !w-56"
              allowClear
              onPressEnter={() => { setPage(1); fetchUsers() }}
              onChange={(e) => setSearchUsername(e.target.value)}
            />
          </div>
          <Select value={filterRole} onChange={(v) => { setFilterRole(v); setPage(1) }} className="w-32" options={roleOptions} />
          <Select
            value={filterStatus}
            onChange={(v) => { setFilterStatus(v); setPage(1) }}
            className="w-28"
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'active', label: '活跃' },
              { value: 'disabled', label: '已禁用' },
            ]}
          />
        </div>
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
            创建用户
          </Button>
        )}
      </div>

      {/* Table */}
      <Table<User>
        columns={columns}
        dataSource={users}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个用户`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps) },
        }}
        size="middle"
      />

      {/* Create user modal */}
      <Modal
        title="创建用户"
        open={createModalOpen}
        onCancel={() => { setCreateModalOpen(false); createForm.resetFields() }}
        onOk={() => createForm.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={submitting}
        destroyOnHidden
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate} className="mt-4">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input maxLength={50} placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[
            { required: true, message: '请输入邮箱' },
            { type: 'email', message: '请输入有效的邮箱地址' },
          ]}>
            <Input maxLength={255} placeholder="user@example.com" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[
            { required: true, message: '请输入密码' },
            { min: 8, message: '密码至少 8 位' },
          ]}>
            <Input.Password placeholder="至少 8 位，包含字母和数字" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: '请选择角色' }]} initialValue="viewer">
            <Select options={roles.map((r) => ({ value: r.name, label: r.display_name }))} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Reset password modal */}
      <Modal
        title={`重置密码 — ${targetUser?.username ?? ''}`}
        open={resetPwdModalOpen}
        onCancel={() => { setResetPwdModalOpen(false); resetPwdForm.resetFields(); setTargetUser(null) }}
        onOk={() => resetPwdForm.submit()}
        okText="重置"
        cancelText="取消"
        confirmLoading={submitting}
        destroyOnHidden
      >
        <Form form={resetPwdForm} layout="vertical" onFinish={handleResetPassword} className="mt-4">
          <Form.Item name="new_password" label="新密码" rules={[
            { required: true, message: '请输入新密码' },
            { min: 8, message: '密码至少 8 位' },
          ]}>
            <Input.Password placeholder="至少 8 位，包含字母和数字" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
