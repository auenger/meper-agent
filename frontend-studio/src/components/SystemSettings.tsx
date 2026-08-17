import { useState, useEffect, FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Key, ShieldAlert, Plus, Sliders, CheckCircle, Check, Copy,
  AlertTriangle, RefreshCw, ChevronDown, ChevronUp, ChevronRight, Pencil,
} from 'lucide-react';
import { agentApi } from '../services/agent-api';
import { workflowsApi } from '../services/workflows-api';
import {
  apiKeyApi, apiKeyKeys, ALL_API_KEY_SCOPES, SCOPE_LABELS,
  type ApiKey,
  type ApiKeyCreateInput, type ApiKeyCreated, type ApiKeyUpdateInput,
} from '../services/api-keys-api';
import { applicationApi, applicationKeys } from '../services/application-api';
import { getErrorMessage } from '../lib/api-client';
import { copyToClipboard } from '../lib/clipboard';

/** Fields rendered in the detail panel — satisfied by both list items and the create response. */
type KeyDetailLike = {
  scopes: string[];
  bindings: { agents: string[]; workflows: string[] };
  rate_limit: number;
  expires_at: string | null;
  key_prefix: string;
  introspect_url?: string;
  app_id?: string;
  created_at: string;
  last_used_at?: string | null;
  updated_at?: string;
};

