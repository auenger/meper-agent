/**
 * ExternalAuthPage — 外部授权（服务卡片 + 内联应用管理）。
 *
 * 所有登录用户：查看可授权的应用卡片，点击授权 → 弹窗填 username/password
 *   → 后端调 login_url 验证 + 自动建立身份映射。
 * 有 application:write 权限的用户：卡片上直接显示编辑/删除按钮（数据源切换
 *   为全部应用，含未配置登录验证的），页面头显示「创建应用」。
 *
 * Ported from frontend/src/pages/external-auth-page.tsx, native Tailwind
 * (lucide icons). MCP 绑定编辑器：按分组折叠 + 整组添加/移除 + 单个勾选。
 */
import { useState, useMemo, type FormEvent, type ReactNode } from 'react';
import {
  Blocks, Plus, Pencil, Trash2, X, Loader2, Folder,
  ChevronDown, ChevronRight, Plug,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../stores/auth-store';
import {
  myAppAuthorizationsApi,
  myAppAuthorizationKeys,
  type AvailableApp,
} from '../services/my-app-authorizations-api';
import {
  applicationApi,
  applicationKeys,
  type Application,
  type ApplicationCreateInput,
} from '../services/application-api';
import { mcpApi, mcpKeys, type McpConnection } from '../services/mcp-api';
import { mcpCategoryApi, mcpCategoryKeys, type McpCategory } from '../services/mcp-category-api';
import { Select } from './ui';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';
import { getErrorMessage } from '../lib/api-client';

/** 卡片视图模型：归一化「全部应用」与「可授权应用」两种数据源。 */
interface AppCard {
  id: string;
  name: string;
  description: string;
  mcp_count: number;
  /** 是否已配置登录验证（未配置的应用普通用户不可见、不可授权）。 */
  configured: boolean;
}

export function ExternalAuthPage() {
  const queryClient = useQueryClient();
  const canManage = useAuthStore((s) => s.user?.permissions?.includes('application:write')) ?? false;

  /* ─── 授权弹窗 state ─── */
  const [bindModalApp, setBindModalApp] = useState<AvailableApp | null>(null);
  const [formUsername, setFormUsername] = useState('');
  const [formPassword, setFormPassword] = useState('');

  /* ─── 应用编辑弹窗 state ─── */
  const [appModalOpen, setAppModalOpen] = useState(false);
  const [editingApp, setEditingApp] = useState<Application | null>(null);
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formMcpIds, setFormMcpIds] = useState<string[]>([]);
  const [formLoginUrl, setFormLoginUrl] = useState('');
  const [formLoginMethod, setFormLoginMethod] = useState('POST');
  const [formUsernameField, setFormUsernameField] = useState('username');
  const [formPasswordField, setFormPasswordField] = useState('password');
  const [formTokenJsonpath, setFormTokenJsonpath] = useState('data.token');
  const [formSessionTtl, setFormSessionTtl] = useState(3600);

  /* ─── Queries ─── */
  const { data: credData, isLoading: credLoading } = useQuery({
    queryKey: myAppAuthorizationKeys.detail(),
    queryFn: () => myAppAuthorizationsApi.get(),
  });

  const { data: appsData, isLoading: appsLoading } = useQuery({
    queryKey: myAppAuthorizationKeys.availableApps(),
    queryFn: () => myAppAuthorizationsApi.availableApps(),
  });

  // admin 看全部应用（含未配置验证的），作为卡片数据源 + 编辑弹窗原始对象。
  const { data: adminAppsData, isLoading: adminAppsLoading } = useQuery({
    queryKey: applicationKeys.lists(),
    queryFn: () => applicationApi.list(),
    enabled: canManage,
  });

  const bindingMap = useMemo(
    () => new Map((credData?.bindings ?? []).map((b) => [b.app_id, b])),
    [credData],
  );

  /* 归一化卡片列表：canManage → 全部应用；否则 → 可授权应用。 */
  const cards = useMemo<AppCard[]>(() => {
    if (canManage) {
      return (adminAppsData?.items ?? []).map((a) => ({
        id: a.id,
        name: a.name,
        description: a.description ?? '',
        mcp_count: a.mcp_connection_ids?.length ?? 0,
        configured: !!(a.login_config as Record<string, unknown>)?.login_url,
      }));
    }
    return (appsData?.items ?? []).map((a) => ({
      id: a.id,
      name: a.name,
      description: a.description,
      mcp_count: a.mcp_count,
      configured: true,
    }));
  }, [canManage, adminAppsData, appsData]);

  const adminAppMap = useMemo(
    () => new Map((adminAppsData?.items ?? []).map((a) => [a.id, a])),
    [adminAppsData],
  );

  const isLoading = credLoading || (canManage ? adminAppsLoading : appsLoading);

  /* ─── Mutations ─── */
  const bindMutation = useMutation({
    mutationFn: ({ appId, input }: { appId: string; input: { username: string; password: string } }) =>
      myAppAuthorizationsApi.authorize(appId, input),
    onSuccess: () => {
      toast.success('授权成功');
      queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all });
      closeBindModal();
    },
    onError: (e) => toast.error(getErrorMessage(e, '授权失败')),
  });

  const revokeMutation = useMutation({
    mutationFn: (appId: string) => myAppAuthorizationsApi.revoke(appId),
    onSuccess: () => {
      toast.success('已取消授权');
      queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all });
    },
    onError: (e) => toast.error(getErrorMessage(e, '取消授权失败')),
  });

  const saveAppM = useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ApplicationCreateInput }) =>
      id ? applicationApi.update(id, input) : applicationApi.create(input),
    onSuccess: (_, { id }) => {
      toast.success(id ? '应用已更新' : '应用创建成功');
      invalidateApps();
      closeAppModal();
    },
    onError: (e) => toast.error(getErrorMessage(e, '保存失败')),
  });

  const deleteAppM = useMutation({
    mutationFn: (id: string) => applicationApi.remove(id),
    onSuccess: () => {
      toast.success('应用已删除');
      invalidateApps();
    },
    onError: (e) => toast.error(getErrorMessage(e, '删除失败')),
  });

  const invalidateApps = () => {
    queryClient.invalidateQueries({ queryKey: applicationKeys.all });
    queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all });
  };

  /* ─── 授权 Actions ─── */
  const openBindModal = (app: AppCard) => {
    setBindModalApp(app);
    setFormUsername(bindingMap.get(app.id)?.username ?? '');
    setFormPassword('');
  };

  const closeBindModal = () => {
    setBindModalApp(null);
    setFormUsername('');
    setFormPassword('');
  };

  const handleAuthorize = (e: FormEvent) => {
    e.preventDefault();
    if (!formUsername.trim() || !formPassword.trim()) {
      toast.error('请填写用户名和密码');
      return;
    }
    if (bindModalApp) {
      bindMutation.mutate({
        appId: bindModalApp.id,
        input: { username: formUsername.trim(), password: formPassword },
      });
    }
  };

  const handleRevoke = async (app: AppCard) => {
    const ok = await confirmDialog({
      title: `取消授权「${app.name}」？`,
      description: '取消后相关 MCP 将无法访问。',
      okText: '取消授权',
      danger: true,
    });
    if (ok) revokeMutation.mutate(app.id);
  };

  /* ─── 应用管理 Actions（application:write） ─── */
  const openCreateApp = () => {
    setEditingApp(null);
    setFormName(''); setFormDescription(''); setFormMcpIds([]);
    setFormLoginUrl(''); setFormLoginMethod('POST');
    setFormUsernameField('username'); setFormPasswordField('password');
    setFormTokenJsonpath('data.token'); setFormSessionTtl(3600);
    setAppModalOpen(true);
  };

  const openEditApp = (app: Application) => {
    setEditingApp(app);
    setFormName(app.name); setFormDescription(app.description || '');
    setFormMcpIds(app.mcp_connection_ids ?? []);
    const lc = (app.login_config ?? {}) as Record<string, unknown>;
    setFormLoginUrl(lc.login_url as string ?? '');
    setFormLoginMethod(lc.method as string ?? 'POST');
    setFormUsernameField(lc.username_field as string ?? 'username');
    setFormPasswordField(lc.password_field as string ?? 'password');
    setFormTokenJsonpath(lc.token_jsonpath as string ?? 'data.token');
    setFormSessionTtl(lc.session_ttl as number ?? 3600);
    setAppModalOpen(true);
  };

  const closeAppModal = () => {
    setAppModalOpen(false);
    setEditingApp(null);
  };

  const handleAppSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) {
      toast.error('请填写应用名称');
      return;
    }
    const input: ApplicationCreateInput = {
      name: formName.trim(),
      description: formDescription.trim(),
      mcp_connection_ids: formMcpIds,
      login_config: formLoginUrl.trim() ? {
        login_url: formLoginUrl.trim(),
        method: formLoginMethod || 'POST',
        username_field: formUsernameField.trim() || 'username',
        password_field: formPasswordField.trim() || 'password',
        token_jsonpath: formTokenJsonpath.trim() || 'data.token',
        session_ttl: formSessionTtl || 3600,
      } : {},
    };
    saveAppM.mutate({ id: editingApp?.id ?? null, input });
  };

  const handleDeleteApp = async (app: AppCard) => {
    const ok = await confirmDialog({
      title: `删除应用「${app.name}」？`,
      description: '若应用仍绑定了 MCP 将无法删除。',
      okText: '删除',
      danger: true,
    });
    if (ok) deleteAppM.mutate(app.id);
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Blocks className="w-5 h-5 text-indigo-400" />
            外部授权
          </h2>
          <p className="text-xs text-[#71717a] mt-1">
            连接你的外部应用。授权后，Agent 可以在你的业务权限范围内访问应用下的 MCP 资源。
          </p>
        </div>
        {canManage && (
          <button onClick={openCreateApp}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-semibold transition cursor-pointer shadow-md shadow-indigo-600/20">
            <Plus className="w-4 h-4" /> 创建应用
          </button>
        )}
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-16 text-[#71717a]">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中…
        </div>
      )}

      {/* Empty */}
      {!isLoading && cards.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[#52525b]">
          <Plug className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-sm">暂无应用</p>
          <p className="text-xs mt-1">
            {canManage ? '点击右上角「创建应用」开始' : '请联系管理员配置应用'}
          </p>
        </div>
      )}

      {/* App cards */}
      {!isLoading && cards.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3.5">
          {cards.map((app) => {
            const binding = bindingMap.get(app.id);
            const bound = !!binding;
            return (
              <div
                key={app.id}
                className="min-w-0 p-4 rounded-xl border border-[#27272a] bg-[#18181b] transition-colors hover:border-indigo-500/40"
              >
                {/* Header */}
                <div className="flex justify-between items-start gap-2 mb-3">
                  <div className="flex gap-2.5 items-center min-w-0">
                    <span className="flex-shrink-0 w-10 h-10 grid place-items-center rounded-lg bg-indigo-500/10 text-indigo-400">
                      <Plug className="w-5 h-5" />
                    </span>
                    <div className="min-w-0">
                      <strong className="block text-[13px] text-white truncate">{app.name}</strong>
                      {app.description && (
                        <span className="block mt-0.5 text-[10px] text-[#71717a] truncate">{app.description}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {app.configured ? (
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        bound ? 'text-emerald-400 bg-emerald-500/10' : 'text-amber-400 bg-amber-500/10'
                      }`}>
                        {bound ? '已授权' : '未授权'}
                      </span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold text-[#71717a] bg-[#27272a]">
                        未配置验证
                      </span>
                    )}
                    {canManage && (
                      <>
                        <button onClick={() => {
                          const raw = adminAppMap.get(app.id);
                          if (raw) openEditApp(raw);
                        }} title="编辑"
                          className="p-1 rounded-lg text-[#71717a] hover:text-indigo-400 hover:bg-[#27272a] transition cursor-pointer">
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDeleteApp(app)} title="删除" disabled={deleteAppM.isPending}
                          className="p-1 rounded-lg text-[#71717a] hover:text-rose-400 hover:bg-[#27272a] transition cursor-pointer disabled:opacity-40">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Details */}
                <div className="grid gap-1.5 my-3 p-2.5 rounded-lg bg-[#121214]">
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#71717a]">包含 MCP</span>
                    <strong className="text-white font-medium">{app.mcp_count} 个服务</strong>
                  </div>
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#71717a]">外部账号</span>
                    <strong className="text-white font-medium overflow-hidden text-ellipsis">
                      {bound ? (binding!.username || '—') : '尚未绑定'}
                    </strong>
                  </div>
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#71717a]">凭证状态</span>
                    <strong className={`font-medium ${bound ? 'text-emerald-400' : 'text-[#71717a]'}`}>
                      {bound ? (binding!.password_masked || '已加密') : '—'}
                    </strong>
                  </div>
                </div>

                {/* Action */}
                {app.configured ? (
                  <>
                    <button
                      onClick={() => openBindModal(app)}
                      disabled={revokeMutation.isPending}
                      className={`w-full min-h-[36px] rounded-lg text-[11px] font-bold transition disabled:opacity-40 cursor-pointer ${
                        bound
                          ? 'border border-indigo-500/40 text-indigo-400 bg-transparent hover:bg-indigo-500/10'
                          : 'bg-indigo-600 text-white hover:bg-indigo-500'
                      }`}
                    >
                      {bound ? '重新认证' : `连接 ${app.name}`}
                    </button>
                    {bound && (
                      <button
                        onClick={() => handleRevoke(app)}
                        disabled={revokeMutation.isPending}
                        className="w-full mt-1.5 min-h-[28px] rounded-lg text-[10px] text-[#71717a] hover:text-rose-400 hover:bg-rose-500/5 transition-colors cursor-pointer disabled:opacity-40"
                      >
                        取消授权
                      </button>
                    )}
                  </>
                ) : (
                  <p className="text-[10px] text-[#71717a] text-center min-h-[36px] flex items-center justify-center">
                    尚未配置登录验证，用户暂不可授权
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Authorize Modal */}
      {bindModalApp && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-sm bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a]">
              <h3 className="text-sm font-bold text-white">
                {bindingMap.has(bindModalApp.id) ? '重新授权' : '授权应用'}
              </h3>
              <button onClick={closeBindModal}
                className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            <form onSubmit={handleAuthorize} className="p-5 space-y-3 text-xs">
              <div className="rounded-lg bg-[#18181b] px-3 py-2">
                <div className="text-[#71717a]">
                  应用：<span className="text-white font-medium">{bindModalApp.name}</span>
                </div>
              </div>
              <Field label="用户名 *">
                <input
                  value={formUsername}
                  onChange={(e) => setFormUsername(e.target.value)}
                  placeholder="你在该系统的用户名"
                  className={inputCls}
                  autoComplete="off"
                />
              </Field>
              <Field label="密码 *">
                <input
                  type="password"
                  value={formPassword}
                  onChange={(e) => setFormPassword(e.target.value)}
                  placeholder="你在该系统的密码"
                  className={inputCls}
                  autoComplete="new-password"
                />
              </Field>
              <p className="text-[10px] text-[#71717a]">
                授权时系统会调该应用的登录端点验证账号密码，验证通过后凭证加密存储。
              </p>
              <div className="flex justify-end gap-3 pt-3 border-t border-[#27272a]">
                <button type="button" onClick={closeBindModal}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold">取消</button>
                <button type="submit" disabled={bindMutation.isPending || !formUsername.trim() || !formPassword.trim()}
                  className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-md cursor-pointer font-semibold disabled:opacity-60 flex items-center gap-2">
                  {bindMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  授权
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* App Create/Edit Modal（application:write） */}
      {appModalOpen && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a] sticky top-0 bg-[#121214] z-10">
              <h3 className="text-sm font-bold text-white">
                {editingApp ? `编辑应用 — ${editingApp.name}` : '创建应用'}
              </h3>
              <button onClick={closeAppModal}
                className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            <form onSubmit={handleAppSubmit} className="p-5 space-y-4 text-xs">
              <div className="grid grid-cols-2 gap-4">
                <Field label="应用名称 *"><input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="如：OA 系统" maxLength={100} className={inputCls} /></Field>
                <Field label="描述"><input value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="应用描述（可选）" maxLength={500} className={inputCls} /></Field>
              </div>

              {/* MCP 绑定编辑器 */}
              <Field label={`绑定 MCP 资源（已选 ${formMcpIds.length} 个）`}>
                <McpBindingEditor selected={formMcpIds} onChange={setFormMcpIds} />
              </Field>

              {/* 登录验证配置 */}
              <details className="rounded-lg border border-[#27272a] px-3 py-2" open={!formLoginUrl}>
                <summary className="text-[11px] font-medium text-[#a1a1aa] cursor-pointer select-none">
                  登录验证配置（配置后用户可授权此应用）
                </summary>
                <div className="flex flex-col gap-2.5 mt-2.5">
                  <div className="grid grid-cols-[1fr_80px] gap-2">
                    <Field label="登录端点 URL">
                      <input value={formLoginUrl} onChange={(e) => setFormLoginUrl(e.target.value)} placeholder="https://example.com/api/login" className={inputCls} />
                    </Field>
                    <Field label="方法">
                      <Select
                        value={formLoginMethod}
                        onChange={(v) => setFormLoginMethod(v ?? 'POST')}
                        options={[{ value: 'POST', label: 'POST' }, { value: 'GET', label: 'GET' }]}
                      />
                    </Field>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <Field label="用户名字段名"><input value={formUsernameField} onChange={(e) => setFormUsernameField(e.target.value)} placeholder="username" className={inputCls} /></Field>
                    <Field label="密码字段名"><input value={formPasswordField} onChange={(e) => setFormPasswordField(e.target.value)} placeholder="password" className={inputCls} /></Field>
                    <Field label="Token JSONPath"><input value={formTokenJsonpath} onChange={(e) => setFormTokenJsonpath(e.target.value)} placeholder="data.token" className={inputCls} /></Field>
                  </div>
                  <Field label="Session 缓存秒数">
                    <input type="number" min="60" value={formSessionTtl} onChange={(e) => setFormSessionTtl(Number(e.target.value))} className={inputCls} />
                  </Field>
                </div>
              </details>

              <div className="flex justify-end gap-3 pt-4 border-t border-[#27272a]">
                <button type="button" onClick={closeAppModal}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold">取消</button>
                <button type="submit" disabled={saveAppM.isPending || !formName.trim()}
                  className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-md cursor-pointer font-semibold disabled:opacity-60 flex items-center gap-2">
                  {saveAppM.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {editingApp ? '保存' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
 * MCP 绑定编辑器：分组折叠 + 整组添加/移除 + 单个勾选
 * ═══════════════════════════════════════════════════════════ */

function McpBindingEditor({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const { data: mcpData } = useQuery({
    queryKey: mcpKeys.list({ page: 1, page_size: 200 }),
    queryFn: () => mcpApi.list({ page: 1, page_size: 200 }),
  });
  const { data: catData } = useQuery({
    queryKey: mcpCategoryKeys.lists(),
    queryFn: () => mcpCategoryApi.list(),
  });

  const connections = useMemo(() => mcpData?.items ?? [], [mcpData]);
  const categories = useMemo(() => catData?.items ?? [], [catData]);

  /* 按分组分桶，未分组排最后 */
  const buckets = useMemo(() => {
    const byCat = new Map<string, McpConnection[]>();
    for (const c of connections) {
      const key = c.category_id || '__ungrouped__';
      if (!byCat.has(key)) byCat.set(key, []);
      byCat.get(key)!.push(c);
    }
    const result: { key: string; name: string; conns: McpConnection[] }[] = [];
    for (const cat of categories) {
      if (byCat.has(cat.id)) {
        result.push({ key: cat.id, name: cat.name, conns: byCat.get(cat.id)! });
      }
    }
    if (byCat.has('__ungrouped__')) {
      result.push({ key: '__ungrouped__', name: '未分组', conns: byCat.get('__ungrouped__')! });
    }
    return result;
  }, [connections, categories]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const toggleGroup = (ids: string[], check: boolean) => {
    if (check) {
      onChange(Array.from(new Set([...selected, ...ids])));
    } else {
      onChange(selected.filter((id) => !ids.includes(id)));
    }
  };

  const toggleOne = (id: string, check: boolean) => {
    if (check) onChange([...selected, id]);
    else onChange(selected.filter((x) => x !== id));
  };

  if (connections.length === 0) {
    return <div className="text-xs text-[#71717a] py-3 text-center border border-dashed border-[#27272a] rounded-lg">暂无 MCP 连接</div>;
  }

  return (
    <div className="border border-[#27272a] rounded-lg max-h-[260px] overflow-y-auto flex flex-col bg-[#121214]">
      {buckets.map((bucket) => {
        const isCollapsed = collapsed[bucket.key];
        const groupIds = bucket.conns.map((c) => c.id);
        const selectedCount = groupIds.filter((id) => selectedSet.has(id)).length;
        const allSelected = selectedCount === groupIds.length;
        return (
          <div key={bucket.key} className="flex flex-col border-b border-[#27272a] last:border-b-0">
            <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#18181b]">
              <button
                type="button"
                onClick={() => setCollapsed((s) => ({ ...s, [bucket.key]: !s[bucket.key] }))}
                className="flex items-center gap-1.5 text-white hover:text-indigo-400 transition-colors cursor-pointer"
              >
                {isCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                <Folder className="text-indigo-400 w-3.5 h-3.5" />
                <span className="text-xs font-medium">{bucket.name}</span>
                <span className="text-[10px] text-[#71717a]">（{selectedCount}/{groupIds.length}）</span>
              </button>
              <button
                type="button"
                onClick={() => toggleGroup(groupIds, !allSelected)}
                className="text-[10px] text-indigo-400 hover:underline cursor-pointer"
              >
                {allSelected ? '移除整组' : '添加整组'}
              </button>
            </div>
            {!isCollapsed && (
              <div className="divide-y divide-[#27272a]/60">
                {bucket.conns.map((c) => {
                  const checked = selectedSet.has(c.id);
                  return (
                    <label key={c.id} className="flex items-center justify-between px-3 py-2 hover:bg-[#18181b] transition-colors cursor-pointer">
                      <div className="flex items-center gap-2 min-w-0">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => toggleOne(c.id, e.target.checked)}
                          className="accent-indigo-500"
                        />
                        <Plug className="text-emerald-400 w-3.5 h-3.5" />
                        <span className="text-xs text-white truncate">{c.name}</span>
                      </div>
                      <span className={`text-[11px] shrink-0 ${
                        c.status === 'connected' ? 'text-emerald-400' :
                        c.status === 'error' ? 'text-rose-400' : 'text-[#71717a]'
                      }`}>
                        {c.status === 'connected' ? '已连接' : c.status === 'error' ? '异常' : '已断开'}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const inputCls = 'w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 transition text-xs font-sans';

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-slate-400 font-medium font-sans">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-[#71717a] leading-relaxed">{hint}</p>}
    </div>
  );
}
