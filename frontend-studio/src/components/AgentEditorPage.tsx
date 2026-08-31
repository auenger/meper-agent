/**
 * AgentEditorPage — full-featured Agent editor on a dedicated page.
 *
 * Replaces the cramped edit modal. Groups all fields into collapsible sections:
 * 基本信息 / Prompt 配置 / 执行参数 / 工具绑定. Loaded from a single agent
 * detail query; saves via PUT. Publish/archive actions in the header.
 *
 * Used for both editing existing agents and completing a freshly-created one
 * (backend POST only takes name+description, so the editor fills the rest).
 */
import { useState, useEffect, useMemo, type FC, type ReactNode } from 'react';
import {
  ArrowLeft, Bot, Save, Loader2, Rocket, Archive, RefreshCw, Cpu, Wrench,
  ChevronDown, ChevronRight, Sparkles, Plus, Trash2, Mic, ListPlus,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { agentApi, agentKeys, type AgentUpdateInput } from '../services/agent-api';
import { modelApi, modelKeys } from '../services/model-api';
import { toolsApi, toolKeys, type BuiltinTool } from '../services/tools-api';
import { mcpApi, mcpKeys } from '../services/mcp-api';
import { workflowsApi, workflowKeys } from '../services/workflows-api';
import { knowledgeApi, knowledgeKeys } from '../services/knowledge-api';
import { toStudioAgent, fromStudioAgent } from '../services/adapters';
import { Select, Modal, type SelectOptionGroup } from './ui';
import { toast } from './ui/toast';
import type { Agent } from '../types';
import AvatarField from './AvatarField';

const inputCls =
  'w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-600 transition font-sans';

export function AgentEditorPage({
  agentId,
  onBack,
  onSaved,
}: {
  agentId: string;
  onBack: () => void;
  /** Called after a successful save; parent typically navigates to detail/chat. */
  onSaved?: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Agent | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: agentKeys.detail(agentId),
    queryFn: () => agentApi.get(agentId),
  });

  // Sync remote data into the local form once loaded.
  useEffect(() => {
    if (data) setForm(toStudioAgent(data));
  }, [data]);

  // Tool-binding + model data sources.
  const { data: modelsData } = useQuery({
    queryKey: modelKeys.list({ status: 'active', page_size: 100 }),
    queryFn: () => modelApi.list({ status: 'active', page_size: 100 }),
  });
  const activeModels = modelsData?.items ?? [];
  // Group active models by compatibility_type (provider) for the model selector.
  const modelGroups: SelectOptionGroup[] = useMemo(() => {
    const buckets = new Map<string, SelectOptionGroup>();
    for (const m of activeModels) {
      const provider = (m.compatibility_type ?? 'other') as string;
      if (!buckets.has(provider)) buckets.set(provider, { label: provider, options: [] });
      // value = Model record _id (e.g. "model_01..."); backend get_llm_client
      // only resolves references with the "model_" prefix. Using model_id
      // (e.g. "glm-5.2") here would fall through to the env-var fallback
      // path and fail with "Missing credentials".
      buckets.get(provider)!.options.push({
        value: m.id,
        label: `${m.name} (${m.model_id})`,
      });
    }
    return Array.from(buckets.values());
  }, [activeModels]);
  const { data: builtinsData } = useQuery({
    queryKey: ['builtin-tools'],
    queryFn: () => toolsApi.listBuiltins(),
  });
  const builtinTools: BuiltinTool[] = builtinsData ?? [];
  const { data: skillsData } = useQuery({
    queryKey: toolKeys.list({ source: 'markdown', page_size: 100 }),
    queryFn: () => toolsApi.list({ source: 'markdown', page_size: 100 }),
  });
  const skillTools = skillsData?.items ?? [];
  const { data: mcpData } = useQuery({
    queryKey: mcpKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => mcpApi.list({ page: 1, page_size: 100 }),
  });
  const mcpConnections = mcpData?.items ?? [];
  const { data: workflowsData } = useQuery({
    queryKey: workflowKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  });
  const workflows = workflowsData?.items ?? [];
  const { data: kbData } = useQuery({
    queryKey: knowledgeKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => knowledgeApi.list({ page: 1, page_size: 100 }),
  });
  const knowledgeBases = kbData?.items ?? [];

  const updateM = useMutation({
    mutationFn: (input: AgentUpdateInput) => agentApi.update(agentId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.all });
      refetch();
      // 统一 Toast 反馈（替代旧的 savedMsg 临时横幅），然后交回父级跳转。
      toast.success('配置已保存');
      if (onSaved) onSaved(agentId);
    },
  });

  const publishM = useMutation({
    mutationFn: () => agentApi.publish(agentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: agentKeys.all }),
  });

  const archiveM = useMutation({
    mutationFn: () => agentApi.archive(agentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: agentKeys.all }),
  });

  // 批量添加推荐项弹窗 + 紧凑列表的展开索引（hooks 必须位于 isLoading 早退之前）。
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState('');
  const [expandedItems, setExpandedItems] = useState<Set<number>>(new Set());
  const bulkParsed = useMemo(() => parseBulkRecommendedItems(bulkText), [bulkText]);

  const handleSave = () => {
    if (!form) return;
    if (!form.rolePrompt?.trim() || !form.taskPrompt?.trim()) {
      toast.error('「角色定义」和「任务描述」为必填项（对话执行校验）');
      return;
    }
    updateM.mutate(fromStudioAgent(form));
  };

  if (isLoading || !form) {
    return (
      <div className="flex items-center justify-center py-16 text-[#71717a]">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> 载入 Agent 配置…
      </div>
    );
  }

  const isPublished = form.status === 'online';
  const set = (patch: Partial<Agent>) => setForm({ ...form, ...patch });

  // ── 推荐项（快捷输入）操作 ──
  const recommendedItems = form.recommendedItems ?? [];

  const updateItem = (idx: number, patch: Partial<{ label: string; prompt: string }>) =>
    set({ recommendedItems: recommendedItems.map((it, i) => (i === idx ? { ...it, ...patch } : it)) });

  // 删除后数组塌缩，需同步平移展开索引（删除项之前的保留，之后的减一）。
  const removeItem = (idx: number) => {
    set({ recommendedItems: recommendedItems.filter((_, i) => i !== idx) });
    const next = new Set<number>();
    for (const i of expandedItems) {
      if (i < idx) next.add(i);
      else if (i > idx) next.add(i - 1);
    }
    setExpandedItems(next);
  };

  const addItem = () => {
    set({ recommendedItems: [...recommendedItems, { label: '', prompt: '' }] });
    setExpandedItems((prev) => new Set(prev).add(recommendedItems.length)); // 新项自动展开便于填写
  };

  const toggleItemExpanded = (idx: number) =>
    setExpandedItems((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  const handleBulkAdd = () => {
    const remaining = MAX_RECOMMENDED_ITEMS - recommendedItems.length;
    if (remaining <= 0 || bulkParsed.items.length === 0) return;
    const added = bulkParsed.items.slice(0, remaining);
    set({ recommendedItems: [...recommendedItems, ...added] });
    if (bulkParsed.items.length > remaining) {
      toast.warning(`已达上限 ${MAX_RECOMMENDED_ITEMS} 条：本次添加 ${added.length} 条，其余 ${bulkParsed.items.length - remaining} 条忽略`);
    } else {
      toast.success(`已添加 ${added.length} 条推荐项`);
    }
    setBulkOpen(false);
    setBulkText('');
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Scrollable form column — owns its own padding because content_stage
          renders p-0/overflow-hidden when the editor is open (see App.tsx). */}
      <div className="flex-1 min-h-0 overflow-y-auto max-w-3xl w-full mx-auto px-6 py-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
              <ArrowLeft className="w-4 h-4" />
            </button>
            <div>
              <div className="flex items-center gap-2 text-xs text-[#71717a]">
                <Bot className="w-3.5 h-3.5" />
                <span>智能体</span><span>/</span>
                <span className="text-white font-semibold">{form.name || '未命名'}</span>
              </div>
              <h2 className="text-sm font-bold text-white mt-0.5">编辑 Agent 配置</h2>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {isPublished ? (
              <button
                onClick={() => archiveM.mutate()}
                disabled={archiveM.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-[#27272a] hover:bg-[#27272a] text-[#a1a1aa] hover:text-white rounded-lg text-xs font-semibold transition cursor-pointer disabled:opacity-60"
              >
                {archiveM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Archive className="w-3.5 h-3.5" />}
                归档
              </button>
            ) : (
              <button
                onClick={() => publishM.mutate()}
                disabled={publishM.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold transition cursor-pointer disabled:opacity-60"
              >
                {publishM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Rocket className="w-3.5 h-3.5" />}
                发布
              </button>
            )}
          </div>
        </div>

        {/* ── Section: 基本信息 ── */}
        <Section title="基本信息" icon={<Bot className="w-3.5 h-3.5" />} defaultOpen>
          <Field label="智能体名称 *">
            <input className={inputCls} value={form.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label="头像">
            <AvatarField value={form.avatar} entityId={agentId} onChange={(url) => set({ avatar: url })} />
          </Field>
          <Field label="职责描述">
            <input className={inputCls} value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="这个 Agent 专门解决什么问题…" />
          </Field>
          <button
            type="button"
            role="switch"
            aria-checked={form.voiceEnabled}
            onClick={() => set({ voiceEnabled: !form.voiceEnabled })}
            className="flex min-h-11 w-full items-center justify-between gap-4 rounded-lg border border-[#27272a] bg-[#121214] px-3 py-2 text-left transition-colors duration-200 hover:border-[#3f3f46] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 cursor-pointer"
          >
            <span className="flex min-w-0 items-center gap-2.5">
              <Mic className={`h-4 w-4 shrink-0 ${form.voiceEnabled ? 'text-sky-400' : 'text-[#71717a]'}`} />
              <span>
                <span className="block text-xs font-semibold text-[#f4f4f5]">允许语音对话</span>
                <span className="mt-0.5 block text-[11px] leading-relaxed text-[#71717a]">
                  还需配置全局语音凭证，两个条件满足后才显示麦克风入口。
                </span>
              </span>
            </span>
            <span
              className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-200 ${
                form.voiceEnabled
                  ? 'border-sky-400 bg-sky-500'
                  : 'border-[#52525b] bg-[#27272a]'
              }`}
              aria-hidden="true"
            >
              <span
                className={`absolute top-0.5 h-4.5 w-4.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                  form.voiceEnabled ? 'translate-x-5' : 'translate-x-0.5'
                }`}
              />
            </span>
          </button>
        </Section>

        {/* ── Section: Prompt 配置 ── */}
        <Section title="Prompt 配置" icon={<Cpu className="w-3.5 h-3.5" />} defaultOpen>
          <Field label="角色定义 *（Role — 必填）">
            <textarea rows={2} className={`${inputCls} font-mono text-xs placeholder-[#52525b]`} placeholder="如：你是一位资深产品经理…" value={form.rolePrompt ?? ''} onChange={(e) => set({ rolePrompt: e.target.value })} />
          </Field>
          <Field label="任务描述 *（Task — 必填）">
            <textarea rows={3} className={`${inputCls} font-mono text-xs placeholder-[#52525b]`} placeholder="如：根据用户需求，输出功能拆解与优先级。" value={form.taskPrompt ?? ''} onChange={(e) => set({ taskPrompt: e.target.value })} />
          </Field>
          <div className="grid grid-cols-1 gap-4">
            <Field label="约束规则（Constraints · 可选）">
              <textarea rows={2} className={`${inputCls} font-mono text-xs placeholder-[#52525b]`} placeholder="如：回答必须用中文；不臆测。" value={form.constraintsPrompt ?? ''} onChange={(e) => set({ constraintsPrompt: e.target.value })} />
            </Field>
            <Field label="上下文信息（Context · 可选）">
              <textarea rows={2} className={`${inputCls} font-mono text-xs placeholder-[#52525b]`} placeholder="如：当前项目 meper-agent，技术栈 React+FastAPI。" value={form.contextPrompt ?? ''} onChange={(e) => set({ contextPrompt: e.target.value })} />
            </Field>
            <Field label="输出格式（Output Format · 可选）">
              <textarea rows={2} className={`${inputCls} font-mono text-xs placeholder-[#52525b]`} placeholder="如：用 Markdown 表格输出。" value={form.outputFormatPrompt ?? ''} onChange={(e) => set({ outputFormatPrompt: e.target.value })} />
            </Field>
            <Field label="补充说明（System Prompt · 可选）">
              <textarea rows={3} className={`${inputCls} font-mono text-xs`} value={form.systemPrompt} onChange={(e) => set({ systemPrompt: e.target.value })} />
            </Field>
          </div>
        </Section>

        {/* ── Section: 欢迎与引导（终端用户首屏） ── */}
        <Section title="欢迎与引导" icon={<Sparkles className="w-3.5 h-3.5" />} defaultOpen={false}>
          <Field label="欢迎词（Markdown，展示在终端用户首屏）">
            <textarea rows={3} className={`${inputCls} text-xs`} placeholder="如：你好！我是销售助理，可以问我业绩、客户、订单等相关问题。" value={form.welcomeMessage ?? ''} onChange={(e) => set({ welcomeMessage: e.target.value })} />
            <div className="text-[11px] text-slate-500 mt-1">留空则使用默认欢迎语。支持 Markdown 语法（加粗、列表等）。</div>
          </Field>
          <Field label="推荐问题 / 操作（终端用户可一键点击发送）">
            <div className="space-y-2">
              {recommendedItems.length > 0 && (
                <div className="max-h-[420px] overflow-y-auto space-y-1.5 pr-1">
                  {recommendedItems.map((item, idx) => {
                    const expanded = expandedItems.has(idx);
                    return (
                      <div key={idx} className="rounded-lg border border-[#27272a] bg-[#121214]">
                        <div className="flex items-center gap-2 px-2.5 py-2">
                          <button
                            type="button"
                            onClick={() => toggleItemExpanded(idx)}
                            className="flex flex-1 min-w-0 items-center gap-2 text-left cursor-pointer"
                            title={expanded ? '收起' : '展开编辑'}
                          >
                            {expanded
                              ? <ChevronDown className="w-3.5 h-3.5 text-[#71717a] shrink-0" />
                              : <ChevronRight className="w-3.5 h-3.5 text-[#71717a] shrink-0" />}
                            <span className="text-[10px] text-[#71717a] font-mono shrink-0">#{idx + 1}</span>
                            <span className={`text-xs truncate shrink-0 max-w-[240px] ${item.label ? 'text-white' : 'text-[#52525b] italic'}`}>
                              {item.label || '（未填写）'}
                            </span>
                            <span className="text-[11px] text-[#71717a] truncate">
                              {item.prompt ? `· ${item.prompt}` : '· 点击直接发送显示文案'}
                            </span>
                          </button>
                          <button type="button" onClick={() => removeItem(idx)} className="flex items-center gap-1 text-[#ef4444] text-[10px] hover:underline cursor-pointer shrink-0">
                            <Trash2 className="w-3 h-3" /> 删除
                          </button>
                        </div>
                        {expanded && (
                          <div className="px-3 pb-3 space-y-2">
                            <input className={inputCls} placeholder="显示文案（必填），如：导出本月报表" value={item.label} onChange={(e) => updateItem(idx, { label: e.target.value })} />
                            <input className={inputCls} placeholder="实际发送内容（留空则同显示文案）" value={item.prompt} onChange={(e) => updateItem(idx, { prompt: e.target.value })} />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {recommendedItems.length === 0 && (
                <div className="text-xs text-[#71717a] text-center py-3">暂无推荐项，点击下方按钮添加或批量导入</div>
              )}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={addItem}
                  disabled={recommendedItems.length >= MAX_RECOMMENDED_ITEMS}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-[#27272a] hover:bg-[#27272a] text-[#a1a1aa] hover:text-white rounded-lg text-xs font-semibold transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Plus className="w-3.5 h-3.5" /> 添加推荐项
                </button>
                <button
                  type="button"
                  onClick={() => setBulkOpen(true)}
                  disabled={recommendedItems.length >= MAX_RECOMMENDED_ITEMS}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-[#27272a] hover:bg-[#27272a] text-[#a1a1aa] hover:text-white rounded-lg text-xs font-semibold transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <ListPlus className="w-3.5 h-3.5" /> 批量添加
                </button>
                <span className="text-[10px] text-[#71717a] font-mono ml-auto">{recommendedItems.length} / {MAX_RECOMMENDED_ITEMS}</span>
              </div>
              <div className="text-[11px] text-slate-500">最多 {MAX_RECOMMENDED_ITEMS} 条。「实际发送内容」留空时，点击按钮直接发送「显示文案」。</div>
            </div>
          </Field>
        </Section>

        {/* ── Section: 执行参数 ── */}
        <Section title="执行参数" icon={<RefreshCw className="w-3.5 h-3.5" />} defaultOpen>
          <Field label="推理模型">
            <Select
              value={form.model || null}
              onChange={(v) => set({ model: v ?? '' })}
              placeholder={activeModels.length === 0 ? '暂无可用模型，请先在模型配置页添加' : '— 未选择 —'}
              groups={modelGroups}
            />
          </Field>
          <Field label={`思维活性 Temperature: ${form.temperature}`}>
            <input type="range" min="0" max="1.0" step="0.1" value={form.temperature} onChange={(e) => set({ temperature: parseFloat(e.target.value) })} className="w-full accent-indigo-500 cursor-pointer" />
          </Field>
          <Field label="最大重试次数（0-10）">
            <input type="number" min="0" max="10" className={`${inputCls} font-mono`} value={form.maxRetry ?? 3} onChange={(e) => set({ maxRetry: Math.max(0, Math.min(10, Number(e.target.value) || 0)) })} />
          </Field>
          <Field label="会话 Token 上限（0 = 全局默认）">
            <input type="number" min="0" max="10000000" step="10000" className={`${inputCls} font-mono`} value={form.maxTokens ?? 0} onChange={(e) => set({ maxTokens: Math.max(0, Number(e.target.value) || 0) })} placeholder="0 = 使用全局默认" />
            <div className="text-[11px] text-slate-500 mt-1">单次会话累计 Token 上限，超出后 Agent 自动停止。0 表示使用全局默认值（200000）。</div>
          </Field>
        </Section>

        {/* ── Section: 工具绑定 ── */}
        <Section title="工具绑定" icon={<Wrench className="w-3.5 h-3.5" />} defaultOpen>
          <ToolGroup title="内置工具 (Built-in)" hint={builtinTools.length === 0 ? '后端无内置工具' : undefined}>
            {builtinTools.map((t) => (
              <ToolChip key={`builtin:${t.name}`} label={t.name} checked={form.skills.includes(`builtin:${t.name}`)} onToggle={() => set({ skills: toggleSkill(form.skills, `builtin:${t.name}`) })} />
            ))}
          </ToolGroup>
          <ToolGroup title="技能 (Skills)" hint={skillTools.length === 0 ? '无已上传技能' : undefined}>
            {skillTools.map((t) => (
              <ToolChip key={t.id} label={t.name} checked={form.skills.includes(t.id)} onToggle={() => set({ skills: toggleSkill(form.skills, t.id) })} />
            ))}
          </ToolGroup>
          <ToolGroup title="MCP 连接" hint={mcpConnections.length === 0 ? '无 MCP 连接' : undefined}>
            {mcpConnections.map((c) => (
              <ToolChip key={`mcp:${c.id}`} label={c.name} checked={form.skills.includes(`mcp:${c.id}`)} onToggle={() => set({ skills: toggleSkill(form.skills, `mcp:${c.id}`) })} />
            ))}
          </ToolGroup>
          <ToolGroup title="工作流 (Workflows)" hint={workflows.length === 0 ? '无工作流' : undefined}>
            {workflows.map((w) => (
              <ToolChip key={w.id} label={w.name} checked={form.skills.includes(`workflow:${w.id}`)} onToggle={() => set({ skills: toggleSkill(form.skills, `workflow:${w.id}`) })} />
            ))}
          </ToolGroup>
          <ToolGroup title="知识库 (Knowledge Base)" hint={knowledgeBases.length === 0 ? '无知识库' : undefined}>
            {knowledgeBases.map((kb) => (
              <ToolChip key={`kb:${kb.id}`} label={kb.name} checked={form.skills.includes(`kb:${kb.id}`)} onToggle={() => set({ skills: toggleSkill(form.skills, `kb:${kb.id}`) })} />
            ))}
          </ToolGroup>
        </Section>

      </div>

      {/* Footer save bar — a flex sibling of the scroll area, so it is pinned
          flush to the bottom of the main column with no gap. bg-[#09090b] and
          border-[#27272a] are theme-aware via the index.css overrides. */}
      <div className="shrink-0 border-t border-[#27272a] bg-[#09090b] px-6 py-3">
        <div className="max-w-3xl mx-auto flex justify-end gap-3">
          <button onClick={onBack} className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-[#a1a1aa] hover:text-white rounded-lg cursor-pointer font-semibold text-xs">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={updateM.isPending}
            className="flex items-center gap-2 px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg cursor-pointer font-semibold text-xs disabled:opacity-60"
          >
            {updateM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            保存配置
          </button>
        </div>
      </div>

      {/* 批量添加推荐项弹窗 */}
      <Modal
        open={bulkOpen}
        title="批量添加推荐项"
        onOk={handleBulkAdd}
        onCancel={() => setBulkOpen(false)}
        okText={`添加 ${Math.max(0, Math.min(bulkParsed.items.length, MAX_RECOMMENDED_ITEMS - recommendedItems.length))} 条`}
        okButtonProps={{ disabled: bulkParsed.items.length === 0 || recommendedItems.length >= MAX_RECOMMENDED_ITEMS }}
        width={560}
      >
        <div className="space-y-3">
          <div className="text-[11px] text-[#71717a] leading-relaxed">
            自动识别格式，直接粘贴即可：
            <br />① 纯文本 — 每行一条 <code className="text-indigo-300">{'显示文案 | 实际发送内容'}</code>，不需要 []，不含 | 时整行即显示文案；
            <br />② JSON — 整段数组，或每行一个 <code className="text-indigo-300">{'{"label":"…","prompt":"…"}'}</code> 对象（[] 可省略，prompt 可省略）。
          </div>
          <textarea
            rows={8}
            className={`${inputCls} font-mono text-xs`}
            placeholder={'导出本月报表 | 帮我导出 2026 年 8 月的销售报表\n写一份周报\n\n也可粘贴 JSON：{"label": "导出本月报表", "prompt": "帮我导出 2026 年 8 月的销售报表"}'}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
          />
          {bulkText.trim() && (
            <div className="text-[11px] leading-relaxed">
              {bulkParsed.items.length > 0 ? (
                <span className="text-emerald-400">
                  解析出 {bulkParsed.items.length} 条（{bulkParsed.mode === 'json' ? 'JSON 格式' : '逐行文本格式'}）
                  {bulkParsed.items.length > MAX_RECOMMENDED_ITEMS - recommendedItems.length && (
                    <span className="text-amber-400">，超出上限仅添加前 {Math.max(0, MAX_RECOMMENDED_ITEMS - recommendedItems.length)} 条</span>
                  )}
                </span>
              ) : (
                <span className="text-rose-400">未解析出有效条目，请检查格式</span>
              )}
              {bulkParsed.invalidCount > 0 && (
                <span className="text-amber-400">
                  {' '}{bulkParsed.invalidCount} 条格式无效已跳过（label 必填 ≤{RECOMMENDED_LABEL_MAX} 字符，prompt ≤{RECOMMENDED_PROMPT_MAX} 字符）
                </span>
              )}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}

// ── Sub-components ──

// 推荐项约束 — 与后端 RecommendedItem / recommended_items max_length 保持一致
const RECOMMENDED_LABEL_MAX = 100;
const RECOMMENDED_PROMPT_MAX = 500;
const MAX_RECOMMENDED_ITEMS = 200;

/**
 * 解析批量输入的推荐项，自动识别格式：
 * ① 整段 JSON 数组（以 [ 开头）；
 * ② 每行一个 JSON 对象（可省略外层 []）；
 * ③ 每行一条纯文本（label | prompt，无 | 则整行为 label）。
 * 返回有效条目 + 被跳过的无效条数，供弹窗实时预览。
 */
function parseBulkRecommendedItems(text: string): {
  items: { label: string; prompt: string }[];
  invalidCount: number;
  mode: 'json' | 'lines' | 'none';
} {
  const trimmed = text.trim();
  if (!trimmed) return { items: [], invalidCount: 0, mode: 'none' };

  const validate = (label: unknown, prompt: unknown): { label: string; prompt: string } | null => {
    if (typeof label !== 'string') return null;
    const l = label.trim();
    const p = typeof prompt === 'string' ? prompt.trim() : '';
    if (!l || l.length > RECOMMENDED_LABEL_MAX || p.length > RECOMMENDED_PROMPT_MAX) return null;
    return { label: l, prompt: p };
  };

  // ① 整段 JSON 数组
  if (trimmed.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        const items: { label: string; prompt: string }[] = [];
        let invalidCount = 0;
        for (const row of parsed) {
          const rec = (row ?? {}) as Record<string, unknown>;
          const item = validate(rec.label, rec.prompt);
          if (item) items.push(item);
          else invalidCount++;
        }
        return { items, invalidCount, mode: 'json' };
      }
      // 合法 JSON 但不是数组 — 整体无效
      return { items: [], invalidCount: 1, mode: 'json' };
    } catch {
      // 非法 JSON → 回退逐行解析
    }
  }

  // ②/③ 逐行解析：{ 开头尝试单行 JSON 对象，否则按 label | prompt 文本
  const items: { label: string; prompt: string }[] = [];
  let invalidCount = 0;
  let jsonLines = 0;
  for (const line of trimmed.split('\n')) {
    const l = line.trim();
    if (!l) continue;
    if (l.startsWith('{')) {
      try {
        const row = JSON.parse(l) as Record<string, unknown>;
        const item = validate(row?.label, row?.prompt);
        if (item) {
          items.push(item);
          jsonLines++;
          continue;
        }
        invalidCount++; // JSON 对象但字段无效（缺 label 等）
        continue;
      } catch {
        invalidCount++; // 以 { 开头但不是合法 JSON — 视为无效而非怪异文本
        continue;
      }
    }
    const sep = l.indexOf('|');
    const item = validate(sep >= 0 ? l.slice(0, sep) : l, sep >= 0 ? l.slice(sep + 1) : '');
    if (item) items.push(item);
    else invalidCount++;
  }
  return { items, invalidCount, mode: jsonLines > 0 && jsonLines === items.length ? 'json' : 'lines' };
}

/** Collapsible section with a title bar. */
const Section: FC<{ title: string; icon?: ReactNode; defaultOpen?: boolean; children: ReactNode }> = ({
  title, icon, defaultOpen = false, children,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left cursor-pointer hover:bg-[#1c1c1f] transition"
      >
        {open ? <ChevronDown className="w-4 h-4 text-[#71717a]" /> : <ChevronRight className="w-4 h-4 text-[#71717a]" />}
        {icon && <span className="text-indigo-400">{icon}</span>}
        <span className="text-sm font-bold text-white">{title}</span>
      </button>
      {open && <div className="px-4 pb-4 space-y-4">{children}</div>}
    </div>
  );
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-slate-400 font-semibold text-xs font-sans">{label}</label>
      {children}
    </div>
  );
}

/** Toggle a prefixed skill token in/out of the skills array. */
function toggleSkill(skills: string[], token: string): string[] {
  return skills.includes(token) ? skills.filter((s) => s !== token) : [...skills, token];
}

const ToolGroup: FC<{ title: string; hint?: string; children: ReactNode }> = ({ title, hint, children }) => (
  <div className="p-3 rounded-lg bg-[#121214] border border-[#27272a]">
    <p className="text-[10px] text-[#71717a] uppercase tracking-wider font-bold mb-2">{title}</p>
    {hint ? <p className="text-[11px] text-[#52525b] italic">{hint}</p> : <div className="flex flex-wrap gap-1.5">{children}</div>}
  </div>
);
const ToolChip: FC<{ label: string; checked: boolean; onToggle: () => void }> = ({ label, checked, onToggle }) => (
  <button
    type="button"
    onClick={onToggle}
    className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition cursor-pointer select-none ${
      checked ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-[#18181b] border-[#27272a] text-[#a1a1aa] hover:text-white hover:border-[#52525b]'
    }`}
  >
    {checked ? '✓ ' : ''}{label}
  </button>
);
