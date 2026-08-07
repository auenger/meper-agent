/**
 * WorkflowSpace — workflow card grid (list view).
 *
 * Replaces the dropdown-picker-inside-designer pattern with a proper list:
 * stats + search + status filter + card grid. Clicking a card opens the
 * react-flow editor (controlled via onOpen). Supports delete (workflowsApi.remove
 * existed but was previously uncalled). Mirrors the AgentSpace card layout.
 */
import { useState, useMemo, type FC } from 'react';
import { getErrorMessage } from '../lib/api-client';
import {
  Layers, Search, Plus, Pencil, Trash2, Loader2, X,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  workflowsApi,
  workflowKeys,
  type WorkflowSummary,
  type WorkflowStatusValue,
} from '../services/workflows-api';

const STATUS_META: Record<WorkflowStatusValue, { label: string; dot: string; text: string }> = {
  draft: { label: '草稿', dot: 'bg-slate-500', text: 'text-slate-400' },
  published: { label: '已发布', dot: 'bg-emerald-500', text: 'text-emerald-400' },
  archived: { label: '已归档', dot: 'bg-zinc-500', text: 'text-zinc-400' },
};

const STATUS_FILTERS: { value: WorkflowStatusValue | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'draft', label: '草稿' },
  { value: 'published', label: '已发布' },
  { value: 'archived', label: '已归档' },
];

function formatTime(ts: string): string {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleDateString('zh-CN');
  } catch {
    return ts;
  }
}

