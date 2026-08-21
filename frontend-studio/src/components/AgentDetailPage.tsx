/**
 * AgentDetailPage — left config + right chat split-screen for live testing.
 *
 * Mirrors agent-detail-page.tsx (legacy): left column renders a read-only
 * config card (with an edit affordance back to AgentSpace), right column
 * embeds ChatHomepage scoped to this single agent so you can iterate on the
 * config and test in real time.
 */
import { ArrowLeft, Cpu, Wrench, RefreshCw, Thermometer, Pencil } from 'lucide-react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { agentApi, agentKeys } from '../services/agent-api';
import { modelApi, modelKeys } from '../services/model-api';
import { toStudioAgent, displayToAgentStatus } from '../services/adapters';
import AvatarRender from './AvatarRender';
import { ChatHomepage } from './ChatHomepage';

const STATUS_DOT: Record<string, string> = {
  idle: 'bg-slate-500',
  thinking: 'bg-violet-500',
  online: 'bg-emerald-500',
  offline: 'bg-zinc-500',
};

/**
 * Lifecycle badge — maps the studio display status back to a backend
 * lifecycle (published/draft/archived) for a colored, labeled badge.
 */
const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  published: { label: '已发布', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
  draft: { label: '草稿', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
  archived: { label: '已归档', cls: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30' },
};

/** Split a flat skills view-model into 4 typed buckets by prefix. */
function groupSkills(skills: string[]): { builtin: string[]; skill: string[]; mcp: string[]; workflow: string[] } {
  const builtin: string[] = [];
  const skill: string[] = [];
  const mcp: string[] = [];
  const workflow: string[] = [];
  for (const s of skills) {
    if (s.startsWith('builtin:')) builtin.push(s.slice(8));
    else if (s.startsWith('mcp:')) mcp.push(s.slice(4));
    else if (s.startsWith('workflow:')) workflow.push(s.slice(9));
    else skill.push(s);
  }
  return { builtin, skill, mcp, workflow };
}

export function AgentDetailPage({
  agentId,
  theme,
  onBack,
  onOpenEdit,
}: {
  agentId: string;
  theme: 'dark' | 'light';
  onBack: () => void;
  onOpenEdit?: (id: string) => void;
}) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: agentKeys.detail(agentId),
    queryFn: () => agentApi.get(agentId),
  });
  const agent = data ? toStudioAgent(data) : null;

  // Load active models once to map _id → friendly name · provider.
  // Keyed by m.id (the "model_..." record id) to match the value stored
  // in Agent.default_model by the editor.
  const { data: modelsData } = useQuery({
    queryKey: modelKeys.list({ page_size: 100 }),
    queryFn: () => modelApi.list({ page_size: 100 }),
  });
  const modelById = new Map((modelsData?.items ?? []).map((m) => [m.id, m]));

  if (isLoading || !agent) {
    return (
      <div className="flex items-center justify-center py-16 text-[#71717a]">
        <RefreshCw className="w-5 h-5 animate-spin mr-2" /> 载入 Agent 详情…
      </div>
    );
  }

  const lifecycle = displayToAgentStatus(agent.status);
  const badge = STATUS_BADGE[lifecycle] ?? STATUS_BADGE.draft;
  const modelMeta = agent.model ? modelById.get(agent.model) : undefined;
  const modelLabel = modelMeta
    ? `${modelMeta.name} · ${modelMeta.compatibility_type}`
    : (agent.model || '（未设置）');
  const toolGroups = groupSkills(agent.skills);

  return (
    <div className="space-y-4">
      {/* Breadcrumb / header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={onBack} className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-white hover:bg-[#27272a] transition cursor-pointer shrink-0">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-[#71717a]">
              <span>智能体</span><span>/</span>
              <span className="text-white font-semibold truncate">{agent.name}</span>
            </div>
            <p className="text-[11px] text-[#52525b] mt-0.5">左侧配置 · 右侧实时对话测试</p>
          </div>
        </div>
        {onOpenEdit && (
          <button
            onClick={() => onOpenEdit(agentId)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold transition cursor-pointer shrink-0"
          >
            <Pencil className="w-3.5 h-3.5" /> 编辑
          </button>
        )}
      </div>

      {/* Split: config card | chat */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 h-[calc(100vh-180px)] min-h-[480px]">
        {/* Left: config */}
        <aside className="lg:col-span-4 rounded-xl border border-[#27272a] bg-[#18181b] overflow-y-auto p-5 space-y-4">
          <div className="flex items-center gap-3 pb-4 border-b border-[#27272a]">
            <div className={`w-12 h-12 rounded-xl ${agent.iconColor} flex items-center justify-center text-2xl shrink-0 overflow-hidden`}>
              <AvatarRender value={agent.avatar} className="w-full h-full" textClassName="text-2xl" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-bold text-white truncate">{agent.name}</h3>
              <span className="flex items-center gap-1.5 text-[10px] text-[#a1a1aa]">
                <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[agent.status] ?? 'bg-slate-500'}`} />
                {agent.statusText ?? agent.status}
              </span>
            </div>
            <span className={`shrink-0 px-2 py-0.5 rounded-full border text-[10px] font-bold tracking-wide ${badge.cls}`}>
              {badge.label}
            </span>
          </div>

          <ConfigRow label="职责描述">{agent.description || '（无描述）'}</ConfigRow>

          <ConfigRow icon={<Cpu className="w-3.5 h-3.5" />} label="推理模型">
            <span className="font-mono">{modelLabel}</span>
          </ConfigRow>

          <div className="grid grid-cols-2 gap-3">
            <ConfigRow icon={<Thermometer className="w-3.5 h-3.5" />} label="Temperature">
              <span className="font-mono">{agent.temperature}</span>
            </ConfigRow>
            <ConfigRow icon={<RefreshCw className="w-3.5 h-3.5" />} label="Max Retry">
              <span className="font-mono">{agent.maxRetry ?? 3}</span>
            </ConfigRow>
          </div>

          {agent.skills.length > 0 && (
            <ConfigRow icon={<Wrench className="w-3.5 h-3.5" />} label="已绑定工具/技能">
              <div className="space-y-2 mt-1">
                <ToolBucket title="内置工具" items={toolGroups.builtin} />
                <ToolBucket title="Skill" items={toolGroups.skill} />
                <ToolBucket title="MCP 连接" items={toolGroups.mcp} />
                <ToolBucket title="工作流" items={toolGroups.workflow} />
              </div>
            </ConfigRow>
          )}

          {agent.rolePrompt && (
            <ConfigRow label="角色定义 (Role)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.rolePrompt}
              </pre>
            </ConfigRow>
          )}

          {agent.taskPrompt && (
            <ConfigRow label="任务描述 (Task)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.taskPrompt}
              </pre>
            </ConfigRow>
          )}

          {agent.constraintsPrompt && (
            <ConfigRow label="约束规则 (Constraints)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.constraintsPrompt}
              </pre>
            </ConfigRow>
          )}

          {agent.contextPrompt && (
            <ConfigRow label="上下文信息 (Context)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.contextPrompt}
              </pre>
            </ConfigRow>
          )}

          {agent.outputFormatPrompt && (
            <ConfigRow label="输出格式 (Output Format)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.outputFormatPrompt}
              </pre>
            </ConfigRow>
          )}

          {agent.systemPrompt && (
            <ConfigRow label="补充说明 (System Prompt)">
              <pre className="mt-1 p-2 rounded-lg bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] whitespace-pre-wrap font-mono max-h-40 overflow-y-auto">
                {agent.systemPrompt}
              </pre>
            </ConfigRow>
          )}

          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 text-[11px] text-indigo-400 hover:text-indigo-300 cursor-pointer pt-2"
          >
            <RefreshCw className="w-3 h-3" /> 刷新配置（编辑后点此同步）
          </button>
        </aside>

        {/* Right: live chat scoped to this agent */}
        <section className="lg:col-span-8 h-full min-h-0 overflow-hidden">
          <ChatHomepage agents={[agent]} theme={theme} fixedAgentId={agent.id} />
        </section>
      </div>
    </div>
  );
}

function ConfigRow({
  icon, label, children,
}: {
  icon?: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div>
      <p className="flex items-center gap-1.5 text-[10px] text-[#71717a] uppercase tracking-wider font-bold mb-1">
        {icon}
        {label}
      </p>
      <div className="text-xs text-[#d4d4d8]">{children}</div>
    </div>
  );
}

/** A labeled bucket of tool chips; hidden entirely when empty. */
function ToolBucket({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] text-[#71717a] uppercase tracking-wider font-bold mb-1">
        {title} <span className="text-[#52525b]">({items.length})</span>
      </p>
      <div className="flex flex-wrap gap-1">
        {items.map((s) => (
          <span key={s} className="px-1.5 py-0.5 rounded bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] font-mono">
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}
