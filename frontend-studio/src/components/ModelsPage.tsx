/**
 * ModelsPage — standalone LLM model configuration surface.
 *
 * Full CRUD + connectivity test + api_key masking + search/stats. All field
 * handling mirrors the legacy umi models-page but renders with native Tailwind
 * (no antd). Backed by services/model-api.ts (already complete); backend has
 * zero changes for this feature.
 *
 * Permission gating: read needs `model:read`, write/test needs `model:write`
 * (the nav entry hides this page entirely for users lacking model:read).
 */
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  Plus, Search, Server, Pencil, Trash2, Zap, Loader2, X, AlertTriangle, CheckCircle2,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  modelApi,
  modelKeys,
  type Model,
  type ModelCreateInput,
  type CompatibilityType,
  type AuthType,
  type ModelStatus,
  type ModelTestResult,
} from '../services/model-api';
import { Select } from './ui';
import { confirmDialog } from './ui/confirm';
import { usePermission } from '../hooks/use-permission';
import { getErrorMessage } from '../lib/api-client';

const COMPATIBILITY_LABELS: Record<CompatibilityType, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

const AUTH_LABELS: Record<AuthType, string> = {
  bearer: 'Bearer Token (Authorization)',
  x_api_key: 'X-API-Key (Header)',
  api_key_header: 'API-Key (Header)',
  custom: '自定义',
};

// Error-code → troubleshooting hint, mirrors the legacy troubleshooting map.
const ERROR_HINTS: Record<string, string> = {
  AUTH_FAILED: '检查 API Key 是否正确、是否已过期或被吊销。',
  MODEL_NOT_FOUND_UPSTREAM: 'model_id 在上游不存在；确认模型名拼写与所属 provider。',
  CONNECTION_ERROR: '无法连接 base_url；检查网络、代理或服务地址是否可达。',
  TIMEOUT: '请求超时；可重试，或确认上游服务响应正常。',
  RATE_LIMITED: '触发上游限流；稍后重试或提升配额。',
  QUOTA_EXCEEDED: 'API 额度已用尽；检查账户余额。',
  SSL_ERROR: 'SSL/TLS 握手失败；确认 base_url 协议与证书有效。',
};

const COMPATIBILITY_OPTIONS: CompatibilityType[] = ['openai', 'anthropic'];
const AUTH_TYPES: AuthType[] = ['bearer', 'x_api_key', 'api_key_header', 'custom'];

