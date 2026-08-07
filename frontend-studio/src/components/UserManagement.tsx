import { useState, FormEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users, Shield, Search, Plus, Lock,
} from 'lucide-react';
import { userApi } from '../services/user-api';
import { roleApi } from '../services/role-api';
import { toStudioUser } from '../services/adapters';
import { Select } from './ui';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';
import { PermissionTree } from './ui/PermissionTree';
import { getErrorMessage, type NormalizedApiError } from '../lib/api-client';
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

export function UserManagement() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedUserForRole, setSelectedUserForRole] = useState<string | null>(null);

  // New user form state
  const [isAdding, setIsAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<string>('viewer');
  // Field-level / form-level error rendered inside the create-user modal so
  // the user sees why it failed instead of the dialog silently closing.
  const [createFormError, setCreateFormError] = useState<NormalizedApiError | null>(null);

  // New role form state
  const [isAddingRole, setIsAddingRole] = useState(false);
  const [roleName, setRoleName] = useState('');
  const [roleDisplay, setRoleDisplay] = useState('');
  const [roleDesc, setRoleDesc] = useState('');
  const [rolePerms, setRolePerms] = useState<Set<string>>(new Set());

  const { data: usersData, isLoading } = useQuery({
    queryKey: ['users', { page: 1, page_size: 50 }],
    queryFn: async () => (await userApi.list({ page: 1, page_size: 50 })).data,
  });
  const { data: rolesData } = useQuery({
    queryKey: ['roles'],
    queryFn: async () => (await roleApi.list()).data,
  });
  const { data: allPermsData } = useQuery({
    queryKey: ['roles', 'permissions'],
    queryFn: async () => (await roleApi.getAllPermissions()).data,
  });

  const users = (usersData?.items ?? []).map(toStudioUser);
  const roles = rolesData ?? [];
  const allPerms = allPermsData?.permissions ?? [];

  // Drive role selectors from the dynamic roles list (system + custom). The
  // backend role `name` is the value we submit/store; `display_name` is what
  // the user sees. This lets custom roles created in the side panel be
  // assigned to users instead of being hidden behind a hardcoded list.
  const roleOptions = roles.map((r) => ({ value: r.name, label: r.display_name }));
  const roleDisplayName = (key: string): string =>
    roles.find((r) => r.name === key)?.display_name ?? key;

  const filteredUsers = users.filter(
    (u) =>
      u.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      u.email.toLowerCase().includes(searchQuery.toLowerCase()),
  );

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
    onError: (e: unknown) => toast.error(getErrorMessage(e, '更新用户失败')),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => userApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
    onError: (e: unknown) => toast.error(getErrorMessage(e, '删除失败')),
  });

  const createRoleM = useMutation({
    mutationFn: (input: { name: string; display_name: string; description?: string; permissions: string[] }) =>
      roleApi.create(input).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      toast.success('角色已创建');
    },
    onError: (e: unknown) => toast.error(getErrorMessage(e, '创建角色失败')),
  });

  const deleteRoleM = useMutation({
    mutationFn: (id: string) => roleApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] });
      toast.success('角色已删除');
    },
    onError: (e: unknown) => toast.error(getErrorMessage(e, '删除角色失败')),
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
    setSelectedUserForRole(null);
  };

  const handleToggleStatus = (userId: string, current: User['status']) => {
    updateM.mutate({
      id: userId,
      body: { status: current === 'active' ? 'disabled' : 'active' },
    });
  };

  const handleCreateRole = (e: FormEvent) => {
    e.preventDefault();
    if (!roleName || !roleDisplay) return;
    createRoleM.mutate({
      name: roleName,
      display_name: roleDisplay,
      description: roleDesc,
      permissions: [...rolePerms],
    });
    setRoleName('');
    setRoleDisplay('');
    setRoleDesc('');
    setRolePerms(new Set());
    setIsAddingRole(false);
  };

  return (
    <div className="space-y-6">
      {/* Top action bar */}
      <div className="flex flex-col sm:flex-row justify-between sm:items-center p-4 bg-[#18181b] rounded-xl border border-[#27272a] gap-4">
        <div className="flex items-center gap-3">
          <Shield className="w-5 h-5 text-emerald-400" />
          <div className="space-y-0.5">
            <h2 className="text-sm font-bold text-[#fafafa] font-sans">RBAC 角色及矩阵鉴权中心</h2>
            <p className="text-xs text-[#a1a1aa] font-sans">配置组织成员角色、管理角色权限点（后端 24 权限字符串）。</p>
          </div>
        </div>

        <div className="flex gap-2.5">
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索团队成员..."
              className="pl-9 pr-4 py-1.5 w-56 text-xs bg-[#121214] border border-[#27272a] rounded-lg text-slate-300 focus:outline-none focus:border-indigo-500 font-sans"
            />
          </div>

          <button
            onClick={() => { setCreateFormError(null); setIsAdding(true); }}
            id="btn_add_user"
            className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl shadow transition cursor-pointer flex items-center gap-1 font-sans"
          >
            <Plus className="w-3.5 h-3.5" />
            新增成员
          </button>
        </div>
      </div>

      {/* RENDER MEMBERS TABLE */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 p-5 bg-[#18181b] border border-[#27272a] rounded-xl space-y-4 shadow-lg">
          <h3 className="text-xs font-semibold text-slate-400 tracking-wider uppercase flex items-center gap-1">
            <Users className="w-3.5 h-3.5 text-indigo-400" />
            团队成员授权列表 ({filteredUsers.length})
          </h3>

          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left text-slate-400 leading-normal">
              <thead>
                <tr className="border-b border-[#27272a] text-[#71717a] font-semibold">
                  <th className="py-2.5 px-3">基本信息</th>
                  <th className="py-2.5 px-3">系统角色</th>
                  <th className="py-2.5 px-3">账号状态</th>
                  <th className="py-2.5 px-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#27272a]/60">
                {isLoading ? (
                  <tr><td colSpan={4} className="py-4 px-3 text-[#71717a]">加载中…</td></tr>
                ) : filteredUsers.map((user) => {
                  return (
                    <tr key={user.id} className="hover:bg-[#121214]/60 transition-colors">
                      <td className="py-3.5 px-3">
                        <div className="flex items-center gap-3">
                          <div
                            className="w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold uppercase shrink-0 text-white"
                            style={{ backgroundColor: MONOGRAM_COLORS[hashIndex(user.name, MONOGRAM_COLORS.length)] }}
                          >
                            {initialsOf(user.name)}
                          </div>
                          <div className="min-w-0">
                            <span className="font-semibold text-white truncate block font-sans">{user.name}</span>
                            <span className="text-[10px] text-slate-500 font-mono block">{user.email}</span>
                          </div>
                        </div>
                      </td>

                      <td className="py-3.5 px-3">
                        {selectedUserForRole === user.id ? (
                          <Select
                            size="small"
                            value={user.role}
                            onChange={(v) => handleChangeRole(user.id, v ?? 'viewer')}
                            className="min-w-[120px]"
                            options={roleOptions}
                          />
                        ) : (
                          <span
                            onClick={() => setSelectedUserForRole(user.id)}
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide font-sans cursor-pointer hover:bg-[#121214] ${
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
                        )}
                      </td>

                      <td className="py-3.5 px-3">
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                          user.status === 'active'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-500'
                        }`}>
                          <span className={`w-1 h-1 rounded-full ${user.status === 'active' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                          {user.status === 'active' ? '正常激活' : '已停用'}
                        </span>
                      </td>

                      <td className="py-3.5 px-3 text-right space-x-2">
                        <button
                          onClick={() => handleToggleStatus(user.id, user.status)}
                          className={`text-[10px] uppercase font-bold hover:underline transition cursor-pointer ${
                            user.status === 'active' ? 'text-red-400 hover:text-red-300' : 'text-emerald-400'
                          }`}
                        >
                          {user.status === 'active' ? '锁定' : '激活'}
                        </button>
                        <button
                          onClick={() => deleteM.mutate(user.id)}
                          className="text-[10px] uppercase font-bold text-slate-500 hover:text-rose-400 transition cursor-pointer"
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* ROLES PANEL (管理并入此页) */}
        <div className="p-5 bg-[#18181b] border border-[#27272a] rounded-xl space-y-4 shadow-lg">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-slate-400 tracking-wider uppercase flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5 text-indigo-400" />
              角色管理 ({roles.length})
            </h3>
            <button
              onClick={() => setIsAddingRole(true)}
              className="text-[10px] text-indigo-400 hover:underline cursor-pointer"
            >
              + 新建角色
            </button>
          </div>

          <div className="space-y-2">
            {roles.map((r) => (
              <div key={r.id} className="p-3 bg-[#121214]/60 rounded-lg border border-[#27272a]">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-[#fafafa]">{r.display_name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] text-[#71717a] font-mono">{r.role_type}</span>
                    {r.role_type === 'custom' && (
                      <button
                        onClick={() => handleDeleteRole(r)}
                        className="text-[10px] uppercase font-bold text-slate-500 hover:text-rose-400 transition cursor-pointer"
                      >
                        删除
                      </button>
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
        <div id="modal_create_user" className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
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
                  options={roleOptions}
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

      {/* CREATE ROLE DIALOG MODAL */}
      {isAddingRole && (
        <div id="modal_create_role" className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-lg bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl relative">
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
              <h3 className="text-normal font-sans font-bold text-[#fafafa] flex items-center gap-1.5">
                <Plus className="w-4 h-4 text-indigo-400" />
                新建角色（POST /roles）
              </h3>
              <button onClick={() => setIsAddingRole(false)} className="text-slate-500 hover:text-slate-300 font-bold cursor-pointer">✕</button>
            </div>

            <form onSubmit={handleCreateRole} className="p-5 space-y-4 text-xs">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-slate-400 font-medium font-sans">角色 key (小写)</label>
                  <input type="text" required pattern="[a-z][a-z0-9_]*" value={roleName}
                    onChange={(e) => setRoleName(e.target.value)} placeholder="如: content_editor"
                    className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-mono" />
                </div>
                <div className="space-y-1">
                  <label className="text-slate-400 font-medium font-sans">显示名</label>
                  <input type="text" required value={roleDisplay}
                    onChange={(e) => setRoleDisplay(e.target.value)} placeholder="如: 内容编辑"
                    className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-sans" />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">描述</label>
                <input type="text" value={roleDesc} onChange={(e) => setRoleDesc(e.target.value)}
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 font-sans" />
              </div>

              <div className="space-y-1">
                <label className="text-slate-400 font-medium font-sans">权限点 ({rolePerms.size}/{allPerms.length})</label>
                <PermissionTree selected={rolePerms} onChange={setRolePerms} />
              </div>

              <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end gap-3 pt-4">
                <button type="button" onClick={() => setIsAddingRole(false)}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold">
                  取消
                </button>
                <button type="submit" disabled={createRoleM.isPending}
                  className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-md cursor-pointer font-sans disabled:opacity-60">
                  {createRoleM.isPending ? '创建中…' : '创建角色'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