export function WorkflowSpace({
  onOpen,
  theme = 'dark',
}: {
  onOpen: (id: string) => void;
  theme?: 'dark' | 'light';
}) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<WorkflowStatusValue | 'all'>('all');
  const [confirmDelete, setConfirmDelete] = useState<WorkflowSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: workflowKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  });
  const workflows = data?.items ?? [];

  const filtered = useMemo(() => {
    let list = workflows;
    if (statusFilter !== 'all') list = list.filter((w) => w.status === statusFilter);
    const q = search.trim().toLowerCase();
    if (q) list = list.filter((w) => w.name.toLowerCase().includes(q) || w.description?.toLowerCase().includes(q));
    return list;
  }, [workflows, statusFilter, search]);

  const stats = useMemo(
    () => ({
      total: workflows.length,
      published: workflows.filter((w) => w.status === 'published').length,
      draft: workflows.filter((w) => w.status === 'draft').length,
      archived: workflows.filter((w) => w.status === 'archived').length,
    }),
    [workflows],
  );

  const createM = useMutation({
    mutationFn: () => workflowsApi.create({ name: `未命名工作流 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}` }),
    onSuccess: (wf) => {
      queryClient.invalidateQueries({ queryKey: workflowKeys.lists() });
      setError(null);
      onOpen(wf.id); // jump straight into the editor for the new workflow
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '创建失败')),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => workflowsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: workflowKeys.all });
      setConfirmDelete(null);
      setError(null);
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '删除失败')),
  });

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Layers className="w-5 h-5 text-indigo-400" />
            工作流
          </h2>
          <p className="text-xs text-[#71717a] mt-1">以卡片浏览工作流，点击进入可视化编辑。</p>
        </div>
        <button
          onClick={() => createM.mutate()}
          disabled={createM.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-semibold transition cursor-pointer shadow-md shadow-indigo-600/20 disabled:opacity-60"
        >
          {createM.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          新建工作流
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: '总数', value: stats.total, color: 'text-white' },
          { label: '已发布', value: stats.published, color: 'text-emerald-400' },
          { label: '草稿', value: stats.draft, color: 'text-slate-400' },
          { label: '已归档', value: stats.archived, color: 'text-zinc-400' },
        ].map((s) => (
          <div key={s.label} className="p-4 rounded-xl border border-[#27272a] bg-[#18181b]">
            <p className="text-[10px] text-[#71717a] uppercase tracking-widest font-bold">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#71717a]" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索工作流名称 / 描述"
            className="w-full pl-9 pr-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-sm text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 transition"
          />
        </div>
        <div className="flex items-center gap-1 p-1 bg-[#121214] border border-[#27272a] rounded-lg">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatusFilter(f.value)}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition cursor-pointer ${
                statusFilter === f.value ? 'bg-indigo-600 text-white' : 'text-[#a1a1aa] hover:text-white hover:bg-[#27272a]'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-300 text-xs">{error}</div>
      )}

      {/* Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-[#71717a]">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中…
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-[#52525b]">
          <Layers className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-sm">暂无工作流{search || statusFilter !== 'all' ? '匹配筛选' : ''}，点击右上角新建</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((wf) => (
            <WorkflowCard key={wf.id} wf={wf} onOpen={onOpen} onDelete={setConfirmDelete} theme={theme} />
          ))}
        </div>
      )}

      {/* Delete confirm modal */}
      {confirmDelete && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in">
          <div className="w-full max-w-sm bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#27272a]">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Trash2 className="w-4 h-4 text-rose-400" /> 确认删除
              </h3>
              <button onClick={() => setConfirmDelete(null)} className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-sm text-[#a1a1aa]">
                确认删除工作流「<span className="text-white font-semibold">{confirmDelete.name}</span>」？此操作不可撤销。
              </p>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setConfirmDelete(null)} className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold text-xs">
                  取消
                </button>
                <button
                  onClick={() => deleteM.mutate(confirmDelete.id)}
                  disabled={deleteM.isPending}
                  className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg cursor-pointer font-semibold text-xs disabled:opacity-60 flex items-center gap-2"
                >
                  {deleteM.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  删除
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const WorkflowCard: FC<{
  wf: WorkflowSummary;
  onOpen: (id: string) => void;
  onDelete: (wf: WorkflowSummary) => void;
  theme: 'dark' | 'light';
}> = ({ wf, onOpen, onDelete }) => {
  const meta = STATUS_META[wf.status] ?? STATUS_META.draft;
  return (
    <div className="group relative bg-[#18181b] border border-[#27272a] rounded-xl p-5 hover:border-[#52525b] transition cursor-pointer" onClick={() => onOpen(wf.id)}>
      {/* Header: icon + name + status */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-indigo-500 to-violet-500 flex items-center justify-center shrink-0 shadow-md">
            <Layers className="w-5 h-5 text-white" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-bold text-white truncate">{wf.name}</h3>
            <span className="text-[10px] text-[#71717a] font-mono">v{wf.version}</span>
          </div>
        </div>
        <span className={`flex items-center gap-1.5 text-[10px] font-bold ${meta.text} shrink-0`}>
          <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
          {meta.label}
        </span>
      </div>

      {/* Description */}
      <p className="text-xs text-[#a1a1aa] mt-3 line-clamp-2 leading-relaxed min-h-[32px]">
        {wf.description || '（无描述）'}
      </p>

      {/* Tags */}
      {wf.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-3">
          {wf.tags.slice(0, 4).map((t) => (
            <span key={t} className="px-1.5 py-0.5 rounded bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa]">
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between mt-4 pt-3 border-t border-[#27272a]">
        <span className="text-[10px] text-[#52525b] flex items-center gap-1">
          <Layers className="w-3 h-3" /> {wf.node_count} 节点
        </span>
        <span className="text-[10px] text-[#52525b]">{formatTime(wf.updated_at)}</span>
      </div>

      {/* Hover actions */}
      <div className="absolute top-3 right-3 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => onOpen(wf.id)}
          title="编辑"
          className="p-1.5 rounded-lg bg-[#121214] border border-[#27272a] text-[#a1a1aa] hover:text-indigo-400 hover:border-indigo-500/50 transition cursor-pointer"
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onDelete(wf)}
          title="删除"
          className="p-1.5 rounded-lg bg-[#121214] border border-[#27272a] text-[#a1a1aa] hover:text-rose-400 hover:border-rose-500/50 transition cursor-pointer"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
};