/** ISO timestamp → YYYY-MM-DD, or fallback when null/empty. */
function fmtDate(iso: string | null | undefined, fallback = '—'): string {
  if (!iso) return fallback;
  return iso.length >= 10 ? iso.slice(0, 10) : fallback;
}

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export function SystemSettings() {
  const qc = useQueryClient();

  // ── Key list (real backend) ───────────────────────────────
  const { data, isLoading, isFetching } = useQuery({
    queryKey: apiKeyKeys.lists(),
    queryFn: () => apiKeyApi.list(),
  });
  const apiKeys = data?.items ?? [];

  // ── Create form state ─────────────────────────────────────
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Set<string>>(new Set(['agents:read', 'agents:invoke']));
  const [rateLimit, setRateLimit] = useState(60);
  const [boundAgents, setBoundAgents] = useState<Set<string>>(new Set());
  const [boundWorkflows, setBoundWorkflows] = useState<Set<string>>(new Set());
  const [showBindings, setShowBindings] = useState(false);
  const [formIntrospectUrl, setFormIntrospectUrl] = useState('');
  const [formAppId, setFormAppId] = useState('');
  const [error, setError] = useState('');
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copiedValue, setCopiedValue] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<ApiKey | null>(null);

  // Agent/workflow option lists — always loaded (used by bindings picker AND detail name resolution).
  const { data: agentsData } = useQuery({
    queryKey: ['api-keys', 'agent-options'],
    queryFn: () => agentApi.list({ page: 1, page_size: 100, status: 'all' }),
  });
  const { data: workflowsData } = useQuery({
    queryKey: ['api-keys', 'workflow-options'],
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  });
  // Application list — used by the app-binding picker AND detail name resolution.
  const { data: appsData } = useQuery({
    queryKey: applicationKeys.lists(),
    queryFn: () => applicationApi.list(),
  });

  const agentName = (id: string) => agentsData?.items.find((a) => a.id === id)?.name ?? id;
  const workflowName = (id: string) => workflowsData?.items.find((w) => w.id === id)?.name ?? id;
  const appName = (id: string) => appsData?.items.find((a) => a.id === id)?.name ?? id;

  const copy = (text: string) => {
    copyToClipboard(text);
    setCopiedValue(text);
    setTimeout(() => setCopiedValue(null), 2000);
  };

  const createMutation = useMutation({
    mutationFn: (payload: ApiKeyCreateInput) => apiKeyApi.create(payload),
    onSuccess: (res) => {
      setCreatedKey(res);
      qc.invalidateQueries({ queryKey: apiKeyKeys.lists() });
      setName('');
      setScopes(new Set(['agents:read', 'agents:invoke']));
      setRateLimit(60);
      setBoundAgents(new Set());
      setBoundWorkflows(new Set());
      setFormIntrospectUrl('');
      setFormAppId('');
      setError('');
    },
    onError: (e) => setError(getErrorMessage(e, '创建失败，请重试')),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiKeyApi.revoke(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: apiKeyKeys.lists() }),
  });

  const handleCreate = (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (!name.trim()) return setError('请填写 Key 名称');
    if (scopes.size === 0) return setError('至少选择一项权限');
    createMutation.mutate({
      name: name.trim(),
      scopes: [...scopes],
      rate_limit: rateLimit,
      bindings: { agents: [...boundAgents], workflows: [...boundWorkflows] },
      introspect_url: formIntrospectUrl.trim() || null,
      app_id: formAppId,
    });
  };

  const handleRevoke = (key: ApiKey) => {
    if (!window.confirm(`确认撤销 Key「${key.name}」？撤销后该 Key 立即失效，且无法恢复。`)) return;
    revokeMutation.mutate(key.id);
  };

  // ── Detail row + panel renderers (plain functions, not components) ──
  const renderRow = (label: string, value: string, mono = false) => (
    <div className="flex gap-2" key={label}>
      <span className="text-[#71717a] w-20 shrink-0">{label}</span>
      <span className={`text-slate-300 ${mono ? 'font-mono break-all' : ''}`}>{value}</span>
    </div>
  );

  const renderKeyDetail = (info: KeyDetailLike) => {
    const aNames = info.bindings.agents.map(agentName);
    const wNames = info.bindings.workflows.map(workflowName);
    return (
      <div className="space-y-2.5 pt-3 mt-3 border-t border-[#27272a] text-[10px] font-sans">
        <div className="flex gap-2">
          <span className="text-[#71717a] w-20 shrink-0">权限</span>
          <div className="flex flex-wrap gap-1">
            {info.scopes.map((s) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-[#27272a] text-slate-400 text-[9px] font-mono">
                {SCOPE_LABELS[s] ?? s}
              </span>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          <span className="text-[#71717a] w-20 shrink-0">资源绑定</span>
          <div className="space-y-0.5">
            {aNames.length === 0 && wNames.length === 0 ? (
              <span className="text-slate-500">不限制（可访问全部资源）</span>
            ) : (
              <>
                {aNames.length > 0 && <div className="text-slate-300">智能体：{aNames.join('、')}</div>}
                {wNames.length > 0 && <div className="text-slate-300">工作流：{wNames.join('、')}</div>}
              </>
            )}
          </div>
        </div>
        {renderRow('限流', `${info.rate_limit} 次/分`)}
        {info.app_id !== undefined && renderRow('绑定应用', info.app_id ? appName(info.app_id) : '未绑定')}
        {info.introspect_url !== undefined &&
          renderRow('身份校验', info.introspect_url || '未配置', !!info.introspect_url)}
        {renderRow('过期时间', info.expires_at ? fmtDate(info.expires_at) : '永不过期')}
        {renderRow('创建时间', fmtDate(info.created_at))}
        {info.updated_at && renderRow('更新时间', fmtDate(info.updated_at))}
        {info.last_used_at !== undefined &&
          renderRow('上次使用', fmtDate(info.last_used_at, '从未使用'))}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[#71717a] w-20 shrink-0">密钥前缀</span>
          <code className="text-slate-400 font-mono">{info.key_prefix || '—'}</code>
          {info.key_prefix && (
            <button
              onClick={() => copy(info.key_prefix)}
              className="text-[#a1a1aa] hover:text-white flex items-center gap-0.5 cursor-pointer transition"
            >
              {copiedValue === info.key_prefix ? (
                <Check className="w-3 h-3 text-emerald-400" />
              ) : (
                <Copy className="w-3 h-3" />
              )}
              <span className="text-[9px]">{copiedValue === info.key_prefix ? '已复制' : '复制'}</span>
            </button>
          )}
          <span className="text-[9px] text-slate-600">明文仅创建时可见，无法再次获取</span>
        </div>
      </div>
    );
  };

  // ── Right-column global config (unchanged mock) ───────────
  const [timeout, setTimeoutVal] = useState(30);
  const [detailedAnimation, setDetailedAnimation] = useState(true);
  const [autoClearCache, setAutoClearCache] = useState(false);
  const [strictSchema, setStrictSchema] = useState(true);

  return (
    <div className="space-y-6">
      {/* One-time raw key reveal + full detail */}
      {createdKey && (
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-emerald-400 text-sm font-bold font-sans">
              <CheckCircle className="w-4 h-4 shrink-0" />
              Key「{createdKey.name}」已创建
            </div>
            <button
              onClick={() => setCreatedKey(null)}
              className="text-slate-400 hover:text-white transition cursor-pointer"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>

          <div className="flex items-center gap-1.5 text-[11px] text-amber-400 font-sans">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            明文 Key 仅此一次显示，关闭后无法再次查看，请立即复制保存。
          </div>

          <div className="flex items-center gap-2">
            <code className="flex-1 px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-emerald-300 font-mono text-xs break-all">
              {createdKey.key}
            </code>
            <button
              onClick={() => copy(createdKey.key)}
              className="shrink-0 px-3 py-2 bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-bold rounded-lg text-xs flex items-center gap-1 cursor-pointer transition font-sans"
            >
              {copiedValue === createdKey.key ? (
                <Check className="w-3.5 h-3.5" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
              {copiedValue === createdKey.key ? '已复制' : '复制 Key'}
            </button>
          </div>

          {renderKeyDetail(createdKey)}
        </div>
      )}

      {/* Visual top bar */}
      <div className="flex items-center gap-3 p-4 bg-[#18181b] rounded-xl border border-[#27272a]">
        <Key className="w-5 h-5 text-amber-400" />
        <div className="space-y-0.5">
          <h2 className="text-sm font-bold text-white font-sans">对外接入密钥与后端引擎全局配置</h2>
          <p className="text-xs text-[#a1a1aa] font-sans">
            管理对外接入用的 API Key（供 /ext 嵌入式对话、第三方系统调用），并配置运行时全局参数。
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* LEFT COLUMN: API KEY MANAGEMENT (real) */}
        <div className="lg:col-span-7 bg-[#18181b] border border-[#27272a] rounded-xl p-5 shadow-lg space-y-5">
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-white font-sans">对外接入 API Key (/api-keys)</h3>
              <button
                onClick={() => qc.invalidateQueries({ queryKey: apiKeyKeys.lists() })}
                disabled={isFetching}
                className="text-[10px] text-[#a1a1aa] hover:text-white flex items-center gap-1 cursor-pointer disabled:opacity-40 transition font-sans"
              >
                <RefreshCw className={`w-3 h-3 ${isFetching ? 'animate-spin' : ''}`} />
                刷新
              </button>
            </div>
            <p className="text-xs text-slate-500 font-sans">
              第三方经此 Key 调用 /ext 接口。点击列表项查看详情。
            </p>
          </div>

          {/* Create form */}
          <form onSubmit={handleCreate} className="space-y-3 bg-[#121214]/60 border border-[#27272a] rounded-xl p-4">
            {/* name */}
            <div className="space-y-1">
              <label className="text-[11px] text-[#a1a1aa] font-sans">Key 名称</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：MES 产线 A、官网访客"
                maxLength={100}
                className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans placeholder-slate-600"
              />
            </div>

            {/* scopes */}
            <div className="space-y-1.5">
              <label className="text-[11px] text-[#a1a1aa] font-sans">权限（至少一项）</label>
              <div className="flex flex-wrap gap-2">
                {ALL_API_KEY_SCOPES.map((s) => {
                  const active = scopes.has(s);
                  return (
                    <button
                      type="button"
                      key={s}
                      onClick={() => setScopes((prev) => toggleInSet(prev, s))}
                      className={`px-2.5 py-1 rounded-lg text-[10px] font-mono border transition cursor-pointer ${
                        active
                          ? 'bg-amber-500/20 border-amber-500/50 text-amber-300'
                          : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                      }`}
                    >
                      {active && <Check className="w-3 h-3 inline mr-1" />}
                      {SCOPE_LABELS[s]}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* bindings (collapsible) */}
            <div className="space-y-1.5">
              <button
                type="button"
                onClick={() => setShowBindings((v) => !v)}
                className="flex items-center gap-1 text-[11px] text-[#a1a1aa] hover:text-white cursor-pointer font-sans"
              >
                {showBindings ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                资源绑定（可选，留空 = 不限制）
              </button>
              {showBindings && (
                <div className="space-y-3 pl-1">
                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 font-sans">绑定智能体（{boundAgents.size}）</span>
                    <div className="max-h-28 overflow-y-auto flex flex-wrap gap-1.5">
                      {(agentsData?.items ?? []).map((a) => {
                        const active = boundAgents.has(a.id);
                        return (
                          <button
                            type="button"
                            key={a.id}
                            onClick={() => setBoundAgents((prev) => toggleInSet(prev, a.id))}
                            className={`px-2 py-0.5 rounded text-[10px] border transition cursor-pointer ${
                              active
                                ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                                : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                            }`}
                          >
                            {a.name}
                          </button>
                        );
                      })}
                      {(agentsData?.items ?? []).length === 0 && (
                        <span className="text-[10px] text-slate-600 font-sans">暂无智能体</span>
                      )}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <span className="text-[10px] text-slate-500 font-sans">绑定工作流（{boundWorkflows.size}）</span>
                    <div className="max-h-28 overflow-y-auto flex flex-wrap gap-1.5">
                      {(workflowsData?.items ?? []).map((w) => {
                        const active = boundWorkflows.has(w.id);
                        return (
                          <button
                            type="button"
                            key={w.id}
                            onClick={() => setBoundWorkflows((prev) => toggleInSet(prev, w.id))}
                            className={`px-2 py-0.5 rounded text-[10px] border transition cursor-pointer ${
                              active
                                ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                                : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                            }`}
                          >
                            {w.name}
                          </button>
                        );
                      })}
                      {(workflowsData?.items ?? []).length === 0 && (
                        <span className="text-[10px] text-slate-600 font-sans">暂无工作流</span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* introspection url */}
            <div className="space-y-1">
              <label className="text-[11px] text-[#a1a1aa] font-sans">用户认证 Introspection URL</label>
              <input
                type="text"
                value={formIntrospectUrl}
                onChange={(e) => setFormIntrospectUrl(e.target.value)}
                placeholder="https://partner.example.com/oauth/introspect"
                maxLength={500}
                className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans placeholder-slate-600"
              />
              <p className="text-[10px] text-slate-600 font-sans">
                接入方 token 校验端点（RFC 7662）。API 调用需携带 X-User-Token，平台按此端点校验终端用户身份
              </p>
            </div>

            {/* app binding */}
            <div className="space-y-1">
              <label className="text-[11px] text-[#a1a1aa] font-sans">绑定应用</label>
              <select
                value={formAppId}
                onChange={(e) => setFormAppId(e.target.value)}
                className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans"
              >
                <option value="">不绑定</option>
                {(appsData?.items ?? []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
              <p className="text-[10px] text-slate-600 font-sans">
                配置用户身份校验时必填——接入方系统与应用一一对应，外部用户身份按应用命名空间隔离
              </p>
            </div>

            {/* rate limit */}
            <div className="flex items-center gap-3">
              <label className="text-[11px] text-[#a1a1aa] font-sans shrink-0">每分钟请求上限</label>
              <input
                type="number"
                min={1}
                max={10000}
                value={rateLimit}
                onChange={(e) => setRateLimit(Math.max(1, Math.min(10000, Number(e.target.value) || 60)))}
                className="w-24 px-2 py-1.5 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans"
              />
              <span className="text-[10px] text-slate-500 font-sans">次/分</span>
            </div>

            {error && (
              <p className="text-[11px] text-rose-400 font-sans">{error}</p>
            )}

            <button
              type="submit"
              disabled={createMutation.isPending}
              className="w-full px-4 py-2 bg-gradient-to-r from-amber-500 to-yellow-600 hover:from-amber-600 hover:to-yellow-700 disabled:opacity-50 text-slate-950 font-bold rounded-lg shadow cursor-pointer transition flex items-center justify-center gap-1 font-sans text-xs"
            >
              <Plus className="w-4 h-4 text-slate-950" />
              {createMutation.isPending ? '创建中…' : '生成新 Key'}
            </button>
          </form>

          {/* Keys list */}
          <div className="space-y-3">
            {isLoading && (
              <p className="text-xs text-slate-500 font-sans text-center py-4">加载中…</p>
            )}
            {!isLoading && apiKeys.length === 0 && (
              <p className="text-xs text-slate-500 font-sans text-center py-4">暂无对外 Key，创建第一个吧。</p>
            )}
            {apiKeys.map((key) => {
              const open = expandedId === key.id;
              return (
                <div
                  key={key.id}
                  className="p-4 bg-[#121214]/60 rounded-lg border border-[#27272a] space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-slate-200 font-sans text-xs">{key.name}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[8px] font-mono font-bold ${
                        key.status === 'active' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-red-400'
                      }`}>
                        {key.status === 'active' ? 'ACTIVE' : 'REVOKED'}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setExpandedId((prev) => (prev === key.id ? null : key.id))}
                        className="p-1 px-2 border border-[#27272a] text-[#a1a1aa] hover:text-white hover:border-slate-600 rounded-lg transition text-[10px] cursor-pointer flex items-center gap-1 font-sans"
                      >
                        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                        详情
                      </button>
                      {key.status === 'active' && (
                        <button
                          onClick={() => setEditingKey(key)}
                          className="p-1 px-2 border border-[#27272a] text-amber-400 hover:text-white hover:border-amber-500/50 rounded-lg transition text-[10px] cursor-pointer flex items-center gap-1 font-sans"
                        >
                          <Pencil className="w-3 h-3" />
                          编辑
                        </button>
                      )}
                      {key.status === 'active' && (
                        <button
                          onClick={() => handleRevoke(key)}
                          disabled={revokeMutation.isPending}
                          className="p-1 px-2 border border-rose-950/20 text-rose-400 hover:text-white hover:bg-rose-950 rounded-lg transition text-[10px] cursor-pointer font-semibold disabled:opacity-40 font-sans"
                        >
                          撤销
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {key.scopes.map((s) => (
                      <span key={s} className="px-1.5 py-0.5 rounded bg-[#27272a] text-slate-400 text-[9px] font-mono">
                        {SCOPE_LABELS[s] ?? s}
                      </span>
                    ))}
                  </div>
                  {open && renderKeyDetail(key)}
                </div>
              );
            })}
          </div>
        </div>

        {/* RIGHT COLUMN: ENGINE GENERAL CONFIGS (unchanged) */}
        <div className="lg:col-span-5 bg-[#18181b] border border-[#27272a] rounded-xl p-5 shadow-lg flex flex-col justify-between">
          <div className="space-y-5">
            <div className="space-y-1 border-b border-[#27272a] pb-3 mb-2">
              <h3 className="text-sm font-bold text-white font-sans flex items-center gap-1.5">
                <Sliders className="w-4 h-4 text-indigo-400" />
                虚拟机全局控制参数
              </h3>
              <p className="text-xs text-slate-500 font-sans">微调 Dify 以及 LangGraph 工作流运行时系统机制。</p>
            </div>

            {/* Timeout settings */}
            <div className="space-y-2">
              <div className="flex justify-between text-xs">
                <span className="text-[#a1a1aa] font-medium font-sans">最大 LLM 事务超时时长 (Timeout)</span>
                <span className="text-white font-mono font-bold">{timeout}s</span>
              </div>
              <input
                type="range"
                min="5"
                max="120"
                step="5"
                value={timeout}
                onChange={(e) => setTimeoutVal(parseInt(e.target.value))}
                className="w-full focus:outline-none accent-indigo-500"
              />
            </div>

            {/* Boolean feature flags toggles */}
            <div className="space-y-4 pt-2">
              {[
                {
                  id: 'detailedAnimation',
                  title: '启用图 Dashing 流转连线动效',
                  desc: '在测试运行工作流时，实时渲染连线的高精度流动虚线轨迹。建议始终开启。',
                  state: detailedAnimation,
                  setter: setDetailedAnimation,
                },
                {
                  id: 'strictSchema',
                  title: '严格模型执行 Schema 校验',
                  desc: '对每一个 Agent worker 输出的 JSON payload 实施强制格式和数据类型校正，出错则自动重试打回。',
                  state: strictSchema,
                  setter: setStrictSchema,
                },
                {
                  id: 'autoClearCache',
                  title: '会话闭环后自清理内存临时变量',
                  desc: '为了安全性。一旦整个流程图完美流至 END 节点，一键释放在内存中的任务草纲与文件缓存。',
                  state: autoClearCache,
                  setter: setAutoClearCache,
                },
              ].map((flag) => (
                <div key={flag.id} className="flex items-start justify-between gap-4 text-xs">
                  <div className="space-y-0.5">
                    <span className="font-semibold text-slate-300 font-sans">{flag.title}</span>
                    <p className="text-[11px] text-[#71717a] leading-normal font-sans">{flag.desc}</p>
                  </div>

                  <button
                    onClick={() => flag.setter(!flag.state)}
                    className="shrink-0 w-11 h-6 rounded-full transition relative flex items-center p-0.5 cursor-pointer bg-[#27272a]"
                    style={{
                      backgroundColor: flag.state ? '#4f46e5' : '#27272a',
                    }}
                  >
                    <span
                      className="w-5 h-5 rounded-full bg-white shadow-md block transition-transform duration-200"
                      style={{
                        transform: flag.state ? 'translateX(20px)' : 'translateX(0)',
                      }}
                    />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="p-3 bg-indigo-950/20 border border-indigo-900/30 rounded-lg space-y-1 text-indigo-300 mt-4">
            <span className="font-semibold flex items-center gap-1.5 text-xs font-sans">
              <ShieldAlert className="w-3.5 h-3.5 text-indigo-400" />
              API 安全提醒
            </span>
            <p className="text-[10px] leading-relaxed text-[#a1a1aa]">
              对外 Key 的明文仅在创建时展示一次。请妥善保管；如发现泄露，立即「撤销」并重新生成。
            </p>
          </div>
        </div>
      </div>

      {/* Edit dialog (portal) */}
      {editingKey && (
        <EditKeyModal
          keyItem={editingKey}
          onClose={() => setEditingKey(null)}
        />
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────── */
/*  EditKeyModal — edit an existing API Key (name / scopes / bindings /   */
/*  rate limit). Rendered via portal; reuses the create form's field      */
/*  layout and amber-dark styling. PUT /api/v1/api-keys/{id}.             */
/* ─────────────────────────────────────────────────────────────────────── */

function EditKeyModal({ keyItem, onClose }: { keyItem: ApiKey; onClose: () => void }) {
  const qc = useQueryClient();

  // ── form state, initialised from the key being edited ─────────────────
  const [name, setName] = useState(keyItem.name);
  const [scopes, setScopes] = useState<Set<string>>(new Set(keyItem.scopes));
  const [rateLimit, setRateLimit] = useState(keyItem.rate_limit);
  const [boundAgents, setBoundAgents] = useState<Set<string>>(new Set(keyItem.bindings.agents));
  const [boundWorkflows, setBoundWorkflows] = useState<Set<string>>(new Set(keyItem.bindings.workflows));
  const [showBindings, setShowBindings] = useState(false);
  const [formIntrospectUrl, setFormIntrospectUrl] = useState(keyItem.introspect_url ?? '');
  const [formAppId, setFormAppId] = useState(keyItem.app_id ?? '');
  const [error, setError] = useState('');

  // agent / workflow / application option lists (same sources as the create form)
  const { data: agentsData } = useQuery({
    queryKey: ['api-keys', 'agent-options'],
    queryFn: () => agentApi.list({ page: 1, page_size: 100, status: 'all' }),
  });
  const { data: workflowsData } = useQuery({
    queryKey: ['api-keys', 'workflow-options'],
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  });
  const { data: appsData } = useQuery({
    queryKey: applicationKeys.lists(),
    queryFn: () => applicationApi.list(),
  });

  // close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const updateMutation = useMutation({
    mutationFn: (payload: ApiKeyUpdateInput) => apiKeyApi.update(keyItem.id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: apiKeyKeys.lists() });
      onClose();
    },
    onError: (e) => setError(getErrorMessage(e, '保存失败，请重试')),
  });

  const handleSave = (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (!name.trim()) return setError('请填写 Key 名称');
    if (scopes.size === 0) return setError('至少选择一项权限');
    updateMutation.mutate({
      name: name.trim(),
      scopes: [...scopes],
      rate_limit: rateLimit,
      bindings: { agents: [...boundAgents], workflows: [...boundWorkflows] },
      introspect_url: formIntrospectUrl.trim() || null,
      app_id: formAppId,
    });
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSave}
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-[#18181b] border border-[#27272a] rounded-xl shadow-2xl flex flex-col"
      >
        {/* header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#27272a]">
          <div className="flex items-center gap-2">
            <Pencil className="w-4 h-4 text-amber-400" />
            <span className="text-sm font-bold text-white font-sans">编辑 API Key</span>
            <span className="text-[10px] text-slate-500 font-mono">{keyItem.key_prefix}</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white transition cursor-pointer"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* body */}
        <div className="px-5 py-4 space-y-3 flex-1 overflow-y-auto">
          {/* name */}
          <div className="space-y-1">
            <label className="text-[11px] text-[#a1a1aa] font-sans">Key 名称</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：MES 产线 A、官网访客"
              maxLength={100}
              className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans placeholder-slate-600"
            />
          </div>

          {/* scopes */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-[#a1a1aa] font-sans">权限（至少一项）</label>
            <div className="flex flex-wrap gap-2">
              {ALL_API_KEY_SCOPES.map((s) => {
                const active = scopes.has(s);
                return (
                  <button
                    type="button"
                    key={s}
                    onClick={() => setScopes((prev) => toggleInSet(prev, s))}
                    className={`px-2.5 py-1 rounded-lg text-[10px] font-mono border transition cursor-pointer ${
                      active
                        ? 'bg-amber-500/20 border-amber-500/50 text-amber-300'
                        : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                    }`}
                  >
                    {active && <Check className="w-3 h-3 inline mr-1" />}
                    {SCOPE_LABELS[s]}
                  </button>
                );
              })}
            </div>
          </div>

          {/* bindings (collapsible) */}
          <div className="space-y-1.5">
            <button
              type="button"
              onClick={() => setShowBindings((v) => !v)}
              className="flex items-center gap-1 text-[11px] text-[#a1a1aa] hover:text-white cursor-pointer font-sans"
            >
              {showBindings ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              资源绑定（可选，留空 = 不限制）
            </button>
            {showBindings && (
              <div className="space-y-3 pl-1">
                <div className="space-y-1">
                  <span className="text-[10px] text-slate-500 font-sans">绑定智能体（{boundAgents.size}）</span>
                  <div className="max-h-28 overflow-y-auto flex flex-wrap gap-1.5">
                    {(agentsData?.items ?? []).map((a) => {
                      const active = boundAgents.has(a.id);
                      return (
                        <button
                          type="button"
                          key={a.id}
                          onClick={() => setBoundAgents((prev) => toggleInSet(prev, a.id))}
                          className={`px-2 py-0.5 rounded text-[10px] border transition cursor-pointer ${
                            active
                              ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                              : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                          }`}
                        >
                          {a.name}
                        </button>
                      );
                    })}
                    {(agentsData?.items ?? []).length === 0 && (
                      <span className="text-[10px] text-slate-600 font-sans">暂无智能体</span>
                    )}
                  </div>
                </div>
                <div className="space-y-1">
                  <span className="text-[10px] text-slate-500 font-sans">绑定工作流（{boundWorkflows.size}）</span>
                  <div className="max-h-28 overflow-y-auto flex flex-wrap gap-1.5">
                    {(workflowsData?.items ?? []).map((w) => {
                      const active = boundWorkflows.has(w.id);
                      return (
                        <button
                          type="button"
                          key={w.id}
                          onClick={() => setBoundWorkflows((prev) => toggleInSet(prev, w.id))}
                          className={`px-2 py-0.5 rounded text-[10px] border transition cursor-pointer ${
                            active
                              ? 'bg-indigo-500/20 border-indigo-500/50 text-indigo-300'
                              : 'bg-[#121214] border-[#27272a] text-slate-400 hover:border-slate-600'
                          }`}
                        >
                          {w.name}
                        </button>
                      );
                    })}
                    {(workflowsData?.items ?? []).length === 0 && (
                      <span className="text-[10px] text-slate-600 font-sans">暂无工作流</span>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* introspection url */}
          <div className="space-y-1">
            <label className="text-[11px] text-[#a1a1aa] font-sans">用户认证 Introspection URL</label>
            <input
              type="text"
              value={formIntrospectUrl}
              onChange={(e) => setFormIntrospectUrl(e.target.value)}
              placeholder="https://partner.example.com/oauth/introspect"
              maxLength={500}
              className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans placeholder-slate-600"
            />
            <p className="text-[10px] text-slate-600 font-sans">
              接入方 token 校验端点（RFC 7662）。API 调用需携带 X-User-Token，平台按此端点校验终端用户身份
            </p>
          </div>

          {/* app binding */}
          <div className="space-y-1">
            <label className="text-[11px] text-[#a1a1aa] font-sans">绑定应用</label>
            <select
              value={formAppId}
              onChange={(e) => setFormAppId(e.target.value)}
              className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans"
            >
              <option value="">不绑定</option>
              {(appsData?.items ?? []).map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <p className="text-[10px] text-slate-600 font-sans">
              配置用户身份校验时必填——接入方系统与应用一一对应，外部用户身份按应用命名空间隔离
            </p>
          </div>

          {/* rate limit */}
          <div className="flex items-center gap-3">
            <label className="text-[11px] text-[#a1a1aa] font-sans shrink-0">每分钟请求上限</label>
            <input
              type="number"
              min={1}
              max={10000}
              value={rateLimit}
              onChange={(e) => setRateLimit(Math.max(1, Math.min(10000, Number(e.target.value) || 60)))}
              className="w-24 px-2 py-1.5 bg-[#121214] border border-[#27272a] rounded-lg text-slate-200 text-xs focus:outline-none focus:border-amber-400 transition font-sans"
            />
            <span className="text-[10px] text-slate-500 font-sans">次/分</span>
          </div>

          {error && <p className="text-[11px] text-rose-400 font-sans">{error}</p>}
        </div>

        {/* footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-[#27272a]">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 border border-[#27272a] text-[#a1a1aa] hover:text-white hover:border-slate-600 rounded-lg text-xs cursor-pointer transition font-sans"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="px-4 py-2 bg-gradient-to-r from-amber-500 to-yellow-600 hover:from-amber-600 hover:to-yellow-700 disabled:opacity-50 text-slate-950 font-bold rounded-lg shadow cursor-pointer transition font-sans text-xs flex items-center gap-1"
          >
            <Check className="w-4 h-4 text-slate-950" />
            {updateMutation.isPending ? '保存中…' : '保存修改'}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
