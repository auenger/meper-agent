import { useState, useEffect, useRef, type ChangeEvent } from 'react';
import { getErrorMessage } from '../lib/api-client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Search, Compass, FolderUp, Trash2 } from 'lucide-react';
import { toolsApi, toolKeys } from '../services/tools-api';
import { toStudioSkill } from '../services/adapters';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';
import AvatarRender from './AvatarRender';
import type { Skill } from '../types';

/**
 * Studio Skill store — backed by real Tools + MCP connections.
 *
 * GAP (documented in features/active-feat-studio-core-crud/spec.md):
 * the backend Tools have no store metadata (rating / isAdded / isPaid /
 * includedSkills / datasets). Those fields are kept as a client-side local
 * map keyed by tool id so the existing store UI keeps working; they never
 * round-trip to the backend.
 */

interface ClientMeta {
  rating: number;
  isAdded: boolean;
  isPaid?: boolean;
  includedSkills?: string[];
  datasets?: string[];
}

export function SkillsStore({ onOpenSkill }: { onOpenSkill?: (skill: Skill) => void } = {}) {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Folder-upload input: webkitdirectory is a non-standard attribute, so set
  // it via ref on mount (avoids TS gaps + React unknown-attribute warnings).
  const folderInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = folderInputRef.current;
    if (el) {
      el.setAttribute('webkitdirectory', '');
      el.setAttribute('directory', '');
    }
  }, []);

  // Client-side store metadata (gap). Reset on remount; keyed by tool id.
  const [clientMeta, setClientMeta] = useState<Record<string, ClientMeta>>({});
  const setMeta = (id: string, patch: Partial<ClientMeta>) =>
    setClientMeta((prev) => ({ ...prev, [id]: { ...(prev[id] ?? {}), ...patch } as ClientMeta }));

  const { data: toolsData, isLoading } = useQuery({
    queryKey: toolKeys.list({ page: 1, page_size: 50 }),
    queryFn: () => toolsApi.list({ page: 1, page_size: 50 }),
  });

  const skills: Skill[] = (toolsData?.items ?? []).map((t) => {
    const base = toStudioSkill(t);
    const meta = clientMeta[t.id];
    return meta ? { ...base, ...meta } : base;
  });

  const uploadM = useMutation({
    mutationFn: (files: File[]) => toolsApi.upload(files),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: toolKeys.all });
      setNotice(`已上传 ${res.created.length} 个 Skill${res.errors.length ? `（${res.errors.length} 项跳过/失败）` : ''}`);
      setError(null);
    },
    onError: (e: unknown) => setError(getErrorMessage(e, '上传失败')),
  });

  const handleUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.currentTarget.files ?? []) as File[];
    if (files.length) uploadM.mutate(files);
    e.currentTarget.value = '';
  };

  const handleToggleAdded = (id: string, current: boolean) => setMeta(id, { isAdded: !current });

  const deleteM = useMutation({
    mutationFn: (id: string) => toolsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: toolKeys.all });
      toast.success('Skill 已删除');
    },
  });

  const handleDelete = async (id: string, name: string) => {
    const ok = await confirmDialog({
      title: `删除 Skill「${name}」？`,
      description: '该 Skill 的文件将被清除。若被 Agent 引用将拒绝删除。',
      okText: '删除',
      danger: true,
    });
    if (ok) deleteM.mutate(id);
  };

  const filteredSkills = skills.filter((skill) => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    return (
      skill.name.toLowerCase().includes(q) ||
      skill.description.toLowerCase().includes(q) ||
      skill.tags.join(' ').toLowerCase().includes(q)
    );
  });

  const packages = filteredSkills.filter((s) => s.tags.includes('技能包'));
  const datasets = filteredSkills.filter((s) => s.tags.includes('数据集'));

  return (
    <div className="space-y-6">
      {/* Toolbar: 标题 + 上传 + 搜索（MCP 连接管理已移至独立的 MCP 连接页面） */}
      <div className="flex flex-col sm:flex-row justify-between sm:items-center p-4 bg-[#18181b] rounded-xl border border-[#27272a] gap-4">
        <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600/20 border border-indigo-500/30 text-indigo-400">
          <Compass className="w-3.5 h-3.5" />
          技能商店 (Tools)
        </div>

        <div className="flex items-center gap-2">
          <label className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl shadow transition cursor-pointer flex items-center gap-1">
            上传 Markdown Skill
            <input type="file" accept=".md,.markdown" multiple className="hidden" onChange={handleUpload} />
          </label>
          <label className="px-3 py-1.5 text-xs font-semibold bg-sky-600 hover:bg-sky-500 text-white rounded-xl shadow transition cursor-pointer flex items-center gap-1">
            <FolderUp className="w-3.5 h-3.5" />
            上传 Skill 文件夹
            <input ref={folderInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
          </label>
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索更多技能..."
              className="pl-9 pr-4 py-1.5 w-64 text-xs bg-[#121214] border border-[#27272a] rounded-xl text-slate-300 focus:outline-none focus:border-indigo-500 font-sans font-medium"
            />
          </div>
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-rose-950/30 border border-rose-700/40 text-rose-300 text-xs">{error}</div>
      )}
      {notice && (
        <div className="px-3 py-2 rounded-lg bg-emerald-950/30 border border-emerald-700/40 text-emerald-300 text-xs">{notice}</div>
      )}

      <div className="space-y-8">
          {isLoading ? (
            <p className="text-xs text-[#71717a]">加载中…</p>
          ) : filteredSkills.length === 0 ? (
            <p className="text-xs text-[#71717a]">暂无工具，点击右上角「上传 Markdown Skill」添加。</p>
          ) : (
            <>
              {packages.length > 0 && (
                <div className="space-y-4">
                  <h3 className="font-semibold text-white tracking-wide text-xs uppercase font-sans">
                    技能 / 工具包（来自 GET /tools）
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                    {packages.map((skill) => (
                      <div
                        key={skill.id}
                        className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 flex flex-col justify-between hover:border-[#71717a] transition"
                      >
                        <div className="space-y-4">
                          <div className="flex items-start justify-between">
                            <div className="flex items-center gap-3.5">
                              <div className="w-14 h-14 rounded-xl bg-[#121214] border border-[#27272a] overflow-hidden flex items-center justify-center shrink-0">
                                <AvatarRender value={skill.avatar} className="w-full h-full" />
                              </div>
                              <div className="space-y-1">
                                <h4 className="text-normal font-bold text-[#fafafa] tracking-snug font-sans truncate">{skill.name}</h4>
                                <div className="flex items-center gap-2 text-[11px] text-[#a1a1aa] font-mono">
                                  <span className="px-1.5 py-0.5 rounded bg-[#121214] border border-[#27272a] font-sans text-[10px] text-indigo-400">
                                    {skill.author}
                                  </span>
                                </div>
                              </div>
                            </div>

                            <button
                              onClick={() => handleToggleAdded(skill.id, skill.isAdded)}
                              id={`btn_toggle_${skill.id}`}
                              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                                skill.isAdded
                                  ? 'bg-[#121214] hover:bg-[#18181b] text-emerald-400 border border-[#27272a]'
                                  : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-md'
                              }`}
                            >
                              {skill.isAdded ? '已整合' : '一键添加'}
                            </button>
                          </div>

                          <p className="text-xs text-[#a1a1aa] leading-relaxed font-sans line-clamp-2">
                            {skill.description}
                          </p>

                          <div className="flex items-center justify-between pt-1">
                            {onOpenSkill && (
                              <button
                                onClick={() => onOpenSkill(skill)}
                                className="text-[11px] text-indigo-400 hover:text-indigo-300 font-semibold cursor-pointer"
                              >
                                查看详情 / 编辑文件 →
                              </button>
                            )}
                            <button
                              onClick={() => handleDelete(skill.id, skill.name)}
                              title="删除"
                              className={`flex items-center gap-1 text-[11px] text-[#71717a] hover:text-rose-400 transition cursor-pointer ${onOpenSkill ? '' : 'ml-auto'}`}
                            >
                              <Trash2 className="w-3 h-3" /> 删除
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {datasets.length > 0 && (
                <div className="space-y-4 border-t border-[#27272a] pt-6">
                  <h3 className="font-semibold text-[#fafafa] tracking-wide text-xs uppercase font-sans">外部数据集映射</h3>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {datasets.map((skill) => (
                      <div key={skill.id} className="bg-[#18181b] border border-[#27272a] rounded-xl p-5">
                        <div className="flex items-center gap-2">
                          <div className="w-8 h-8 rounded-lg bg-[#121214] border border-[#27272a] overflow-hidden flex items-center justify-center shrink-0">
                            <AvatarRender value={skill.avatar} className="w-full h-full" />
                          </div>
                          <h4 className="text-normal font-bold text-[#fafafa] truncate">{skill.name}</h4>
                        </div>
                        <p className="text-xs text-[#a1a1aa] line-clamp-2 mt-2">{skill.description}</p>
                        <div className="flex items-center justify-between mt-3">
                          {onOpenSkill && (
                            <button
                              onClick={() => onOpenSkill(skill)}
                              className="text-[11px] text-indigo-400 hover:text-indigo-300 font-semibold cursor-pointer"
                            >
                              查看详情 →
                            </button>
                          )}
                          <button
                            onClick={() => handleDelete(skill.id, skill.name)}
                            title="删除"
                            className={`flex items-center gap-1 text-[11px] text-[#71717a] hover:text-rose-400 transition cursor-pointer ${onOpenSkill ? '' : 'ml-auto'}`}
                          >
                            <Trash2 className="w-3 h-3" /> 删除
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* When no bucketed packs/datasets, show the flat filtered list so
                  nothing is silently hidden. */}
              {packages.length === 0 && datasets.length === 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {filteredSkills.map((skill) => (
                    <div key={skill.id} className="bg-[#18181b] border border-[#27272a] rounded-xl p-4">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 min-w-0">
                          <div className="w-7 h-7 rounded-lg bg-[#121214] border border-[#27272a] overflow-hidden flex items-center justify-center shrink-0">
                            <AvatarRender value={skill.avatar} className="w-full h-full" />
                          </div>
                          <h4 className="text-xs font-bold text-[#fafafa] truncate">{skill.name}</h4>
                        </div>
                        <button
                          onClick={() => handleToggleAdded(skill.id, skill.isAdded)}
                          className={`text-[10px] font-semibold cursor-pointer ${skill.isAdded ? 'text-emerald-400' : 'text-indigo-400'}`}
                        >
                          {skill.isAdded ? '已添加' : '添加'}
                        </button>
                      </div>
                      <p className="text-[11px] text-[#a1a1aa] mt-2 line-clamp-2">{skill.description}</p>
                      <div className="flex items-center justify-between mt-3">
                        {onOpenSkill && (
                          <button
                            onClick={() => onOpenSkill(skill)}
                            className="text-[11px] text-indigo-400 hover:text-indigo-300 font-semibold cursor-pointer"
                          >
                            查看详情 →
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(skill.id, skill.name)}
                          title="删除"
                          className={`flex items-center gap-1 text-[11px] text-[#71717a] hover:text-rose-400 transition cursor-pointer ${onOpenSkill ? '' : 'ml-auto'}`}
                        >
                          <Trash2 className="w-3 h-3" /> 删除
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
      </div>
    </div>
  );
}