/** Format an ISO timestamp as a short Chinese relative string ('刚刚' / '3 分钟前'). '' if missing/invalid. */
function formatRelativeTime(iso?: string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const sec = Math.floor((Date.now() - then) / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN');
}

/** Build the editor form state. Editing leaves api_key empty ("leave blank to keep"). */
type ModelForm = Omit<ModelCreateInput, 'api_key'> & { api_key: string };

function emptyForm(): ModelForm {
  return {
    model_id: '',
    name: '',
    base_url: '',
    api_key: '',
    compatibility_type: 'openai',
    auth_type: 'bearer',
    auth_header_format: '',
    default_params: { temperature: 0.7, max_tokens: 4096, context_window: 128000 },
    provider_tag: '',
  };
}

function modelToForm(m: Model): ModelForm {
  return {
    model_id: m.model_id,
    name: m.name,
    base_url: m.base_url,
    api_key: '', // never prefill the masked secret
    compatibility_type: m.compatibility_type,
    auth_type: m.auth_type,
    auth_header_format: m.auth_header_format ?? '',
    default_params: m.default_params ?? { temperature: 0.7, max_tokens: 4096, context_window: 128000 },
    provider_tag: m.provider_tag ?? '',
  };
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  // 写权限门控：新建/编辑/删除为 model:write；"测试连通性"后端为
  // model:read（探针不改配置），与路由同键，无权限时隐藏入口。
  const canWrite = usePermission('model:write');
  const canTest = usePermission('model:read');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<ModelStatus | 'all'>('all');
  const [editing, setEditing] = useState<Model | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<ModelForm>(emptyForm());
  const [error, setError] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: modelKeys.list({ page_size: 100, ...(statusFilter !== 'all' ? { status: statusFilter } : {}) }),
    queryFn: () =>
      modelApi.list({ page_size: 100, ...(statusFilter !== 'all' ? { status: statusFilter } : {}) }),
  });
  const models = data?.items ?? [];

  // Client-side search over name / model_id / provider_tag (status is backend-filtered).
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) =>
      [m.name, m.model_id, m.provider_tag ?? ''].some((v) => v.toLowerCase().includes(q)),
    );
  }, [models, search]);

  // ── Stats ──
  const stats = useMemo(() => {
    const total = models.length;
    const active = models.filter((m) => m.status === 'active').length;
    const openai = models.filter((m) => m.compatibility_type === 'openai').length;
    const anthropic = models.filter((m) => m.compatibility_type === 'anthropic').length;
    return { total, active, openai, anthropic };
  }, [models]);

  const createM = useMutation({
    mutationFn: (input: ModelCreateInput) => modelApi.create(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: modelKeys.all });
      setError(null);
      setCreating(false);
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '创建模型失败')),
  });

  const updateM = useMutation({
    mutationFn: ({ id, input }: { id: string; input: ModelCreateInput }) => modelApi.update(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: modelKeys.all });
      setError(null);
      setEditing(null);
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '保存模型失败')),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => modelApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: modelKeys.all }),
    onError: (e: unknown) => setError(getErrorMessage(e, '删除失败')),
  });

  const testM = useMutation({
    mutationFn: (id: string) => modelApi.test(id),
    // Backend persists the test outcome; refresh the list so the connectivity badge updates.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: modelKeys.all }),
  });

  const handleOpenCreate = () => {
    setError(null);
    setForm(emptyForm());
    setCreating(true);
  };

  const handleOpenEdit = (m: Model) => {
    setError(null);
    setForm(modelToForm(m));
    setEditing(m);
  };

  const handleOpenTest = (m: Model) => {
    setError(null);
    setTestingId(m.id);
    testM.reset();
    testM.mutate(m.id);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    // Build payload; for edit, an empty api_key means "keep existing" → omit it.
    const isEdit = editing !== null;
    const payload: ModelCreateInput = {
      model_id: form.model_id.trim(),
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key,
      compatibility_type: form.compatibility_type,
      auth_type: form.auth_type,
      ...(form.auth_type === 'custom' ? { auth_header_format: form.auth_header_format } : {}),
      default_params: form.default_params,
      provider_tag: form.provider_tag?.trim() || undefined,
    };
    if (isEdit && editing) {
      updateM.mutate({ id: editing.id, input: payload });
    } else {
      createM.mutate(payload);
    }
  };

  const handleDelete = async (m: Model) => {
    const ok = await confirmDialog({
      title: `删除模型「${m.name}」？`,
      description: '删除后引用该模型的 Agent 将无法执行。',
      okText: '删除',
      danger: true,
    });
    if (!ok) return;
    setError(null);
    deleteM.mutate(m.id);
  };

  const testResult: ModelTestResult | undefined = testM.data;

  return (
    <div className="space-y-5">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Server className="w-5 h-5 text-indigo-400" />
            模型配置
          </h2>
          <p className="text-xs text-[#71717a] mt-1">
            配置 LLM 接入（接口 / Key / 模型 / 协议），供 Agent 与工作流调用。
          </p>
        </div>
        {canWrite && (
          <button
            onClick={handleOpenCreate}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-semibold transition cursor-pointer shadow-md shadow-indigo-600/20"
          >
            <Plus className="w-4 h-4" />
            新建模型
          </button>
        )}
      </div>

      {/* ── Stats ── */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: '模型总数', value: stats.total, color: 'text-white' },
          { label: '已启用 (active)', value: stats.active, color: 'text-emerald-400' },
          { label: 'OpenAI 兼容', value: stats.openai, color: 'text-sky-400' },
          { label: 'Anthropic 兼容', value: stats.anthropic, color: 'text-orange-400' },
        ].map((s) => (
          <div key={s.label} className="p-4 rounded-xl border border-[#27272a] bg-[#18181b]">
            <p className="text-[10px] text-[#71717a] uppercase tracking-widest font-bold">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* ── Toolbar ── */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#71717a]" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索名称 / model_id / provider"
            className="w-full pl-9 pr-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-sm text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 transition"
          />
        </div>
        <Select
          value={statusFilter}
          onChange={(v) => setStatusFilter((v ?? 'all') as ModelStatus | 'all')}
          placeholder="全部状态"
          className="w-36"
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'active', label: '已启用' },
            { value: 'inactive', label: '已停用' },
          ]}
        />
      </div>

      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-300 text-xs">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span className="font-sans">{error}</span>
        </div>
      )}

      {/* ── Table ── */}
      <div className="rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-[#71717a]">
            <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中…
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-[#52525b]">
            <Server className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-sm">暂无模型配置{search ? '匹配搜索' : ''}，点击右上角新建</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#27272a] text-[#71717a] text-[11px] uppercase tracking-wider">
                <th className="text-left font-semibold px-4 py-3">模型</th>
                <th className="text-left font-semibold px-4 py-3">认证</th>
                <th className="text-left font-semibold px-4 py-3">上下文</th>
                <th className="text-left font-semibold px-4 py-3">状态</th>
                <th className="text-left font-semibold px-4 py-3">连通性</th>
                <th className="text-right font-semibold px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m) => (
                <tr key={m.id} className="border-b border-[#27272a] last:border-0 hover:bg-[#1c1c1f] transition">
                  <td className="px-4 py-3">
                    <p className="font-semibold text-white">{m.name}</p>
                    <p className="text-[11px] text-[#71717a] font-mono">{m.model_id}</p>
                  </td>
                  <td className="px-4 py-3 text-[11px] text-[#a1a1aa]">{AUTH_LABELS[m.auth_type]}</td>
                  <td className="px-4 py-3 text-[11px] text-[#a1a1aa] font-mono">
                    {m.default_params?.context_window ? `${Math.round(m.default_params.context_window / 1000)}K` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                      m.status === 'active' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-zinc-500/10 text-zinc-400'
                    }`}>
                      {m.status === 'active' ? '已启用' : '已停用'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {m.last_test_success === true ? (
                      <div className="flex flex-col gap-0.5">
                        <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-500/10 text-emerald-400 w-fit">连通</span>
                        <span className="text-[10px] text-[#52525b]">{formatRelativeTime(m.last_test_at)}</span>
                      </div>
                    ) : m.last_test_success === false ? (
                      <div className="flex flex-col gap-0.5">
                        <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/10 text-rose-400 w-fit">不通</span>
                        <span className="text-[10px] text-[#52525b]">{formatRelativeTime(m.last_test_at)}</span>
                      </div>
                    ) : (
                      <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-zinc-500/10 text-zinc-400">未测试</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {canTest && (
                        <button
                          onClick={() => handleOpenTest(m)}
                          title="测试连通性"
                          className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-amber-400 hover:bg-[#27272a] transition cursor-pointer"
                        >
                          <Zap className="w-4 h-4" />
                        </button>
                      )}
                      {canWrite && (
                        <button
                          onClick={() => handleOpenEdit(m)}
                          title="编辑"
                          className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-indigo-400 hover:bg-[#27272a] transition cursor-pointer"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                      )}
                      {canWrite && (
                        <button
                          onClick={() => handleDelete(m)}
                          title="删除"
                          className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-rose-400 hover:bg-[#27272a] transition cursor-pointer"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Create / Edit Modal ── */}
      {(creating || editing) && (
        <div className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-xl max-h-[90vh] overflow-y-auto bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a] sticky top-0 bg-[#121214] z-10">
              <h3 className="text-sm font-bold text-white">
                {editing ? `编辑模型 · ${editing.name}` : '新建模型'}
              </h3>
              <button
                onClick={() => { setCreating(false); setEditing(null); setError(null); }}
                className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="p-5 space-y-4 text-xs">
              <div className="grid grid-cols-2 gap-4">
                <Field label="模型 ID (model_id) *">
                  <input
                    required value={form.model_id}
                    onChange={(e) => setForm({ ...form, model_id: e.target.value })}
                    placeholder="deepseek-chat, gpt-4o-mini"
                    className={inputCls}
                  />
                </Field>
                <Field label="显示名称 (name) *">
                  <input
                    required value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="DeepSeek Chat"
                    className={inputCls}
                  />
                </Field>
              </div>

              <Field label="Base URL *">
                <input
                  required value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  placeholder="https://api.deepseek.com/v1"
                  className={inputCls}
                />
              </Field>

              <Field label={`API Key ${editing ? '（留空表示不修改）' : '*'}`}>
                <input
                  type="password"
                  required={!editing}
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder={editing ? '留空则保留原有密钥' : '输入 API Key'}
                  className={inputCls}
                />
                {editing && (
                  <p className="text-[10px] text-[#71717a] mt-1">
                    当前密钥已加密存储（脱敏回显：{editing.api_key || '—'}）
                  </p>
                )}
              </Field>

              <div className="grid grid-cols-2 gap-4">
                <Field label="兼容协议">
                  <Select
                    value={form.compatibility_type}
                    onChange={(v) => setForm({ ...form, compatibility_type: (v ?? 'openai') as CompatibilityType })}
                    options={COMPATIBILITY_OPTIONS.map((c) => ({ value: c, label: COMPATIBILITY_LABELS[c] }))}
                  />
                </Field>
                <Field label="认证方式">
                  <Select
                    value={form.auth_type}
                    onChange={(v) => setForm({ ...form, auth_type: (v ?? 'bearer') as AuthType })}
                    options={AUTH_TYPES.map((a) => ({ value: a, label: AUTH_LABELS[a] }))}
                  />
                </Field>
              </div>

              {form.auth_type === 'custom' && (
                <Field label="自定义认证模板（支持 {key} 占位符）">
                  <input
                    value={form.auth_header_format}
                    onChange={(e) => setForm({ ...form, auth_header_format: e.target.value })}
                    placeholder='Authorization: Bearer {key}'
                    className={inputCls}
                  />
                  <p className="text-[10px] text-[#71717a] mt-1">
                    纯文本 → 单个 header；JSON → 多个 header
                  </p>
                </Field>
              )}

              <Field label="Provider 标签（可选）">
                <input
                  value={form.provider_tag ?? ''}
                  onChange={(e) => setForm({ ...form, provider_tag: e.target.value })}
                  placeholder="deepseek / openai / anthropic"
                  className={inputCls}
                />
              </Field>

              {/* Default params */}
              <div className="pt-2 border-t border-[#27272a]">
                <p className="text-[11px] font-bold text-[#a1a1aa] uppercase tracking-wider mb-3">默认参数</p>
                <div className="space-y-4">
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-slate-400 font-medium">Temperature</label>
                      <span className="text-slate-300 font-mono font-bold">
                        {form.default_params?.temperature ?? 0.7}
                      </span>
                    </div>
                    <input
                      type="range" min="0" max="2" step="0.1"
                      value={form.default_params?.temperature ?? 0.7}
                      onChange={(e) => setForm({
                        ...form,
                        default_params: { ...form.default_params!, temperature: Number(e.target.value) },
                      })}
                      className="w-full accent-indigo-500"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="Max Tokens">
                      <input
                        type="number" min="1"
                        value={form.default_params?.max_tokens ?? 4096}
                        onChange={(e) => setForm({
                          ...form,
                          default_params: { ...form.default_params!, max_tokens: Number(e.target.value) },
                        })}
                        className={inputCls}
                      />
                    </Field>
                    <Field label="Context Window">
                      <input
                        type="number" min="1"
                        value={form.default_params?.context_window ?? 128000}
                        onChange={(e) => setForm({
                          ...form,
                          default_params: { ...form.default_params!, context_window: Number(e.target.value) },
                        })}
                        className={inputCls}
                      />
                    </Field>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div className="flex justify-end gap-3 pt-4 border-t border-[#27272a]">
                <button
                  type="button"
                  onClick={() => { setCreating(false); setEditing(null); setError(null); }}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold"
                >
                  取消
                </button>
                {canWrite && (
                  <button
                    type="submit"
                    disabled={createM.isPending || updateM.isPending}
                    className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-md cursor-pointer font-semibold disabled:opacity-60 flex items-center gap-2"
                  >
                    {(createM.isPending || updateM.isPending) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    {editing ? '保存' : '创建'}
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Test Modal ── */}
      {testingId && (
        <div className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-lg bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a]">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Zap className="w-4 h-4 text-amber-400" />
                连通性测试 · {models.find((m) => m.id === testingId)?.name}
              </h3>
              <button
                onClick={() => setTestingId(null)}
                className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-3 text-xs">
              {testM.isPending && (
                <div className="flex items-center justify-center py-8 text-[#71717a]">
                  <Loader2 className="w-5 h-5 animate-spin mr-2" /> 正在向模型发送测试请求…
                </div>
              )}

              {testResult?.success && !testM.isPending && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                    <span className="font-semibold text-emerald-300">连接成功</span>
                    <span className="ml-auto text-[11px] text-[#a1a1aa] font-mono">
                      延迟 {testResult.latency_ms}ms
                    </span>
                  </div>
                  {testResult.reply && (
                    <div>
                      <p className="text-[10px] text-[#71717a] uppercase tracking-wider font-bold mb-1">模型回复</p>
                      <pre className="p-3 rounded-lg bg-[#18181b] border border-[#27272a] text-[#a1a1aa] whitespace-pre-wrap font-mono text-[11px]">
                        {testResult.reply}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              {testResult && !testResult.success && !testM.isPending && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 p-3 rounded-lg border border-rose-500/30 bg-rose-500/5">
                    <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
                    <span className="font-semibold text-rose-300">连接失败</span>
                    {testResult.error_code && (
                      <span className="ml-auto px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 font-mono text-[10px]">
                        {testResult.error_code}
                      </span>
                    )}
                  </div>
                  {testResult.error && (
                    <div>
                      <p className="text-[10px] text-[#71717a] uppercase tracking-wider font-bold mb-1">错误详情</p>
                      <pre className="p-3 rounded-lg bg-[#18181b] border border-[#27272a] text-rose-300/80 whitespace-pre-wrap font-mono text-[11px]">
                        {testResult.error}
                      </pre>
                    </div>
                  )}
                  {testResult.error_code && ERROR_HINTS[testResult.error_code] && (
                    <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-500/30 bg-amber-500/5 text-amber-300">
                      <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                      <span className="font-sans">{ERROR_HINTS[testResult.error_code]}</span>
                    </div>
                  )}
                </div>
              )}

              {testM.isError && !testM.isPending && (
                <div className="flex items-start gap-2 p-3 rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-300">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span className="font-sans">
                    {getErrorMessage(testM.error, '测试请求失败（网络或鉴权问题）')}
                  </span>
                </div>
              )}

              <div className="flex justify-end pt-2 border-t border-[#27272a]">
                <button
                  onClick={() => testingId && testM.mutate(testingId)}
                  disabled={testM.isPending}
                  className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold disabled:opacity-60 flex items-center gap-2"
                >
                  <Zap className="w-3.5 h-3.5" />
                  重新测试
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const inputCls =
  'w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 transition text-xs font-sans';

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-slate-400 font-medium font-sans">{label}</label>
      {children}
    </div>
  );
}
