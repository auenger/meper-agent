import { useState, FormEvent, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import {
  Users, Shield, Search, Plus, Lock, Pencil, ChevronLeft, ChevronRight, KeyRound, RefreshCw, Crown,
} from 'lucide-react';
import { userApi } from '../services/user-api';
import { roleApi, type RoleUpdatePayload } from '../services/role-api';
import { toStudioUser } from '../services/adapters';
import { usePermission } from '../hooks/use-permission';
import { useAuthStore } from '../stores/auth-store';
import { Select, Popover } from './ui';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';
import { PermissionTree } from './ui/PermissionTree';
import { type NormalizedApiError } from '../lib/api-client';
import type { User } from '../types';
import type { Role } from '../services/types';

/** 首字母圆牌配色（纯色 hex + 白字，亮/暗双主题下都清晰）。按用户名 hash 确定性取色。 */
const MONOGRAM_COLORS = [
  '#4f46e5', '#059669', '#d97706', '#e11d48',
  '#0891b2', '#7c3aed', '#db2777', '#2563eb',
];

function hashIndex(s: string, mod: number): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % mod;
}

/** 取用户名首字母（1-2 个 token 的首字母），大写。 */
function initialsOf(name: string): string {
  const parts = name.trim().split(/[\s_.-]+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** 每页条数（服务端分页，避免用户多时页面被表格撑高）。 */
const PAGE_SIZE = 20;

/** 生成 12 位强随机密码（大小写字母 + 数字 + 符号，浏览器 CSPRNG）。 */
function generatePassword(): string {
  const groups = [
    'ABCDEFGHJKLMNPQRSTUVWXYZ', // 去掉易混淆 I O
    'abcdefghijkmnpqrstuvwxyz', // 去掉易混淆 l o
    '23456789',
    '!@#$%^&*',
  ];
  const all = groups.join('');
  const pick = (s: string) => s[crypto.getRandomValues(new Uint32Array(1))[0] % s.length];
  // 每组先各取 1 位保证复杂度，其余从全集补足 12 位
  return [...groups.map(pick), ...Array.from({ length: 12 - groups.length }, () => pick(all))]
    .sort(() => crypto.getRandomValues(new Uint32Array(1))[0] / 2 ** 32 - 0.5)
    .join('');
}

export function UserManagement() {
  const queryClient = useQueryClient();
  // 搜索：输入框即时受控，300ms 防抖后作为服务端 search 参数（用户名/邮箱 $or）
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selectedUserForRole, setSelectedUserForRole] = useState<string | null>(null);
  // 用户与角色的全部写操作（新增/删除/锁定/改角色/角色 CRUD）统一由
  // user:write 门控，与后端 admin.py/roles.py 的授权口径对齐。
  const canWrite = usePermission('user:write');
  // 超管门控：普通管理员不可对其他管理员执行锁定/删除/改角色/重置密码，
  // 也不可将任何用户提升为 admin（后端 SUPER_ADMIN_REQUIRED 守卫兜底）。
  const isSuperAdmin = useAuthStore((s) => Boolean(s.user?.isSuperAdmin));
  const currentUserId = useAuthStore((s) => s.user?.id);
  // 行内角色菜单：同一时间只开一个；徽章本身是触发器，样式全程不变
  const [openRoleMenuUserId, setOpenRoleMenuUserId] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      setSearchQuery(searchInput.trim());
      setPage(1); // 新搜索从第一页开始
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // New user form state
  const [isAdding, setIsAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<string>('viewer');
  // Field-level / form-level error rendered inside the create-user modal so
  // the user sees why it failed instead of the dialog silently closing.
  const [createFormError, setCreateFormError] = useState<NormalizedApiError | null>(null);

  // Reset-password modal state — target user is null while closed.
  const [resetPwdTarget, setResetPwdTarget] = useState<User | null>(null);
  const [resetPwdValue, setResetPwdValue] = useState('');
  const [resetPwdError, setResetPwdError] = useState<NormalizedApiError | null>(null);

  // Role modal state — create & edit share one form (mode distinguishes them).
  const [roleModalMode, setRoleModalMode] = useState<'create' | 'edit' | null>(null);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [roleName, setRoleName] = useState('');
  const [roleDisplay, setRoleDisplay] = useState('');
  const [roleDesc, setRoleDesc] = useState('');
  const [rolePerms, setRolePerms] = useState<Set<string>>(new Set());

  const { data: usersData, isLoading, isFetching } = useQuery({
    queryKey: ['users', { page, page_size: PAGE_SIZE, search: searchQuery }],
    queryFn: async () =>
      (await userApi.list({ page, page_size: PAGE_SIZE, search: searchQuery || undefined })).data,
    placeholderData: keepPreviousData,
  });

  // 删除末页最后一条后 page 可能越界（totalPages 缩小）— 自动回拉到末页
  useEffect(() => {
    if (usersData && page > Math.max(1, Math.ceil(usersData.total / PAGE_SIZE))) {
      setPage(Math.max(1, Math.ceil(usersData.total / PAGE_SIZE)));
    }
  }, [usersData, page]);
  const { data: rolesData } = useQuery({
    queryKey: ['roles'],
    queryFn: async () => (await roleApi.list()).data,
  });
  const { data: allPermsData } = useQuery({
    queryKey: ['roles', 'permissions'],
    queryFn: async () => (await roleApi.getAllPermissions()).data,
  });

  const users = (usersData?.items ?? []).map(toStudioUser);
  const total = usersData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const roles = rolesData ?? [];
  const allPerms = allPermsData?.permissions ?? [];

  // Drive role selectors from the dynamic roles list (system + custom). The
  // backend role `name` is the value we submit/store; `display_name` is what
  // the user sees. This lets custom roles created in the side panel be
  // assigned to users instead of being hidden behind a hardcoded list.
  const roleOptions = roles.map((r) => ({ value: r.name, label: r.display_name }));
  // 非超管不可把用户提升为 admin：角色选择器（改角色菜单 + 新建用户表单）
  // 一律隐藏 admin 选项，后端 SUPER_ADMIN_REQUIRED 守卫兜底。
  const assignableRoleOptions = isSuperAdmin
    ? roleOptions
    : roleOptions.filter((o) => o.value !== 'admin');
  const roleDisplayName = (key: string): string =>
    roles.find((r) => r.name === key)?.display_name ?? key;

  const createM = useMutation({
    mutationFn: (input: { username: string; email: string; password: string; role: string }) =>
      userApi.create(input).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    // 错误由 handleCreateUser 的 try/catch 捕获并写入 createFormError（弹窗内
    // 字段级展示），故此处不重复 toast。
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: string; status?: 'active' | 'disabled' } }) =>
      userApi.update(id, body).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => userApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });

  const resetPwdM = useMutation({
    mutationFn: ({ id, newPassword }: { id: string; newPassword: string }) =>
      userApi.resetPassword(id, { new_password: newPassword }),
    // 成功 toast / 失败提示均在 handleResetPassword 内处理（弹窗内展示）。
  });

  const createRoleM = useMutation({
    mutationFn: (input: { name: string; display_name: string; description?: string; permissions: string[] }) =>
      roleApi.create(input).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      toast.success('角色已创建');
    },
  });

  const updateRoleM = useMutation({
    mutationFn: ({ id, body }: { id: string; body: RoleUpdatePayload }) =>
      roleApi.update(id, body).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      toast.success('角色权限已更新');
    },
  });

  const deleteRoleM = useMutation({
    mutationFn: (id: string) => roleApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      toast.success('角色已删除');
    },
  });

  const handleDeleteRole = async (role: Role) => {
    const ok = await confirmDialog({
      title: `删除角色「${role.display_name}」？`,
      description: '删除后不可恢复。若该角色已分配给用户，需先将这些用户改到其他角色。',
      okText: '删除',
      danger: true,
    });
    if (!ok) return;
    deleteRoleM.mutate(role.id);
  };

  const handleDeleteUser = async (user: User) => {
    const ok = await confirmDialog({
      title: `删除用户「${user.name}」？`,
      description: '删除后不可恢复，该用户的会话与个人数据将一并移除。',
      okText: '删除',
      danger: true,
    });
    if (!ok) return;
    deleteM.mutate(user.id);
  };

  const handleResetPassword = async (e: FormEvent) => {
    e.preventDefault();
    if (!resetPwdTarget || resetPwdValue.length < 8) return;
    setResetPwdError(null);
    try {
      await resetPwdM.mutateAsync({ id: resetPwdTarget.id, newPassword: resetPwdValue });
      toast.success(`已重置「${resetPwdTarget.name}」的密码`);
      setResetPwdTarget(null);
      setResetPwdValue('');
    } catch (err) {
      // Show the concrete backend message (e.g. weak-password 422) inside
      // the modal so the user can fix the input and retry.
      const normalized = err as NormalizedApiError;
      setResetPwdError(normalized);
    }
  };

  const handleCreateUser = async (e: FormEvent) => {
    e.preventDefault();
    if (!newName || !newEmail || !newPassword) return;
    // Clear any previous error shown inside the modal before retrying.
    setCreateFormError(null);
    try {
      await createM.mutateAsync({
        username: newName,
        email: newEmail,
        password: newPassword,
        role: newRole,
      });
      // Only reset & close when the request actually succeeded — otherwise
      // the user loses what they typed and the modal vanishes with no reason.
      setNewName('');
      setNewEmail('');
      setNewPassword('');
      setNewRole('viewer');
      setIsAdding(false);
    } catch (err) {
      // Show the concrete backend message (e.g. "password: ...") inside the
      // modal so the user can fix the input and retry.
      const normalized = err as NormalizedApiError;
      setCreateFormError(normalized);
    }
  };

  const handleChangeRole = (userId: string, roleKey: string) => {
    updateM.mutate({ id: userId, body: { role: roleKey } });
    setOpenRoleMenuUserId(null);
  };

  const handleToggleStatus = (userId: string, current: User['status']) => {
    updateM.mutate({
      id: userId,
      body: { status: current === 'active' ? 'disabled' : 'active' },
    });
  };

  const openRoleCreate = () => {
    setEditingRole(null);
    setRoleName('');
    setRoleDisplay('');
    setRoleDesc('');
    setRolePerms(new Set());
    setRoleModalMode('create');
  };

  const openRoleEdit = (role: Role) => {
    setEditingRole(role);
    setRoleName(role.name);
    setRoleDisplay(role.display_name);
    setRoleDesc(role.description);
    setRolePerms(new Set(role.permissions));
    setRoleModalMode('edit');
  };

  const closeRoleModal = () => {
    setRoleModalMode(null);
    setEditingRole(null);
  };

  const handleRoleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (roleModalMode === 'edit' && editingRole) {
      // 系统角色仅可改权限点（后端 SYSTEM_ROLE_IMMUTABLE_* 校验拒改显示名/描述），
      // 自定义角色可一并提交显示名/描述；name 一律不可改。
      updateRoleM.mutate({
        id: editingRole.id,
        body:
          editingRole.role_type === 'custom'
            ? { display_name: roleDisplay, description: roleDesc, permissions: [...rolePerms] }
            : { permissions: [...rolePerms] },
      });
    } else {
      if (!roleName || !roleDisplay) return;
      createRoleM.mutate({
        name: roleName,
        display_name: roleDisplay,
        description: roleDesc,
        permissions: [...rolePerms],
      });
    }
    closeRoleModal();
  };

  // 编辑系统角色时：显示名/描述只读（后端 SYSTEM_ROLE_IMMUTABLE_* 校验）。
  const isEditingSystemRole = roleModalMode === 'edit' && editingRole?.role_type === 'system';
  const roleFormPending = roleModalMode === 'edit' ? updateRoleM.isPending : createRoleM.isPending;

  return (
    // 固定视口高度布局（App.tsx 中 users tab 已切到 overflow-hidden 分支）：
    // 页面框架不随用户/角色数量增高，表格与角色列表各自内部滚动。
    <div className="flex flex-col h-full min-h-0 gap-6">
      {/* Top action bar */}
      <div className="flex flex-col sm:flex-row justify-between sm:items-center p-4 bg-[#18181b] rounded-xl border border-[#27272a] gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <Shield className="w-5 h-5 text-emerald-400" />
          <div className="space-y-0.5">
            <h2 className="text-sm font-bold text-[#fafafa] font-sans">RBAC 角色及矩阵鉴权中心</h2>
            <p className="text-xs text-[#a1a1aa] font-sans">配置组织成员角色、管理角色权限点（后端 {allPerms.length} 权限字符串）。</p>
          </div>
        </div>

        <div className="flex gap-2.5">
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="搜索用户名 / 邮箱..."
              className="pl-9 pr-4 py-1.5 w-56 text-xs bg-[#121214] border border-[#27272a] rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500 font-sans"
            />
          </div>

          {canWrite && (
            <button
              onClick={() => { setCreateFormError(null); setIsAdding(true); }}
              id="btn_add_user"
              className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl shadow transition cursor-pointer flex items-center gap-1 font-sans"
            >
              <Plus className="w-3.5 h-3.5" />
              新增成员
            </button>
          )}
        </div>
      </div>

      {/* RENDER MEMBERS TABLE */}
      {/* grid-rows：单列（窄屏）时约束两行行高，防止内容把 grid 撑破裁切；lg 单行满高。 */}
      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-3 gap-6 grid-rows-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1">
        <div className="lg:col-span-2 p-5 bg-[#18181b] border border-[#27272a] rounded-xl flex flex-col min-h-0 shadow-lg">
          <h3 className="text-xs font-semibold text-slate-400 tracking-wider uppercase flex items-center gap-1 shrink-0">
            <Users className="w-3.5 h-3.5 text-indigo-400" />
            团队成员授权列表
            <span className="font-mono normal-case">（共 {total} 人{isFetching ? ' · 加载中…' : ''}）</span>
          </h3>

          {/* overscroll-contain：滚到边界后不再向祖先链式滚动，杜绝整个页面被带着滚 */}
          <div className="flex-1 min-h-0 overflow-auto scrollbar-custom overscroll-contain mt-4">
            {/* border-separate 是 sticky 表头的前提：Tailwind preflight 给 table 设了
                border-collapse:collapse，而 Chromium 在 collapse 模式下 th 的
                position:sticky 不生效（表头会随内容滚走）。spacing-0 保持视觉零间距。 */}
            <table className="w-full text-xs text-left text-slate-400 leading-normal border-separate border-spacing-0">
              <thead>
                {/* 吸顶表头：sticky 放在 th 上（tr 的 border 会随滚动消失，边框移到 th）。
                    bg 用卡片色 #18181b，light 主题由 index.css 的 .theme-light 覆写映射。 */}
                <tr className="text-[#71717a] font-semibold">
                  <th className="py-2.5 px-3 sticky top-0 z-10 bg-[#18181b] border-b border-[#27272a]">基本信息</th>
                  <th className="py-2.5 px-3 sticky top-0 z-10 bg-[#18181b] border-b border-[#27272a]">系统角色</th>
                  <th className="py-2.5 px-3 sticky top-0 z-10 bg-[#18181b] border-b border-[#27272a]">账号状态</th>
                  <th className="py-2.5 px-3 text-right sticky top-0 z-10 bg-[#18181b] border-b border-[#27272a]">操作</th>
                </tr>
              </thead>
              {/* border-separate 模式下 tr 边框不绘制，行分隔线用 td 的 border-b */}
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={4} className="py-4 px-3 text-[#71717a] border-b border-[#27272a]/60">加载中…</td></tr>
                ) : users.length === 0 ? (
                  <tr><td colSpan={4} className="py-4 px-3 text-[#71717a] border-b border-[#27272a]/60">{searchQuery ? '没有匹配的成员' : '暂无成员'}</td></tr>
                ) : users.map((user) => {
                  // 超管门控（行级）：
                  // - 重置密码：对"自己"始终可用（后端对 self 豁免），其他管理员目标仅超管；
                  // - 锁定/删除/改角色：管理员目标仅超管，且不对自己开放（防自锁/自删）。
                  const isSelf = user.id === currentUserId;
                  const canManageUser = canWrite && !isSelf && (user.role !== 'admin' || isSuperAdmin);
                  const canResetPwd = canWrite && (isSelf || user.role !== 'admin' || isSuperAdmin);
                  return (
                    <tr key={user.id} className="hover:bg-[#121214]/60 transition-colors">
                      <td className="py-3.5 px-3 border-b border-[#27272a]/60">
                        <div className="flex items-center gap-3">
                          <div
                            className="w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold uppercase shrink-0 text-white"
                            style={{ backgroundColor: MONOGRAM_COLORS[hashIndex(user.name, MONOGRAM_COLORS.length)] }}
                          >
                            {initialsOf(user.name)}
                          </div>
                          <div className="min-w-0">
                            <span className="font-semibold text-white truncate block font-sans inline-flex items-center gap-1">
                              {user.name}
                              {user.isSuperAdmin && (
                                <Crown className="w-3 h-3 text-amber-400 shrink-0" aria-label="超级管理员" />
                              )}
                            </span>
                            <span className="text-[10px] text-slate-500 font-mono block">{user.email}</span>
                          </div>
                        </div>
                      </td>

                      <td className="py-3.5 px-3 border-b border-[#27272a]/60">
                        {/* 点击徽章直接弹角色菜单（Popover 自带定位/翻转/外部点击关闭），
                            不再切换成 Select 输入框——字段样式全程保持徽章原样，零跳变。
                            管理员目标仅超管可改角色（后端守卫兜底）。 */}
                        {canManageUser ? (
                          <Popover
                            trigger="click"
                            open={openRoleMenuUserId === user.id}
                            onOpenChange={(v) => setOpenRoleMenuUserId(v ? user.id : null)}
                            content={
                              <div className="flex flex-col">
                                {assignableRoleOptions.map((o) => (
                                  <button
                                    key={o.value}
                                    onClick={() => handleChangeRole(user.id, o.value)}
                                    className={`px-2.5 py-1.5 text-xs text-left rounded cursor-pointer hover:bg-[#1E5EFF]/10 ${
                                      o.value === user.role ? 'text-[#1E5EFF] font-medium' : 'text-[#fafafa]'
                                    }`}
                                  >
                                    {o.label}
                                  </button>
                                ))}
                              </div>
                            }
                          >
                            <span
                              className={`inline-flex items-center h-7 px-2.5 rounded-full text-[10px] font-bold tracking-wide font-sans cursor-pointer hover:bg-[#121214] ${
                                user.role === 'admin'
                                  ? 'bg-rose-500/10 text-rose-400'
                                  : user.role === 'developer'
                                  ? 'bg-indigo-500/10 text-indigo-400'
                                  : user.role === 'operator'
                                  ? 'bg-amber-500/10 text-amber-500'
                                  : 'bg-slate-900 text-slate-400'
                              }`}
                            >
                              {roleDisplayName(user.role)} ✎
                            </span>
                          </Popover>
                        ) : (
                          <span
                            className={`inline-flex items-center h-7 px-2.5 rounded-full text-[10px] font-bold tracking-wide font-sans ${
                              user.role === 'admin'
                                ? 'bg-rose-500/10 text-rose-400'
                                : user.role === 'developer'
                                ? 'bg-indigo-500/10 text-indigo-400'
                                : user.role === 'operator'
                                ? 'bg-amber-500/10 text-amber-500'
                                : 'bg-slate-900 text-slate-400'
                            }`}
                          >
                            {roleDisplayName(user.role)}
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-3 border-b border-[#27272a]/60">
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                          user.status === 'active'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-500'
                        }`}>
                          <span className={`w-1 h-1 rounded-full ${user.status === 'active' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                          {user.status === 'active' ? '正常激活' : '已停用'}
                        </span>
                      </td>

                      <td className="py-3.5 px-3 text-right space-x-2 border-b border-[#27272a]/60">
                        {canManageUser || canResetPwd ? (
                          <>
                            {canManageUser && (
                              <button
                                onClick={() => handleToggleStatus(user.id, user.status)}
                                className={`text-[10px] uppercase font-bold hover:underline transition cursor-pointer ${
                                  user.status === 'active' ? 'text-red-400 hover:text-red-300' : 'text-emerald-400'
                                }`}
                              >
                                {user.status === 'active' ? '锁定' : '激活'}
                              </button>
                            )}
                            {canResetPwd && (
                              <button
                                onClick={() => {
                                  setResetPwdError(null);
                                  setResetPwdValue('');
                                  setResetPwdTarget(user);
                                }}
                                className="text-[10px] uppercase font-bold text-slate-500 hover:text-amber-400 transition cursor-pointer"
                              >
                                重置密码
                              </button>
                            )}
                            {canManageUser && (
                              <button
                                onClick={() => void handleDeleteUser(user)}
                                className="text-[10px] uppercase font-bold text-slate-500 hover:text-rose-400 transition cursor-pointer"
                              >
                                删除
                              </button>
                            )}
                          </>
                        ) : (
                          <span className="text-[10px] text-[#52525b]">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* 分页条：服务端分页（page/page_size → /users），翻页不清空搜索条件 */}
          {total > 0 && (
            <div className="flex items-center justify-between pt-1 text-[10px] text-[#71717a] font-mono">
              <span>
                第 {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} 条 / 共 {total} 条
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1 || isFetching}
                  className="p-1 rounded-md border border-[#27272a] text-slate-400 hover:text-white hover:bg-[#121214] transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  title="上一页"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <span className="px-2 tabular-nums">{page} / {totalPages}</span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages || isFetching}
                  className="p-1 rounded-md border border-[#27272a] text-slate-400 hover:text-white hover:bg-[#121214] transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  title="下一页"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ROLES PANEL (管理并入此页) */}
        <div className="p-5 bg-[#18181b] border border-[#27272a] rounded-xl flex flex-col min-h-0 shadow-lg">
          <div className="flex items-center justify-between shrink-0">
            <h3 className="text-xs font-semibold text-slate-400 tracking-wider uppercase flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5 text-indigo-400" />
              角色管理 ({roles.length})
            </h3>
            {canWrite && (
              <button
                onClick={openRoleCreate}
                className="text-[10px] text-indigo-400 hover:underline cursor-pointer"
              >
                + 新建角色
              </button>
            )}
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto scrollbar-custom overscroll-contain space-y-2 mt-4 pr-1">
            {roles.map((r) => (
              <div key={r.id} className="p-3 bg-[#121214]/60 rounded-lg border border-[#27272a]">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-[#fafafa]">{r.display_name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] text-[#71717a] font-mono">{r.role_type}</span>
                    {canWrite && (
                      <>
                        <button
                          onClick={() => openRoleEdit(r)}
                          className="text-[10px] uppercase font-bold text-indigo-400 hover:text-indigo-300 transition cursor-pointer"
                        >
                          编辑
                        </button>
                        {r.role_type === 'custom' && (
                          <button
                            onClick={() => handleDeleteRole(r)}
                            className="text-[10px] uppercase font-bold text-slate-500 hover:text-rose-400 transition cursor-pointer"
                          >
                            删除
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
                <p className="text-[10px] text-[#a1a1aa] mt-1 leading-relaxed">
                  {r.description || '—'}
                </p>
                <div className="flex flex-wrap gap-1 mt-2">
                  {r.permissions.slice(0, 6).map((p) => (
                    <span key={p} className="px-1.5 py-0.5 rounded bg-[#18181b] border border-[#27272a] text-[9px] text-slate-400 font-mono">
                      {p}
                    </span>
                  ))}
                  {r.permissions.length > 6 && (
                    <span className="text-[9px] text-[#71717a]">+{r.permissions.length - 6}</span>
                  )}
                </div>
              </div>
            ))}
            {roles.length === 0 && (
              <p className="text-[10px] text-[#71717a]">暂无角色，后端共 {allPerms.length} 个权限点。</p>
            )}
          </div>
        </div>
      </div>

      {/* CREATE USER DIALOG MODAL */}
      {isAdding && (
        <div id="modal_create_user" className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-md bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl relative">
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
              <h3 className="text-normal font-sans font-bold text-[#fafafa] flex items-center gap-1.5">
                <Plus className="w-4 h-4 text-emerald-400" />
                新增团队成员
              </h3>
              <button onClick={() => { setCreateFormError(null); setIsAdding(false); }} className="text-slate-500 hover:text-slate-300 font-bold cursor-pointer">✕</button>
            </div>

            <form onSubmit={handleCreateUser} className="p-5 space-y-4 text-xs">
              {createFormError && (
                <div className="px-3 py-2 rounded-lg bg-rose-950/30 border border-rose-700/40 text-rose-300 text-xs whitespace-pre-line">
                  {createFormError.fieldErrors
                    ? Object.entries(createFormError.fieldErrors)
                        .map(([field, msgs]) =>
                          field === '_form' ? msgs.join('；') : `${field}: ${msgs.join('；')}`,
                        )
                        .join('\n')
                    : createFormError.message}
                </div>
              )}

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">成员用户名</label>
                <input type="text" required value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="如: chenming"
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-emerald-500 transition font-sans" />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">邮箱 (Email)</label>
                <input type="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} placeholder="如: chen.ming@linkgraph.ai"
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-emerald-500 transition font-sans" />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans flex items-center justify-between">
                  <span>初始密码</span>
                  <span className={`text-[10px] font-mono ${newPassword.length > 0 && newPassword.length < 8 ? 'text-rose-400' : 'text-slate-500'}`}>
                    {newPassword.length}/8 位
                  </span>
                </label>
                <input type="password" required minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="至少 8 位"
                  className={`w-full px-3 py-2 bg-[#121214] rounded-lg text-slate-200 focus:outline-none transition font-sans ${
                    newPassword.length > 0 && newPassword.length < 8
                      ? 'border border-rose-700/60 focus:border-rose-500'
                      : 'border border-[#27272a] focus:border-emerald-500'
                  }`} />
                {newPassword.length > 0 && newPassword.length < 8 && (
                  <p className="text-[10px] text-rose-400 font-sans">密码至少需要 8 个字符</p>
                )}
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">赋予初始角色</label>
                <Select
                  value={newRole}
                  onChange={(v) => setNewRole(v ?? 'viewer')}
                  placeholder="选择角色"
                  options={assignableRoleOptions}
                />
              </div>

              <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end gap-3 pt-4">
                <button type="button" onClick={() => { setCreateFormError(null); setIsAdding(false); }}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold">
                  取消
                </button>
                <button type="submit" disabled={createM.isPending}
                  className="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg shadow-md cursor-pointer font-sans disabled:opacity-60">
                  {createM.isPending ? '创建中…' : '挂载该成员'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* RESET PASSWORD MODAL（admin 重置成员密码，POST /users/{id}/reset-password） */}
      {resetPwdTarget && (
        <div id="modal_reset_password" className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-md bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl relative">
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
              <h3 className="text-normal font-sans font-bold text-[#fafafa] flex items-center gap-1.5">
                <KeyRound className="w-4 h-4 text-amber-400" />
                重置密码 — {resetPwdTarget.name}
              </h3>
              <button
                onClick={() => { setResetPwdError(null); setResetPwdTarget(null); }}
                className="text-slate-500 hover:text-slate-300 font-bold cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleResetPassword} className="p-5 space-y-4 text-xs">
              <p className="text-slate-400 font-sans">
                重置后原密码立即失效，请将新密码告知该用户。
              </p>

              {resetPwdError && (
                <div className="px-3 py-2 rounded-lg bg-rose-950/30 border border-rose-700/40 text-rose-300 text-xs whitespace-pre-line">
                  {resetPwdError.fieldErrors
                    ? Object.entries(resetPwdError.fieldErrors)
                        .map(([field, msgs]) =>
                          field === '_form' ? msgs.join('；') : `${field}: ${msgs.join('；')}`,
                        )
                        .join('\n')
                    : resetPwdError.message}
                </div>
              )}

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans flex items-center justify-between">
                  <span>新密码</span>
                  <span className={`text-[10px] font-mono ${resetPwdValue.length > 0 && resetPwdValue.length < 8 ? 'text-rose-400' : 'text-slate-500'}`}>
                    {resetPwdValue.length}/8 位
                  </span>
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    required
                    minLength={8}
                    autoFocus
                    value={resetPwdValue}
                    onChange={(e) => setResetPwdValue(e.target.value)}
                    placeholder="至少 8 位，可点右侧随机生成"
                    className={`flex-1 px-3 py-2 bg-[#121214] rounded-lg text-slate-200 focus:outline-none transition font-mono ${
                      resetPwdValue.length > 0 && resetPwdValue.length < 8
                        ? 'border border-rose-700/60 focus:border-rose-500'
                        : 'border border-[#27272a] focus:border-emerald-500'
                    }`}
                  />
                  <button
                    type="button"
                    onClick={() => setResetPwdValue(generatePassword())}
                    title="重新生成 12 位随机强密码"
                    className="px-3 py-2 border border-[#27272a] rounded-lg text-slate-400 hover:text-white hover:bg-[#121214] transition cursor-pointer shrink-0 flex items-center gap-1.5"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> 随机
                  </button>
                </div>
                {resetPwdValue.length > 0 && resetPwdValue.length < 8 && (
                  <p className="text-[10px] text-rose-400 font-sans">密码至少需要 8 个字符</p>
                )}
              </div>

              <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end gap-3 pt-4">
                <button
                  type="button"
                  onClick={() => { setResetPwdError(null); setResetPwdTarget(null); }}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold"
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={resetPwdM.isPending}
                  className="px-5 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg shadow-md cursor-pointer font-sans disabled:opacity-60"
                >
                  {resetPwdM.isPending ? '重置中…' : '重置密码'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ROLE CREATE/EDIT DIALOG MODAL */}
      {roleModalMode !== null && (
        <div id="modal_create_role" className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-lg bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl relative">
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
              <h3 className="text-normal font-sans font-bold text-[#fafafa] flex items-center gap-1.5">
                {roleModalMode === 'create' ? (
                  <>
                    <Plus className="w-4 h-4 text-indigo-400" />
                    新建角色（POST /roles）
                  </>
                ) : (
                  <>
                    <Pencil className="w-4 h-4 text-indigo-400" />
                    编辑角色（PATCH /roles/{editingRole?.id}）
                  </>
                )}
              </h3>
              <button onClick={closeRoleModal} className="text-slate-500 hover:text-slate-300 font-bold cursor-pointer">✕</button>
            </div>

            <form onSubmit={handleRoleSubmit} className="p-5 space-y-4 text-xs">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-slate-400 font-medium font-sans">角色 key (小写)</label>
                  <input
                    type="text"
                    required
                    pattern="[a-z][a-z0-9_]*"
                    value={roleName}
                    onChange={(e) => setRoleName(e.target.value)}
                    readOnly={roleModalMode === 'edit'}
                    placeholder="如: content_editor"
                    className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-mono read-only:opacity-60 read-only:cursor-not-allowed"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-slate-400 font-medium font-sans">
                    显示名{isEditingSystemRole ? '（系统角色不可改）' : ''}
                  </label>
                  <input
                    type="text"
                    required
                    value={roleDisplay}
                    onChange={(e) => setRoleDisplay(e.target.value)}
                    disabled={isEditingSystemRole}
                    placeholder="如: 内容编辑"
                    className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-sans disabled:opacity-60 disabled:cursor-not-allowed"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">描述{isEditingSystemRole ? '（系统角色不可改）' : ''}</label>
                <input
                  type="text"
                  value={roleDesc}
                  onChange={(e) => setRoleDesc(e.target.value)}
                  disabled={isEditingSystemRole}
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-sans disabled:opacity-60 disabled:cursor-not-allowed"
                />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">权限点 ({rolePerms.size}/{allPerms.length})</label>
                <PermissionTree selected={rolePerms} onChange={setRolePerms} />
              </div>

              <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end gap-3 pt-4">
                <button type="button" onClick={closeRoleModal}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold">
                  取消
                </button>
                <button type="submit" disabled={roleFormPending}
                  className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-md cursor-pointer font-sans disabled:opacity-60">
                  {roleFormPending
                    ? roleModalMode === 'edit' ? '保存中…' : '创建中…'
                    : roleModalMode === 'edit' ? '保存修改' : '创建角色'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
