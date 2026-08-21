import { useState, type FormEvent } from 'react';
import { getErrorMessage } from '../lib/api-client';
import {
  Plus, Edit, Bot, Trash2, MessageSquare, Rocket, Archive, Loader2, MoreVertical,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { agentApi, agentKeys } from '../services/agent-api';
import { toStudioAgent } from '../services/adapters';
import type { Agent } from '../types';
import AvatarRender from './AvatarRender';
import { Popover } from './ui';
import { confirmDialog } from './ui/confirm';

/**
 * AgentSpace — agent card list + create dialog.
 *
 * Slimmed down: the heavy edit modal moved to AgentEditorPage (onOpenEdit).
 * Cards keep publish/archive/delete + detail-test + edit (jumps to editor).
 */
export function AgentSpace({
  onOpenDetail,
  onOpenEdit,
}: {
  onOpenDetail?: (id: string) => void;
  onOpenEdit?: (id: string) => void;
} = {}) {
  const queryClient = useQueryClient();
  const [isCreating, setIsCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [error, setError] = useState<string | null>(null);
  // 当前展开三点菜单的卡片 id。受控 Popover：菜单项点击后需手动关闭
  // （click 模式下点浮层内部不会自动收起）。
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: agentKeys.list({}),
    queryFn: () => agentApi.list({ page: 1, page_size: 50, status: 'all' }),
  });
  const agents = (data?.items ?? []).map(toStudioAgent);

  const createM = useMutation({
    mutationFn: (input: { name: string; description?: string }) => agentApi.create(input),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: agentKeys.all });
      setError(null);
      setIsCreating(false);
      setNewName('');
      setNewDesc('');
      // Jump straight into the editor to fill the full config.
      if (onOpenEdit) onOpenEdit(created.id);
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '创建失败')),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => agentApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: agentKeys.all }),
    onError: (e: unknown) => setError(getErrorMessage(e, '删除失败')),
  });

  const publishM = useMutation({
    mutationFn: (id: string) => agentApi.publish(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: agentKeys.all }),
    onError: (e: unknown) => setError(getErrorMessage(e, '发布失败')),
  });

  const archiveM = useMutation({
    mutationFn: (id: string) => agentApi.archive(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: agentKeys.all }),
    onError: (e: unknown) => setError(getErrorMessage(e, '归档失败')),
  });

  const handleCreateSave = (e: FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    createM.mutate({ name: newName.trim(), description: newDesc.trim() || undefined });
  };

  const handleDelete = async (agent: Agent) => {
    const ok = await confirmDialog({
      title: `删除智能体「${agent.name}」？`,
      description: '删除后不可恢复。',
      okText: '删除',
      danger: true,
    });
    if (ok) deleteM.mutate(agent.id);
  };

  return (
    <div className="space-y-6">
      {/* Top action header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-[#27272a] pb-4.5 gap-4">
        <div className="space-y-0.5">
          <h2 className="text-sm font-bold text-[#fafafa] flex items-center gap-2">
            <Bot className="w-4 h-4 text-indigo-400 animate-pulse" />
            智能体成员看板 ({agents.length})
          </h2>
          <p className="text-xs text-[#71717a]">配置与发布具备独立推理心智、专用微调工具、及特定提示词模版的 AI 单元。</p>
        </div>
        <button
          onClick={() => setIsCreating(true)}
          id="btn_add_agent"
          className="px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg shadow-lg shadow-indigo-600/10 transition flex items-center gap-1.5 cursor-pointer"
        >
          <Plus className="w-4 h-4 text-emerald-400 font-bold" />
          创建独立 Agent
        </button>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-rose-950/30 border border-rose-700/40 text-rose-300 text-xs">{error}</div>
      )}

      {isLoading ? (
        <p className="text-xs text-[#71717a]">加载中…</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {agents.map((agent) => {
            const isPublished = agent.status === 'online';
            return (
              <div
                key={agent.id}
                className="bg-[#18181b] border border-[#27272a] rounded-xl flex flex-col justify-between overflow-hidden relative hover:border-[#3f3f46] transition duration-200"
              >
                <div className="p-5 space-y-4">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-12 h-12 bg-[#121214] rounded-xl flex items-center justify-center text-2.5xl border border-[#27272a] shadow-inner select-none overflow-hidden">
                        <AvatarRender value={agent.avatar} className="w-full h-full" textClassName="text-2.5xl" />
                      </div>
                      <div className="space-y-0.5">
                        <h4 className="text-normal font-bold text-white tracking-tight flex items-center gap-1.5 font-sans">
                          {agent.name}
                          <span className={`w-2 h-2 rounded-full ${
                            agent.status === 'online' ? 'bg-emerald-500'
                              : agent.status === 'offline' ? 'bg-slate-600'
                              : 'bg-amber-500'
                          }`} />
                        </h4>
                        <span className="text-[10px] text-[#71717a] font-mono leading-none block">ID: {agent.id}</span>
                      </div>
                    </div>

                    {/* Card actions: 三点菜单（Popover portal 到主题根，fixed 定位，
                        不被卡片 overflow-hidden 裁剪；打开时随滚动/resize 自动重定位） */}
                    <Popover
                      trigger="click"
                      open={menuOpenFor === agent.id}
                      onOpenChange={(o) => setMenuOpenFor(o ? agent.id : null)}
                      content={
                        <div className="text-xs">
                          {onOpenDetail && (
                            <button
                              onClick={() => { setMenuOpenFor(null); onOpenDetail(agent.id); }}
                              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[#d4d4d8] hover:bg-[#121214] hover:text-indigo-400 transition cursor-pointer"
                            >
                              <MessageSquare className="w-3.5 h-3.5" /> 详情与测试
                            </button>
                          )}
                          <button
                            onClick={() => { setMenuOpenFor(null); onOpenEdit?.(agent.id); }}
                            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[#d4d4d8] hover:bg-[#121214] hover:text-white transition cursor-pointer"
                          >
                            <Edit className="w-3.5 h-3.5" /> 编辑配置
                          </button>
                          <div className="border-t border-[#27272a] my-1" />
                          <button
                            onClick={() => { setMenuOpenFor(null); void handleDelete(agent); }}
                            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-rose-400 hover:bg-rose-950/30 transition cursor-pointer"
                          >
                            <Trash2 className="w-3.5 h-3.5" /> 删除
                          </button>
                        </div>
                      }
                    >
                      <button
                        className="flex-shrink-0 p-1 px-1.5 bg-[#121214] border border-[#27272a] rounded-lg text-slate-400 hover:text-white transition cursor-pointer"
                        title="更多操作"
                      >
                        <MoreVertical className="w-3.5 h-3.5" />
                      </button>
                    </Popover>
                  </div>

                  <div className="space-y-2.5">
                    <p className="text-xs text-[#a1a1aa] leading-relaxed min-h-[36px] line-clamp-2">{agent.description}</p>

                    <div className="flex flex-wrap gap-1.5 pt-2">
                      {agent.skills.slice(0, 5).map((s) => (
                        <span key={s} className="px-2 py-0.5 rounded-full bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] font-mono truncate max-w-[120px]">
                          {s}
                        </span>
                      ))}
                      {agent.skills.length > 5 && (
                        <span className="px-2 py-0.5 rounded-full bg-[#121214] border border-[#27272a] text-[10px] text-[#71717a] font-mono">
                          +{agent.skills.length - 5}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Footer: publish/archive */}
                <div className="px-5 py-3 border-t border-[#27272a] bg-[#121214]/40 flex items-center justify-between">
                  <span className="text-[10px] text-[#71717a] font-sans">{agent.lastActive}</span>
                  {isPublished ? (
                    <button
                      onClick={() => archiveM.mutate(agent.id)}
                      disabled={archiveM.isPending}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-semibold text-[#a1a1aa] hover:text-white border border-[#27272a] hover:bg-[#27272a] transition cursor-pointer disabled:opacity-50"
                    >
                      {archiveM.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Archive className="w-3 h-3" />}
                      归档
                    </button>
                  ) : (
                    <button
                      onClick={() => publishM.mutate(agent.id)}
                      disabled={publishM.isPending}
                      className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-semibold text-white bg-emerald-600/80 hover:bg-emerald-600 transition cursor-pointer disabled:opacity-50"
                    >
                      {publishM.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Rocket className="w-3 h-3" />}
                      发布
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* CREATE dialog (name + description only; full config on the editor page) */}
      {isCreating && (
        <div id="modal_create_agent" className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in text-xs">
          <div className="w-full max-w-md bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl">
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between bg-[#121214]/60">
              <h3 className="text-sm font-bold text-white flex items-center gap-2 font-sans">
                <Plus className="w-4 h-4 text-indigo-400" />
                创建独立 Agent
              </h3>
              <button onClick={() => setIsCreating(false)} className="text-[#71717a] hover:text-white font-bold cursor-pointer">✕</button>
            </div>

            <form onSubmit={handleCreateSave} className="p-6 space-y-4">
              <div className="space-y-1">
                <label className="text-slate-400 font-semibold uppercase tracking-wide">智能体名称 *</label>
                <input
                  type="text" required autoFocus value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="如: 全局分析官..."
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white focus:outline-none focus:border-indigo-600 transition"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-400 font-semibold uppercase tracking-wide">描述详情</label>
                <input
                  type="text" value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="这个 Agent 专门在哪些流程解决痛点..."
                  className="w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white focus:outline-none focus:border-indigo-600 transition"
                />
              </div>
              <p className="text-[10px] text-[#52525b] italic">创建后自动进入编辑页，可配置模型、Prompt、工具等。</p>
              <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end gap-3 pt-4 -mx-6 -mb-6">
                <button type="button" onClick={() => setIsCreating(false)} className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold">
                  取消
                </button>
                <button type="submit" disabled={createM.isPending} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold cursor-pointer shadow-md shadow-indigo-600/20 disabled:opacity-60 flex items-center gap-2">
                  {createM.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {createM.isPending ? '创建中…' : '立即创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
