import { useState, useRef, useEffect, useLayoutEffect, FormEvent, useCallback, type MouseEvent, type ChangeEvent } from 'react';
import { Agent, Message, type ChatAttachment, type TimelineEntry } from '../types';
import {
  Send, Plus, Sparkles, Trash2, FileCode, CheckCircle,
  Bot, Terminal, Loader2, Paperclip, Brain, X,
  Wrench, AlertTriangle, ChevronRight, User, Download, FileText, Image as ImageIcon, Mic,
  ThumbsUp, ThumbsDown, PanelLeftClose, PanelLeftOpen,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { userSkillsApi, type SessionFeedbackItem } from '../services/user-skills-api';
import {
  sessionApi, sessionKeys, type Session, type MessageRecord, type FileRef, getFileId,
} from '../services/session-api';
import { agentApi, agentKeys } from '../services/agent-api';
import { modelApi, modelKeys } from '../services/model-api';
import { toStudioAgent } from '../services/adapters';
import { getFileBlob, downloadFile as downloadFileById } from '../services/file-api';
import { parseSSEStream } from '../lib/sse-parser';
import { SessionFilesPanel, type SessionFilesPanelHandle } from './SessionFilesPanel';
import AvatarRender from './AvatarRender';
import { Markdown } from './Markdown';
import { detectPreviewKind } from './FilePreview';
import { FilePreviewModal } from './FilePreviewModal';
import { WorkflowTaskCard, parseTaskCreated } from './WorkflowTaskCard';
import WorkflowProposalCard from './workflow-proposal-card';
import {
  ClarificationFormCard,
  DISMISSED_CLARIFICATION_TEXT,
  type ClarificationField,
} from './clarification-form-card';
import { ChatVoiceComposer } from './voice/ChatVoiceComposer';
import { voiceConfigApi, voiceConfigKeys } from '../services/voice-config-api';

interface ChatHomepageProps {
  /** Studio agents already adapted to the view model; if absent we fetch. */
  agents?: Agent[];
  theme?: 'light' | 'dark';
  /** 固定 agent 模式：会话列表按该 agent 过滤，点 + 直通新建会话（不弹选择框）。 */
  fixedAgentId?: string;
  /** 会话列表侧栏默认收起为窄轨（仅展开/新建按钮），让对话主区占满宽度。
   *  AgentDetailPage 预览页传 true；主 chat tab 不传保持展开。 */
  defaultSidebarCollapsed?: boolean;
}

/** Convert a stored agent message + its timeline into display Messages. */
/**
 * 从工具结果文本里解析 agent 写出的 output 文件路径。
 * write_to_output 返回形如 "Successfully wrote 123 bytes to output/report.png"，
 * 路径相对 output/，即 sessionApi.previewFile 的 filePath 入参，无需二次转换。
 * 也可命中 "output/xxx" 字面量（agent 在 markdown 链接里引用产物时）。
 * 返回去重后的 ChatAttachment[]（source='output'）。
 */
const OUTPUT_PATH_RE = /\boutput\/([^\s"'<>)\\]+\.[A-Za-z0-9]+)/g;
export function parseOutputAttachments(text: string): ChatAttachment[] {
  if (!text) return [];
  const seen = new Set<string>();
  const out: ChatAttachment[] = [];
  for (const match of text.matchAll(OUTPUT_PATH_RE)) {
    const rel = match[1]; // 相对 output/ 的路径
    if (seen.has(rel)) continue;
    seen.add(rel);
    out.push({
      source: 'output',
      ref: rel,
      name: rel.split('/').pop() || rel,
    });
  }
  return out;
}

/** Convert a persisted backend agent MessageRecord into a single studio
 *  Message whose `timeline` carries text/tool/thinking/error entries in
 *  chronological order (mirrors frontend historyEntryToTimeline). Tool_call
 *  + tool_result are merged into one tool entry; an unanswered
 *  ask_clarification/confirm_workflow tool_call stays interactive
 *  (isInterrupted). Output-file attachments parsed from tool results are
 *  attached to the message. */
function agentMessageToDisplay(rec: MessageRecord, agentName: string, avatar: string): Message {
  const timeline: TimelineEntry[] = [];
  // Map tool_call_id → index of the last pending tool_call entry (for merging
  // the matching tool_result). Prefer id over tool_name so parallel same-name
  // calls (e.g. two kb_search) pair correctly instead of overwriting each other.
  const pendingToolCalls = new Map<string, number>();
  const outputAtts: ChatAttachment[] = [];
  let isInterrupted = false;

  for (let i = 0; i < (rec.timeline_entries ?? []).length; i++) {
    const entry = (rec.timeline_entries ?? [])[i];
    if (entry.type === 'text' || entry.type === 'final_answer') {
      const t = (entry.content ?? '').trim();
      if (t) timeline.push({ id: `${rec._id}-text-${i}`, type: 'text', content: t });
    } else if (entry.type === 'thinking') {
      timeline.push({ id: `${rec._id}-think-${i}`, type: 'thinking', content: entry.content ?? '' });
    } else if ((entry.type as string) === 'error') {
      timeline.push({ id: `${rec._id}-err-${i}`, type: 'error', content: `❌ ${entry.content || '执行出错'}` });
    } else if (entry.type === 'tool_call' || entry.type === 'tool') {
      const name = entry.tool_name ?? '';
      // tool entry (already merged by backend) or tool_call (need merge).
      const isError =
        entry.type === 'tool' &&
        typeof entry.content === 'string' &&
        /\b(error|fail)/i.test(entry.content);
      const isInterruptTool = name === 'ask_clarification' || name === 'confirm_workflow';
      const idx = timeline.push({
        id: `${rec._id}-tool-${name}-${i}`,
        type: 'tool',
        content: '',
        toolName: name,
        toolCallId: entry.id,
        args: entry.args,
        toolStatus: isInterruptTool
          ? 'success'
          : entry.type === 'tool'
            ? (isError ? 'error' : 'success')
            : 'running',
        result: entry.type === 'tool' ? entry.content : undefined,
      }) - 1;
      if (entry.type === 'tool_call') {
        // Prefer tool_call_id; fall back to tool_name for older records.
        const mapKey = entry.id || name;
        if (mapKey) pendingToolCalls.set(mapKey, idx);
      }
      // 收集 output 产物。
      if (entry.type === 'tool' && typeof entry.content === 'string') {
        outputAtts.push(...parseOutputAttachments(entry.content));
      }
    } else if (entry.type === 'tool_result') {
      const name = entry.tool_name ?? '';
      // 真正的 fallback：status 存在时以它为准（success 时内容含 error 字样
      // 也不得误判——此前实现是 ||，正常返回的文档片段/日志被标失败）；
      // 仅旧数据（无 status）才文本嗅探。
      const isError =
        entry.status != null
          ? entry.status === 'error'
          : typeof entry.content === 'string' && /\b(error|fail)/i.test(entry.content);
      // Prefer tool_call_id; fall back to tool_name for older records.
      const mapKey = entry.tool_call_id || name;
      const pendingIdx = mapKey ? pendingToolCalls.get(mapKey) : undefined;
      if (pendingIdx !== undefined && timeline[pendingIdx]) {
        timeline[pendingIdx] = {
          ...timeline[pendingIdx],
          result: entry.content ?? '',
          toolStatus: isError ? 'error' : 'success',
        };
        pendingToolCalls.delete(mapKey);
      } else {
        timeline.push({
          id: `${rec._id}-tool-${name}-${i}`,
          type: 'tool',
          content: '',
          toolName: name,
          toolCallId: entry.tool_call_id,
          result: entry.content ?? '',
          toolStatus: isError ? 'error' : 'success',
        });
      }
      if (typeof entry.content === 'string') {
        outputAtts.push(...parseOutputAttachments(entry.content));
      }
    }
    // tool_call_start / interrupt are transient — skip in history.
  }

  // 未答的 ask_clarification/confirm_workflow（tool_call 无 tool_result）→ 中断态。
  for (const e of timeline) {
    if (
      e.type === 'tool' &&
      (e.toolName === 'ask_clarification' || e.toolName === 'confirm_workflow') &&
      !e.result
    ) {
      isInterrupted = true;
      break;
    }
  }

  const dedupOutput = outputAtts.filter(
    (a, i, arr) => arr.findIndex((b) => b.ref === a.ref) === i,
  );

  return {
    id: rec._id,
    senderName: agentName,
    avatar,
    role: 'agent',
    content: '',
    timestamp: new Date(rec.created_at).toLocaleString(),
    requestId: rec.request_id,
    timeline: timeline.length > 0 ? timeline : undefined,
    isInterrupted,
    attachment: fileRefToAttachment(rec.files?.[0]),
    attachments: dedupOutput.length > 0 ? dedupOutput : undefined,
    usage: rec.token_usage,
  };
}

function fileRefToAttachment(file?: FileRef): Message['attachment'] | undefined {
  if (!file) return undefined;
  const name = file.name;
  const mime = file.mime_type ?? '';
  const type: Message['attachment']['type'] = mime.startsWith('image/')
    ? 'image'
    : mime.startsWith('video/')
      ? 'video'
      : mime.includes('markdown') || name.endsWith('.md')
        ? 'markdown'
        : 'code';
  return { name, type, content: file.storage_key || name };
}

/** 机器人头像：委托 AvatarRender 统一渲染（URL→img / emoji→兼容 / 空→logo）。 */
function BotAvatar({ avatar, className }: { avatar?: string; className?: string }) {
  return <AvatarRender value={avatar} className={className} />;
}

/* ─── 对话内附件预览（用户上传 + agent 产出统一渲染） ─── */

/** 按 source 取 blob：upload 走 FileRef.id，output 走 session output 路径。 */
async function fetchAttachmentBlob(att: ChatAttachment, sessionId: string | null): Promise<Blob> {
  if (att.source === 'upload') {
    return getFileBlob(att.ref);
  }
  if (!sessionId) throw new Error('会话未加载，无法预览产出文件');
  const { blob } = await sessionApi.previewFile(sessionId, att.ref);
  return blob;
}

/** 下载附件：upload 用 file-api（<a download>），output 用 sessionApi.downloadFile。 */
async function downloadAttachment(att: ChatAttachment, sessionId: string | null): Promise<void> {
  if (att.source === 'upload') {
    await downloadFileById(att.ref, att.name);
    return;
  }
  if (!sessionId) throw new Error('会话未加载，无法下载产出文件');
  await sessionApi.downloadFile(sessionId, att.ref);
}

/** 字节数格式化（与 SessionFilesPanel 的 formatFileSize 一致）。 */
function formatBytes(bytes?: number): string {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

/**
 * 单个附件的内联展示：图片懒加载缩略图直显，其余类型显示文件卡片。
 * 点击图片或「预览」打开 FilePreviewModal（复用，支持缩放/Esc/富渲染/下载）。
 * 需要父级传入 sessionId（output 来源取数依赖）。
 */
function ChatAttachmentCard({
  att,
  sessionId,
}: {
  att: ChatAttachment;
  sessionId: string | null;
}) {
  const kind = detectPreviewKind(att.name, att.mime_type);
  const isImage = kind === 'image';

  // 图片缩略图 blob（懒加载，卸载时回收 objectURL）
  const [thumbUrl, setThumbUrl] = useState<string>();
  const [thumbError, setThumbError] = useState(false);
  const [loadingThumb, setLoadingThumb] = useState(isImage);

  // 弹窗预览态：打开时按需拉取并按类型喂给 FilePreviewModal
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewText, setPreviewText] = useState<string>();
  const [previewImageUrl, setPreviewImageUrl] = useState<string>();

  // 图片缩略图：挂载即拉一次（output 图片同样从 blob 取）
  useEffect(() => {
    if (!isImage) return;
    let url: string | undefined;
    let cancelled = false;
    (async () => {
      try {
        const blob = await fetchAttachmentBlob(att, sessionId);
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setThumbUrl(url);
      } catch {
        if (!cancelled) setThumbError(true);
      } finally {
        if (!cancelled) setLoadingThumb(false);
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [att, sessionId, isImage]);

  // 打开弹窗预览：按 detectPreviewKind 决定喂 text 还是 imageUrl；二进制直接走下载
  const openPreview = async () => {
    if (kind === 'binary') {
      // 不可预览：直接下载
      try {
        await downloadAttachment(att, sessionId);
      } catch {
        /* ignore */
      }
      return;
    }
    setPreviewOpen(true);
    setPreviewLoading(true);
    try {
      const blob = await fetchAttachmentBlob(att, sessionId);
      if (kind === 'image') {
        setPreviewImageUrl(URL.createObjectURL(blob));
        setPreviewText(undefined);
      } else {
        setPreviewText(await blob.text());
        setPreviewImageUrl(undefined);
      }
    } catch {
      // 拉取失败：留空，FilePreview 会渲染占位
    } finally {
      setPreviewLoading(false);
    }
  };

  // 关闭弹窗时回收 imageUrl（text 不需要回收）
  const closePreview = () => {
    if (previewImageUrl) URL.revokeObjectURL(previewImageUrl);
    setPreviewImageUrl(undefined);
    setPreviewText(undefined);
    setPreviewOpen(false);
  };

  // ── 图片：缩略图直显（可点击放大） ──
  if (isImage) {
    return (
      <>
        <button
          type="button"
          onClick={openPreview}
          className="relative rounded-lg overflow-hidden border border-[#27272a] bg-[#121214] hover:border-indigo-500/50 transition-colors cursor-pointer block"
          title={att.name}
        >
          {loadingThumb ? (
            <div className="w-48 h-32 flex items-center justify-center text-[#71717a]">
              <Loader2 className="w-4 h-4 animate-spin" />
            </div>
          ) : thumbError ? (
            <div className="w-48 h-32 flex flex-col items-center justify-center text-[#71717a] gap-1 px-2">
              <ImageIcon className="w-5 h-5" />
              <span className="text-[10px] truncate max-w-full">{att.name}</span>
            </div>
          ) : (
            thumbUrl && (
              <img
                src={thumbUrl}
                alt={att.name}
                // 固定尺寸 + cover:与 loading/error 占位(w-48 h-32)一致,
                // 竖图/横图/多图排列整齐无跳动,点击弹窗看原图(不裁剪)。
                className="w-48 h-32 object-cover block"
                draggable={false}
              />
            )
          )}
        </button>
        <FilePreviewModal
          open={previewOpen}
          filename={att.name}
          mime={att.mime_type}
          imageUrl={previewImageUrl}
          text={previewText}
          onClose={closePreview}
          onDownload={() => downloadAttachment(att, sessionId)}
        />
      </>
    );
  }

  // ── 非图片：文件卡片（名称 + 类型徽标 + 预览/下载） ──
  return (
    <>
      <div className="flex items-center gap-2.5 w-72 px-3 py-2.5 rounded-lg border border-[#27272a] bg-[#18181b] hover:border-indigo-500/40 transition-colors group">
        <div className="w-8 h-8 rounded-md bg-[#121214] border border-[#27272a] flex items-center justify-center shrink-0">
          <FileText className="w-4 h-4 text-indigo-400" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold text-slate-200 truncate" title={att.name}>
            {att.name}
          </p>
          <p className="text-[9px] text-[#71717a] font-mono uppercase">
            {att.mime_type || kind}
            {att.size ? ` · ${formatBytes(att.size)}` : ''}
          </p>
        </div>
        {kind === 'binary' ? (
          <button
            type="button"
            onClick={() => downloadAttachment(att, sessionId)}
            title="下载"
            className="p-1.5 text-[#71717a] hover:text-indigo-400 cursor-pointer transition-colors shrink-0"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={openPreview}
            title="预览"
            className="p-1.5 text-[#71717a] hover:text-indigo-400 cursor-pointer transition-colors shrink-0"
          >
            <FileCode className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <FilePreviewModal
        open={previewOpen}
        filename={att.name}
        mime={att.mime_type}
        text={previewText}
        imageUrl={previewImageUrl}
        onClose={closePreview}
        onDownload={() => downloadAttachment(att, sessionId)}
      />
      {previewLoading && previewOpen && null}
    </>
  );
}

function userMessageToDisplay(rec: MessageRecord): Message {
  // 全部附件（非仅首个）映射成可预览 ChatAttachment；source='upload' 走 getFileBlob(id)
  const attachments: ChatAttachment[] | undefined =
    rec.files && rec.files.length > 0
      ? rec.files.map((f) => ({
          source: 'upload' as const,
          ref: getFileId(f),
          name: f.name,
          mime_type: f.mime_type,
          size: f.size,
        }))
      : undefined;
  return {
    id: rec._id,
    senderName: '我',
    avatar: '👩‍💼',
    role: 'user',
    content: rec.content,
    timestamp: new Date(rec.created_at).toLocaleString(),
    attachment: fileRefToAttachment(rec.files?.[0]),
    attachments,
  };
}

export function ChatHomepage({ agents: agentsProp, theme = 'dark', fixedAgentId, defaultSidebarCollapsed = false }: ChatHomepageProps) {
  const qc = useQueryClient();

  // ── Agents (fallback fetch if not passed in) ──
  const { data: agentsData } = useQuery({
    queryKey: agentKeys.list({ page: 1, page_size: 50, status: 'all' }),
    queryFn: () => agentApi.list({ page: 1, page_size: 50, status: 'all' }),
    enabled: !agentsProp || agentsProp.length === 0,
    staleTime: 60_000,
  });
  const agents: Agent[] =
    agentsProp && agentsProp.length > 0
      ? agentsProp
      : (agentsData?.items ?? []).map(toStudioAgent);
  // `agents` is recomputed (new array ref) on every render via .map(), so it
  // must NOT be a dependency of the history-loading effect — otherwise that
  // effect re-fires mid-stream and wipes the optimistic first message. Keep a
  // ref so the effect can read the latest agent name/avatar without re-running.
  const agentsRef = useRef<Agent[]>(agents);
  agentsRef.current = agents;

  // ── Models: build an id → name map so the chat header / input box can show
  // the human-readable model name instead of the raw "model_..." id. ──
  const { data: modelsData } = useQuery({
    queryKey: modelKeys.list({ page_size: 100 }),
    queryFn: () => modelApi.list({ page_size: 100 }),
    staleTime: 60_000,
  });
  const modelNameById = new Map(
    (modelsData?.items ?? []).map((m) => [m.id, m.name]),
  );
  const modelLabel = (id?: string) => (id ? (modelNameById.get(id) ?? id) : 'Auto');

  const { data: voiceStatus } = useQuery({
    queryKey: voiceConfigKeys.status,
    queryFn: voiceConfigApi.status,
    staleTime: 60_000,
    retry: false,
  });
  const voiceConfigured = voiceStatus?.configured === true;

  // ── Sessions list ──
  // 固定 agent 模式按 agent 过滤。key 挂参数（sessionKeys.list({agent_id})）与
  // 全量 key（sessionKeys.lists()）互不覆盖；refreshSessions 对 lists() 做前缀
  // 失效，两个 key 同时命中，主 chat tab 与详情页列表始终一致。
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery({
    queryKey: fixedAgentId ? sessionKeys.list({ agent_id: fixedAgentId }) : sessionKeys.lists(),
    queryFn: () =>
      sessionApi.list({
        page: 1,
        page_size: 50,
        ...(fixedAgentId ? { agent_id: fixedAgentId } : {}),
      }),
    staleTime: 15_000,
  });
  const sessions: Session[] = sessionsData?.items ?? [];

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  // ── 消息级反馈（§8.2 v2）：会话各轮投票态，流结束/切会话时刷新 ──
  const [feedbackTick, setFeedbackTick] = useState(0);
  const [feedbackMap, setFeedbackMap] = useState<Record<string, SessionFeedbackItem>>({});
  useEffect(() => {
    if (!activeSessionId) return;
    let cancelled = false;
    userSkillsApi.sessionFeedback(activeSessionId)
      .then((items) => {
        if (cancelled) return;
        const map: Record<string, SessionFeedbackItem> = {};
        for (const it of items) map[it.request_id] = it;
        setFeedbackMap(map);
      })
      .catch(() => { /* 端点不可用（旧后端）——静默 */ });
    return () => { cancelled = true; };
  }, [activeSessionId, feedbackTick]);

  const handleVoteMessage = useCallback(async (requestId: string, value: 1 | -1) => {
    if (!activeSessionId) return;
    const current = feedbackMap[requestId]?.value ?? 0;
    if (current === value) return;  // 同向已投——no-op
    try {
      await userSkillsApi.voteMessage(activeSessionId, requestId, value);
      setFeedbackMap((prev) => ({
        ...prev,
        [requestId]: { ...(prev[requestId] ?? { request_id: requestId, value: 0, skills: [] }), value },
      }));
    } catch {
      // 投票失败静默——不打断对话
    }
  }, [activeSessionId, feedbackMap]);

  const [showDropdown, setShowDropdown] = useState(false);
  const [showAgentSelectModal, setShowAgentSelectModal] = useState(false);
  // 会话列表侧栏收起态：收起后仅剩窄轨（展开/新建按钮），对话主区拉宽。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(defaultSidebarCollapsed);
  const [inputText, setInputText] = useState('');
  const [inputMode, setInputMode] = useState<'text' | 'voice'>('text');
  const [isStreaming, setIsStreaming] = useState(false);
  // Live messages for the active session (history + in-flight stream deltas).
  const [liveMessages, setLiveMessages] = useState<Message[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  // Thinking mode toggle (enable_thinking execution param) + pending file attachments.
  // NOTE: pendingFiles holds raw File objects queued locally — the actual upload
  // happens inside handleSendMessage (before the stream fires), mirroring
  // frontend/src/components/chat-panel.tsx. This avoids the race where the text
  // prompt is sent before the file upload resolves.
  const [enableThinking, setEnableThinking] = useState(true);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  // Right-pane tab: 'chat' (messages) | 'files' (generated-file manager).
  const [rightTab, setRightTab] = useState<'chat' | 'files'>('chat');

  const messageEndRef = useRef<HTMLDivElement>(null);
  // 对话滚动容器：贴底判定用（只有输出贴底时才自动跟随，见 isPinnedToBottom）。
  const chatScrollRef = useRef<HTMLDivElement>(null);
  // 每次消息更新【前】捕获的贴底状态：DOM 提交后据此决定是否跟随滚动。
  // 不能在 effect 里现测——新内容撑高后距底必超阈值，守卫会永久脱锁，
  // 表现为流式输出不再自动跟随（需手动滚动）。
  const pinnedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const filesPanelRef = useRef<SessionFilesPanelHandle>(null);
  const voiceAgentMsgIdRef = useRef<string | null>(null);
  // ask_clarification / confirm_workflow 中断态：SSE 收到 interrupt 或历史回填时
  // 记录对应的消息 id，使下一次发送走 /resume 而非 /stream（避免 agent 重复提问）。
  const pendingInterruptRef = useRef<{ toolMsgId: string } | null>(null);
  // 流式回答中被切换走的会话：后端后台任务仍会跑完并把完整回答一次性落库。
  // 记录这些 sessionId，切回时轮询 getDetail 直到 agent 回复落库后整体显示。
  const pendingBackgroundSessionsRef = useRef<Set<string>>(new Set());

  // ── Timeline 流式渲染 refs（对齐 frontend chat-panel 的 RAF 批处理）──
  // 每个 text_delta 只进 buffer，一帧最多 setState 一次，保证平滑且多轮
  // text→tool→text 顺序正确（tool_call_start 重置 textEntryIdRef 让新轮 text
  // 开新 entry）。
  const deltaBufferRef = useRef<{ agentMsgId: string; delta: string } | null>(null);
  const rafIdRef = useRef<number | null>(null);
  const textEntryIdRef = useRef<string | null>(null);
  const textStartedRef = useRef(false);

  /** 输出是否贴底（距底部 120px 内）。自动跟随滚动只在贴底时发生——
   *  用户上翻阅读历史、或点开工具/思考详情时，即使 liveMessages 变化
   *  （流式输出/展开收起）也不会把视口拽到底部。
   *  注意：只允许在消息更新【前】调用（DOM 反映用户真实阅读位置），
   *  更新后检测会把"单帧新增内容超 120px"误判为用户上翻。 */
  const isPinnedToBottom = useCallback((): boolean => {
    const el = chatScrollRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  /** 统一的流式消息更新入口：更新前捕获贴底状态，滚动 effect 据此跟随。 */
  const updateLiveMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      pinnedRef.current = isPinnedToBottom();
      setLiveMessages(updater);
    },
    [isPinnedToBottom],
  );

  /** 把累积的 delta flush 进 agent 消息的 timeline（追加到当前 text entry 或新建）。 */
  const flushDelta = useCallback(() => {
    rafIdRef.current = null;
    const buf = deltaBufferRef.current;
    if (!buf) return;
    deltaBufferRef.current = null;
    const { agentMsgId, delta } = buf;
    updateLiveMessages((prev) =>
      prev.map((m) => {
        if (m.id !== agentMsgId) return m;
        const tl = [...(m.timeline ?? [])];
        const existingId = textEntryIdRef.current;
        if (
          existingId &&
          tl.length > 0 &&
          tl[tl.length - 1].id === existingId &&
          tl[tl.length - 1].type === 'text'
        ) {
          // 快路径：ref 命中末尾 text entry，直接追加。
          tl[tl.length - 1] = { ...tl[tl.length - 1], content: tl[tl.length - 1].content + delta };
        } else if (textStartedRef.current && tl.length > 0 && tl[tl.length - 1].type === 'text') {
          // 同一 LLM 输出阶段（无 tool_call 介入），合并进末尾 text entry。
          const lastIdx = tl.length - 1;
          tl[lastIdx] = { ...tl[lastIdx], content: tl[lastIdx].content + delta };
          textEntryIdRef.current = tl[lastIdx].id;
        } else {
          // 新文本块（首个 delta，或 tool_call 之后）：push 新 entry。
          const newId = `${agentMsgId}-text-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
          tl.push({ id: newId, type: 'text', content: delta });
          textEntryIdRef.current = newId;
          textStartedRef.current = true;
        }
        return { ...m, timeline: tl };
      }),
    );
    // 滚动统一交给 liveMessages 的 layout effect（依据更新前 pinnedRef）。
  }, [updateLiveMessages]);

  /** 累积 text_delta 进 buffer，调度 RAF flush（一帧一次）。 */
  const appendDelta = useCallback(
    (agentMsgId: string, delta: string) => {
      const prev = deltaBufferRef.current;
      if (prev && prev.agentMsgId === agentMsgId) prev.delta += delta;
      else deltaBufferRef.current = { agentMsgId, delta };
      if (!rafIdRef.current) rafIdRef.current = requestAnimationFrame(flushDelta);
    },
    [flushDelta],
  );

  const activeSession = sessions.find((s) => s._id === activeSessionId) ?? null;
  const activeAgent =
    agents.find((a) => a.id === activeSession?.agent_id) ?? agents[0] ?? null;
  const voiceAvailable = voiceConfigured && activeAgent?.voiceEnabled === true;

  useEffect(() => {
    if (inputMode === 'voice' && !voiceAvailable) setInputMode('text');
  }, [inputMode, voiceAvailable]);

  // Auto-select first session once loaded.
  useEffect(() => {
    if (!activeSessionId && sessions.length > 0) {
      setActiveSessionId(sessions[0]._id);
    }
  }, [sessions, activeSessionId]);

  // Load messages when the active session changes.
  // NOTE: depends on `activeSessionId` only. `agents` is recomputed (new array
  // ref) on every render, so listing it as a dependency would re-fire this
  // effect mid-stream and wipe the optimistic first message. Read it via
  // agentsRef instead. Likewise guard against re-entry while a stream is live.
  // Load messages when the active session changes. NOTE: depends on `activeSessionId`
  // only — `agents` is a fresh array every render, listing it would re-fire mid-stream
  // and wipe the optimistic first message; read it via agentsRef instead. There is NO
  // streaming guard here: switching sessions mid-stream is the legitimate trigger, and
  // handleSelectSession aborts the in-flight stream first so this load takes over.
  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    const sid = activeSessionId;
    if (!sid) {
      setLiveMessages([]);
      pinnedRef.current = true;
      return;
    }
    setLiveMessages([]);
    // 会话切换/清空后回到贴底初始态：加载完成即定位到最新消息。
    pinnedRef.current = true;
    setStreamError(null);

    // Session switched away mid-stream: the backend asyncio task keeps running and
    // persists the full reply once finished. Poll getDetail until the agent reply
    // lands (messages end with role==='agent' — normal/interrupt/error turns all
    // produce one) or the timeout elapses, then render in one shot.
    const pollStartedAt = Date.now();
    const POLL_INTERVAL = 1500;
    const POLL_TIMEOUT = 90000;

    const load = () => {
      sessionApi
        .getDetail(sid)
        .then((detail) => {
          if (cancelled) return;
          const mapped: Message[] = [];
          for (const rec of detail.messages) {
            if (rec.role === 'user') mapped.push(userMessageToDisplay(rec));
            else {
              const agent = agentsRef.current.find((a) => a.id === detail.session.agent_id);
              mapped.push(
                agentMessageToDisplay(rec, agent?.name ?? 'Agent', agent?.avatar ?? ''),
              );
            }
          }
          // 会话加载完成回到贴底：最新消息就在底部。
          pinnedRef.current = true;
          setLiveMessages(mapped);
          // 检测未答的 interrupt（ask_clarification/confirm_workflow）：页面跳转后
          // SSE 中断导致 pendingInterruptRef 丢失，从历史恢复，使下次发送走 /resume。
          const pending = [...mapped].reverse().find((m) => m.isInterrupted);
          if (pending) pendingInterruptRef.current = { toolMsgId: pending.id };

          if (pendingBackgroundSessionsRef.current.has(sid)) {
            const lastIsAgent =
              detail.messages.length > 0 &&
              detail.messages[detail.messages.length - 1].role === 'agent';
            if (lastIsAgent) {
              // 后端已落库完整回答，停止轮询。
              pendingBackgroundSessionsRef.current.delete(sid);
            } else if (Date.now() - pollStartedAt < POLL_TIMEOUT) {
              pollTimer = setTimeout(load, POLL_INTERVAL);
            } else {
              // 超时仍未落库（后端异常），放弃轮询。
              pendingBackgroundSessionsRef.current.delete(sid);
            }
          }
        })
        .catch((e) => {
          if (cancelled) return;
          // 会话已被删除（404 / “不存在”）：静默回到空状态，不弹报错横幅。
          const status = (e as { response?: { status?: number } })?.response?.status;
          const msg = (e as Error).message ?? '';
          if (status === 404 || /不存在|not found/i.test(msg)) {
            setActiveSessionId(null);
            setLiveMessages([]);
            pinnedRef.current = true;
            pendingBackgroundSessionsRef.current.delete(sid);
            return;
          }
          setStreamError(`加载会话失败：${msg}`);
        });
    };
    load();

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [activeSessionId]);

  // 贴底时跟随滚动（流式输出/展开收起）。依据 pinnedRef（更新前捕获）而非现测
  // 几何距离——新内容渲染后距底必超阈值，现测会把内容增长误判为用户上翻，
  // 守卫永久脱锁导致流式输出不再跟随。瞬时跳底（paint 前）避免 smooth 动画
  // 在高频 delta 下被打断、滞后累积。
  useLayoutEffect(() => {
    if (!pinnedRef.current) return;
    const el = chatScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    else messageEndRef.current?.scrollIntoView();
  }, [liveMessages]);

  const refreshSessions = useCallback(() => {
    qc.invalidateQueries({ queryKey: sessionKeys.lists() });
  }, [qc]);

  const handleSelectSession = (id: string) => {
    setInputMode('text');
    if (isStreaming && id !== activeSessionId) {
      // 流式中切到别的会话：打断前端 SSE 渲染（后端后台任务不受影响，会继续跑完
      // 落库）。标记原会话为"后台进行中"，以便切回时轮询 getDetail 拉取落库结果。
      pendingBackgroundSessionsRef.current.add(activeSessionId);
      abortRef.current?.abort();
      setIsStreaming(false);
    }
    setActiveSessionId(id);
  };

  const handleDeleteSession = async (e: MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await sessionApi.remove(id);
      // 删除当前会话时，主动选中相邻的下一个仍存在的会话；一个都不剩则
      // 置空进入空状态引导。不依赖自动选中 effect——它可能从尚未刷新的
      // 列表缓存里挑回刚删的那个会话，触发 getDetail 404 报错。
      if (activeSessionId === id) {
        setInputMode('text');
        const idx = sessions.findIndex((s) => s._id === id);
        const remaining = sessions.filter((s) => s._id !== id);
        const next = remaining[idx] ?? remaining[idx - 1] ?? null;
        setActiveSessionId(next?._id ?? null);
      }
      refreshSessions();
    } catch (err) {
      setStreamError(`删除会话失败：${(err as Error).message}`);
    }
  };

  const handleStartNewWithAgent = async (agent: Agent) => {
    try {
      // Pass empty title — backend sets it from the first user message
      // (truncated to 30 chars + ellipsis). See session_service.add_message.
      const sess = await sessionApi.create(agent.id, '');
      setInputMode('text');
      setShowAgentSelectModal(false);
      setShowDropdown(false);
      refreshSessions();
      setActiveSessionId(sess._id);
    } catch (err) {
      setStreamError(`创建会话失败：${(err as Error).message}`);
    }
  };

  /** Pick a file into the local queue (no network upload yet — that happens
   *  at send time in handleSendMessage, so text + files dispatch together). */
  const handleUploadFile = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ''; // reset so the same file can be re-picked
    if (files.length) {
      setPendingFiles((prev) => [...prev, ...files]);
    }
  };

  /** Send a message and consume the SSE stream into liveMessages. */
  // 忽略待答的澄清/确认卡片：持久化忽略标记（后端合成 tool_result，刷新后
  // 不复活），本地关闭卡片交互态并清 pending 路由——之后用户正常发送的
  // 消息/附件走 /stream 新一轮（挂起的 interrupt 由后端新输入自然丢弃）。
  const handleDismissInterrupt = async (msgId: string) => {
    const sessionId = activeSessionId;
    const agent = activeAgent;
    if (!sessionId || !agent || isStreaming) return;
    try {
      await agentApi.dismissInterrupt(agent.id, sessionId);
    } catch (err) {
      setStreamError(`忽略失败：${(err as Error).message}`);
      return;
    }
    updateLiveMessages((prev) =>
      prev.map((m) => {
        if (m.id !== msgId) return m;
        // 把 timeline 里第一个未答的 interrupt tool entry 填上忽略标记。
        let dismissed = false;
        const tl = (m.timeline ?? []).map((e) => {
          if (
            !dismissed &&
            e.type === 'tool' &&
            (e.toolName === 'ask_clarification' || e.toolName === 'confirm_workflow') &&
            !e.result
          ) {
            dismissed = true;
            return { ...e, result: DISMISSED_CLARIFICATION_TEXT };
          }
          return e;
        });
        return { ...m, isInterrupted: false, timeline: tl };
      }),
    );
    if (pendingInterruptRef.current?.toolMsgId === msgId) {
      pendingInterruptRef.current = null;
    }
  };

  const handleSendMessage = async (e?: FormEvent, overrideText?: string) => {
    e?.preventDefault();
    const sessionId = activeSessionId;
    const agent = activeAgent;
    // Allow send with text only OR files only (matches legacy chat-panel.tsx).
    // overrideText 来自 ask_clarification 卡片的选项/内联回答（走 /resume）。
    const prompt = (overrideText ?? inputText).trim();
    if ((!prompt && pendingFiles.length === 0) || !sessionId || !agent || isStreaming) return;

    setInputText('');
    setStreamError(null);

    // ── Upload queued files BEFORE dispatching the text prompt, so text + files
    // travel together and the agent sees the file content in the same turn.
    // (frontend-studio previously uploaded at pick-time, which raced the text
    // send and left the file unattached.) Mirrors chat-panel.tsx:443-489. ──
    const uploadedFileIds: string[] = [];
    const uploadedPaths: string[] = [];
    // 捕获完整 FileRef（含 mime_type/size/name），用于立即在用户气泡内联渲染附件，
    // 不必等历史重载。source='upload' → ChatAttachmentCard 走 getFileBlob(id) 取数。
    const uploadedAttachments: ChatAttachment[] = [];
    if (pendingFiles.length > 0) {
      setUploading(true);
      try {
        for (const f of pendingFiles) {
          const res = await sessionApi.uploadFile(sessionId, f);
          if (res.workspace_path) uploadedPaths.push(res.workspace_path);
          const id = getFileId(res.file);
          if (id) {
            uploadedFileIds.push(id);
            uploadedAttachments.push({
              source: 'upload',
              ref: id,
              name: res.file.name || f.name,
              mime_type: res.file.mime_type || f.type || undefined,
              size: res.file.size || f.size || undefined,
            });
          }
        }
      } catch (err) {
        setStreamError(`文件上传失败：${(err as Error).message}`);
        setUploading(false);
        return;
      } finally {
        setUploading(false);
      }
      setPendingFiles([]);
    }

    // 仅附件无文本也继续执行（后端 ExecutionRequest 允许 input 为空但需
    // 携带 file_ids）：图片多模态场景"只贴图不打字"是核心用法。
    // userMsg.content 为空串时气泡只内联渲染附件。

    const userMsg: Message = {
      id: 'msg_user_' + Date.now(),
      senderName: '我',
      avatar: '👩‍💼',
      role: 'user',
      content: prompt,
      timestamp: '刚刚',
      // 立即内联渲染上传附件（图片直显 + 点击弹窗），不等历史重载
      attachments: uploadedAttachments.length > 0 ? uploadedAttachments : undefined,
    };

    // Placeholder agent bubble updated incrementally as deltas arrive.
    const agentMsgId = 'msg_stream_' + Date.now();
    const agentMsg: Message = {
      id: agentMsgId,
      senderName: agent.name,
      avatar: agent.avatar,
      role: 'agent',
      agentId: agent.id,
      content: '',
      timestamp: '刚刚',
      status: 'thinking',
      timeline: [],
    };
    // 重置 timeline 流式追踪 refs（让首轮 text 开新 entry）。
    textEntryIdRef.current = null;
    textStartedRef.current = false;
    deltaBufferRef.current = null;
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }

    // ── 是否走 /resume：pendingInterruptRef 已设置（SSE interrupt）或存在等待中的
    // ask_clarification/confirm_workflow（页面跳转后从历史恢复）。回答注入对应卡片
    // 作为结果，不再单独渲染用户气泡；下次走 resume 让 agent 继续而非重复提问。
    const resumeMsgId =
      pendingInterruptRef.current?.toolMsgId ??
      [...liveMessages].reverse().find((m) => m.isInterrupted)?.id;
    const isResume = !!resumeMsgId;

    updateLiveMessages((prev) => {
      if (isResume && resumeMsgId) {
        // 回答作为旧中断卡片的 timeline tool entry 的 result，关闭中断态；
        // 不渲染独立用户气泡。
        return prev
          .map((m) => {
            if (m.id !== resumeMsgId) return m;
            // 把 timeline 里第一个未答的 tool entry 填上用户回答。
            let answered = false;
            const tl = (m.timeline ?? []).map((e) => {
              if (
                !answered &&
                e.type === 'tool' &&
                (e.toolName === 'ask_clarification' || e.toolName === 'confirm_workflow') &&
                !e.result
              ) {
                answered = true;
                return { ...e, result: prompt, toolStatus: 'success' as const };
              }
              return e;
            });
            return { ...m, isInterrupted: false, timeline: tl };
          })
          .concat(agentMsg);
      }
      return [...prev, userMsg, agentMsg];
    });
    // 用户主动发消息 = 明确要看新回复：无条件恢复跟随（即使发送前在上翻），
    // 避免整个流式过程都不跟随。下方 RAF 内的 smooth 滚动同样是无条件的。
    pinnedRef.current = true;
    // 用户主动发消息：无条件滚到底部跟随新回复（贴底守卫仅约束流式/展开场景）。
    requestAnimationFrame(() => {
      messageEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    });
    pendingInterruptRef.current = null;
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = isResume
        ? await agentApi.resume(agent.id, {
            session_id: sessionId,
            // resume 的 answer 不允许为空(ResumeRequest min_length=1):
            // 澄清回答时只贴图不打字 → 占位让请求可过,agent 从上下文理解。
            answer: prompt || '(用户上传了附件)',
            enable_thinking: enableThinking,
          }, controller.signal)
        : await agentApi.stream(agent.id, {
            input: prompt,
            session_id: sessionId,
            enable_thinking: enableThinking,
            // file_ids: persists the reference on the user message (history).
            // file_paths: backend embeds file contents into the LLM user message
            // (agents.py:589-610) — without this the agent never sees the content.
            file_ids: uploadedFileIds.length > 0 ? uploadedFileIds : undefined,
            file_paths: uploadedPaths.length > 0 ? uploadedPaths : undefined,
          }, controller.signal);
      // Attachments already handed off to the execution request above
      // (pendingFiles cleared earlier in this function).
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = body?.error?.message ?? body?.message ?? detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }

      // 累积流式过程中 agent 产出的 output 文件路径，挂到 agent 消息上内联预览。
      const streamedAttachments: ChatAttachment[] = [];
      // 当前 LLM 轮的 thinking 累积与 entry 定位。轮次边界是 thinking 完整事件
      // （on_chat_model_end）——注意 tool_call_start 在流式期间先于它到达，不能
      // 作为边界，否则 final 会在 tool entry 之后另起新卡片，把本轮思考重复一遍。
      // 同轮交错思考块（thinking→text→thinking）归并进同一个 entry，与后端
      // 持久化结构（final 合并全部块）一致。
      let thinkingText = '';
      let thinkingEntryId: string | null = null;

      for await (const evt of parseSSEStream(res)) {
        if (controller.signal.aborted) break;
        // StreamDoneEvent has no `type` field (it carries `done: true`); every
        // other event has one. Handle the done-event first so the switch below
        // narrows the discriminated union cleanly on `evt.type`.
        if (!('type' in evt)) {
          // Stream finished — refresh sessions so persisted messages/timeline load,
          // and reload generated files so any new outputs appear immediately.
          // 无预建会话直接开聊时，后端在首个请求时创建了会话——此处绑定
          // session_id，否则 activeSession 一直为空，空状态提示会压在消息上方。
          if (!activeSessionId && evt.session_id) {
            setActiveSessionId(evt.session_id);
          }
          refreshSessions();
          filesPanelRef.current?.refresh();
          setFeedbackTick((t) => t + 1);  // 本轮可能 load 了技能——刷新反馈态
          // 收敛残留 pending/running 的 tool entry 为 success（跳过 ask_clarification/
          // confirm_workflow —— 它们 interrupt 等待用户），并挂 usage。
          updateLiveMessages((prev) =>
            prev.map((m) => {
              if (m.id !== agentMsgId) return m;
              const tl = (m.timeline ?? []).map((e) =>
                e.type === 'tool' &&
                (e.toolStatus === 'pending' || e.toolStatus === 'running') &&
                e.toolName !== 'ask_clarification' &&
                e.toolName !== 'confirm_workflow' &&
                e.toolName !== ''
                  ? { ...e, toolStatus: 'success' as const }
                  : e,
              );
              return {
                ...m, status: undefined, timeline: tl,
                usage: evt.usage ?? m.usage,
                requestId: evt.request_id || m.requestId,  // §8.2 消息级反馈轮次键
              };
            }),
          );
          continue;
        }
        switch (evt.type) {
          case 'thinking':
          case 'thinking_delta': {
            // thinking = on_chat_model_end 的权威全文 → 覆盖；thinking_delta = 增量 → 追加。
            // 按 thinkingEntryId 定位本轮 entry（可能不在末尾——text 已插入），
            // final 到达即本轮结束：写入全文、收起、清空累积。
            const isFinal = evt.type === 'thinking';
            thinkingText = isFinal ? evt.content : thinkingText + evt.content;
            const text = thinkingText;
            // id 在 setState 外生成（updater 需保持纯函数，StrictMode 下可能双调）。
            const pushId = thinkingEntryId
              ?? `${agentMsgId}-think-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            thinkingEntryId = pushId;
            if (isFinal) {
              thinkingText = '';
              thinkingEntryId = null;
            }
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                const idx = tl.findIndex((e) => e.id === pushId);
                if (idx >= 0) {
                  tl[idx] = {
                    ...tl[idx],
                    content: text,
                    expanded: isFinal && !tl[idx].userToggled ? false : tl[idx].expanded,
                  };
                } else {
                  tl.push({ id: pushId, type: 'thinking', content: text, expanded: !isFinal });
                }
                // final = 本轮思考结束（on_chat_model_end）。注意事件顺序：final 常
                // 晚于 tool_call_start 到达（后者已把 status 清为 undefined），此处
                // 若无条件设回 'thinking'，工具执行期间（tool_call/tool_result 均不
                // 动 status）气泡会一直错误显示"思考中"，直到下一个 text_delta。
                return { ...m, status: isFinal ? undefined : 'thinking', timeline: tl };
              }),
            );
            break;
          }
          case 'tool_call_start': {
            // 先同步 flush 文本 buffer（让 text 出现在 tool 之前），再 push 一个
            // pending tool entry，并重置 text refs（让 tool 之后的新 text 开新 entry）。
            // 注意：此处不重置 thinking 累积——tool_call_start 在流式期间先于
            // on_chat_model_end 到达，thinking 的轮次边界是 final 事件本身。
            if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
            const buf = deltaBufferRef.current;
            deltaBufferRef.current = null;
            rafIdRef.current = null;
            textEntryIdRef.current = null;
            textStartedRef.current = false;
            // tool_call_start 携带首个 chunk 里已流出的工具名（多数模型此刻
            // 已有名字；个别模型 name 在后续 chunk 才到则为空，此时先显示
            // "调用工具…"占位），完整名字/args 由后续 tool_call 事件补全。
            const toolName = evt.tool_name || '';
            const toolEntryId = `${agentMsgId}-tool-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                if (buf) tl.push({ id: `${agentMsgId}-text-${Date.now()}`, type: 'text', content: buf.delta });
                tl.push({ id: toolEntryId, type: 'tool', content: '', toolName, toolStatus: 'pending' });
                return { ...m, status: undefined, timeline: tl };
              }),
            );
            break;
          }
          case 'tool_call': {
            // 升级 pending tool entry 为 running（填入完整 args + toolCallId），
            // 没有就新建。toolCallId 用于把后续 tool_result 精确配对到本调用
            //（处理并行同名调用，如两次 kb_search）。
            // tool_call 完整事件由后端 on_chat_model_end 在 final thinking 之后
            // 发出，到达即本轮 LLM 已结束；若该轮 final thinking 缺失（reasoning
            // 提取为空时不发），在此兜底重置累积，避免下一轮思考拼进同一张卡片。
            thinkingText = '';
            thinkingEntryId = null;
            if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
            deltaBufferRef.current = null;
            rafIdRef.current = null;
            textEntryIdRef.current = null;
            textStartedRef.current = false;
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                const targetIdx = tl.findIndex(
                  (e) => e.type === 'tool' && e.toolStatus === 'pending',
                );
                if (targetIdx >= 0) {
                  tl[targetIdx] = {
                    ...tl[targetIdx],
                    toolName: evt.tool_name,
                    toolCallId: evt.id,
                    args: evt.args,
                    toolStatus: 'running',
                  };
                } else {
                  tl.push({
                    id: `${agentMsgId}-tool-${Date.now()}`,
                    type: 'tool',
                    content: '',
                    toolName: evt.tool_name,
                    toolCallId: evt.id,
                    args: evt.args,
                    toolStatus: 'running',
                  });
                }
                return { ...m, timeline: tl };
              }),
            );
            break;
          }
          case 'tool_result': {
            const resultContent = evt.content;
            const isError = evt.status === 'error';
            const tcid = evt.tool_call_id;
            // 优先按 tool_call_id 精确匹配（处理并行同名调用）；无 id 的旧数据
            // 回退到 toolName + running 匹配。
            const matchesEntry = (e: TimelineEntry): boolean =>
              e.type === 'tool' &&
              e.toolStatus === 'running' &&
              (tcid ? e.toolCallId === tcid : e.toolName === evt.tool_name);
            // 解析工具结果里产出的 output 文件，累积到 agent 消息（去重）。
            const newAtts = parseOutputAttachments(resultContent);
            if (newAtts.length > 0) {
              for (const a of newAtts) {
                if (!streamedAttachments.some((b) => b.ref === a.ref)) {
                  streamedAttachments.push(a);
                }
              }
            }
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                // 从后往前找匹配的 running tool entry，填入 result + 状态。
                for (let i = tl.length - 1; i >= 0; i--) {
                  if (matchesEntry(tl[i])) {
                    tl[i] = {
                      ...tl[i],
                      result: resultContent,
                      toolStatus: isError ? 'error' : 'success',
                    };
                    break;
                  }
                }
                return {
                  ...m,
                  timeline: tl,
                  attachments: streamedAttachments.length > 0 ? [...streamedAttachments] : m.attachments,
                };
              }),
            );
            break;
          }
          case 'text_delta':
            // 走 RAF 批处理（appendDelta/flushDelta），平滑且多轮顺序正确。
            appendDelta(agentMsgId, evt.content);
            updateLiveMessages((prev) =>
              prev.map((m) => (m.id === agentMsgId ? { ...m, status: undefined } : m)),
            );
            break;
          case 'text': {
            // 权威全文：覆盖最后一个 text entry，没有就 push。
            const content = evt.content;
            if (content) {
              if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
              deltaBufferRef.current = null;
              rafIdRef.current = null;
              updateLiveMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== agentMsgId) return m;
                  const tl = [...(m.timeline ?? [])];
                  let found = false;
                  for (let i = tl.length - 1; i >= 0; i--) {
                    if (tl[i].type === 'text') {
                      tl[i] = { ...tl[i], content };
                      found = true;
                      break;
                    }
                  }
                  if (!found) tl.push({ id: `${agentMsgId}-text-${Date.now()}`, type: 'text', content });
                  return { ...m, status: undefined, timeline: tl };
                }),
              );
            }
            break;
          }
          case 'error': {
            // 不 throw 中断流：把错误记录到 agent 消息 timeline 的 error entry。
            // 错误可能来自中途（如某个工具的加载失败），后续仍可能有事件。
            const errContent = evt.content || '执行出错';
            setStreamError(errContent);
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                tl.push({ id: `${agentMsgId}-err-${Date.now()}`, type: 'error', content: `❌ ${errContent}` });
                return { ...m, status: 'error', timeline: tl };
              }),
            );
            break;
          }
          case 'interrupt': {
            // Agent 经 ask_clarification/confirm_workflow 暂停等待用户回答：
            // 标记该消息为中断态（卡片可交互），记录消息 id 使下次发送走 /resume。
            // 后端在 interrupt 事件里把 question/options/fields/context 作为顶级
            // 字段下发（权威来源），这里回填到对应 ask_clarification tool entry 的
            // args——即使 tool_call 的 args 缺失/延迟，卡片也能正常渲染。
            updateLiveMessages((prev) =>
              prev.map((m) => {
                if (m.id !== agentMsgId) return m;
                const tl = [...(m.timeline ?? [])];
                for (let i = tl.length - 1; i >= 0; i--) {
                  if (
                    tl[i].type === 'tool' &&
                    !tl[i].result &&
                    (tl[i].toolName === 'ask_clarification' || tl[i].toolName === 'confirm_workflow')
                  ) {
                    if (tl[i].toolName === 'ask_clarification') {
                      // 把后端权威字段合并进 args（不覆盖已有值）。
                      const args = { ...tl[i].args };
                      if (!args.question && evt.question) args.question = evt.question;
                      if (!args.clarification_type && evt.clarification_type)
                        args.clarification_type = evt.clarification_type;
                      if (args.context == null && evt.context != null) args.context = evt.context;
                      if (args.options == null && evt.options != null) args.options = evt.options;
                      if (args.fields == null && evt.fields != null) args.fields = evt.fields;
                      tl[i] = { ...tl[i], args, toolStatus: 'success' };
                    } else {
                      tl[i] = { ...tl[i], toolStatus: 'success' };
                    }
                    break;
                  }
                }
                return { ...m, isInterrupted: true, timeline: tl };
              }),
            );
            pendingInterruptRef.current = { toolMsgId: agentMsgId };
            break;
          }
          default:
            break;
        }
      }
      // 兜底 flush 残留 delta。
      if (rafIdRef.current) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      if (deltaBufferRef.current) flushDelta();
    } catch (err) {
      // 用户主动中止（切换会话 / 点停止生成）时 controller 已 abort：静默退出，
      // 不弹"流式请求失败"横幅、不写 error entry。仅对真实异常报错。
      if (!controller.signal.aborted) {
        const msg = (err as Error).message || '流式请求失败';
        setStreamError(msg);
        updateLiveMessages((prev) =>
          prev.map((m) => {
            if (m.id !== agentMsgId) return m;
            const tl = [...(m.timeline ?? [])];
            tl.push({ id: `${agentMsgId}-err-${Date.now()}`, type: 'error', content: `❌ ${msg}` });
            return { ...m, status: 'error', timeline: tl };
          }),
        );
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
      refreshSessions();
    }
  };

  const handleStop = () => {
    // 先通知服务端停止生成（mid-stream abort：立即打断 LLM token 流/工具执行，
    // 半截回复不落库，可直接开始新对话），再断本地 SSE 连接（fetch signal）。
    // 服务端调用 fire-and-forget——409（已结束）等失败不影响本地停止。
    // 注意：会话切换的 abort（handleSelectSession）不通知服务端，后台跑完
    // 落库、切回可见是刻意语义。
    if (activeAgent) void agentApi.stop(activeAgent.id);
    abortRef.current?.abort();
    setIsStreaming(false);
  };

  const handleVoiceTranscriptFinal = (text: string) => {
    const transcript = text.trim();
    if (!transcript) return;
    // 语音输入 = 发送语义：无条件恢复跟随（与文本发送一致）。
    pinnedRef.current = true;
    updateLiveMessages((prev) => [
      ...prev,
      {
        id: `msg_voice_user_${Date.now()}`,
        senderName: '我',
        avatar: '',
        role: 'user',
        content: transcript,
        timestamp: '刚刚',
      },
    ]);
  };

  const handleVoiceTurnStarted = (message: { request_id?: string }) => {
    const agent = activeAgent;
    if (!agent) return;
    const agentMsgId = `msg_voice_agent_${message.request_id || Date.now()}`;
    voiceAgentMsgIdRef.current = agentMsgId;
    textEntryIdRef.current = null;
    textStartedRef.current = false;
    deltaBufferRef.current = null;
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    // 新一轮语音回复开始生成：恢复跟随。
    pinnedRef.current = true;
    updateLiveMessages((prev) => [
      ...prev,
      {
        id: agentMsgId,
        senderName: agent.name,
        avatar: agent.avatar,
        role: 'agent',
        agentId: agent.id,
        content: '',
        timestamp: '刚刚',
        status: 'thinking',
        timeline: [],
      },
    ]);
  };

  const handleVoiceAgentDelta = (delta: string) => {
    const agentMsgId = voiceAgentMsgIdRef.current;
    if (agentMsgId && delta) appendDelta(agentMsgId, delta);
  };

  const handleVoiceTurnEnd = (message: { session_id?: string }) => {
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    if (deltaBufferRef.current) flushDelta();

    const agentMsgId = voiceAgentMsgIdRef.current;
    if (agentMsgId) {
      updateLiveMessages((prev) =>
        prev.map((item) =>
          item.id === agentMsgId ? { ...item, status: undefined } : item,
        ),
      );
    }
    voiceAgentMsgIdRef.current = null;
    refreshSessions();
    filesPanelRef.current?.refresh();

    // Voice persistence is complete before turn.end. Reload the same session so
    // Markdown, tools, attachments and clarification cards use the established
    // chat renderer instead of a separate voice-only transcript view.
    const sessionId = message.session_id || activeSessionId;
    if (!sessionId || sessionId !== activeSessionId) return;
    void sessionApi.getDetail(sessionId).then((detail) => {
      const mapped: Message[] = [];
      for (const record of detail.messages) {
        if (record.role === 'user') mapped.push(userMessageToDisplay(record));
        else {
          const agent = agentsRef.current.find((item) => item.id === detail.session.agent_id);
          mapped.push(
            agentMessageToDisplay(record, agent?.name ?? 'Agent', agent?.avatar ?? ''),
          );
        }
      }
      // 重载完成回到贴底：最新消息就在底部。
      pinnedRef.current = true;
      setLiveMessages(mapped);
      const pending = [...mapped].reverse().find((item) => item.isInterrupted);
      pendingInterruptRef.current = pending ? { toolMsgId: pending.id } : null;
    }).catch((error: Error) => {
      setStreamError(`刷新语音对话失败：${error.message}`);
    });
  };

  const handleVoiceInterruptRequest = (question: string) => {
    // The voice protocol carries clarification separately from text deltas;
    // append it optimistically, then turn.end history refresh restores the card.
    handleVoiceAgentDelta(question);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-0 bg-[#121214] border-0 overflow-hidden relative w-full h-full min-h-0 flex-1">
      {/* 1. SIDEBAR: Conversations List — collapsed narrow rail | expanded list */}
      {sidebarCollapsed ? (
        <div className="lg:col-span-1 min-h-0 border-r border-[#27272a] bg-[#18181b]/80 flex flex-row lg:flex-col items-center gap-3 px-2 py-3">
          <button
            onClick={() => setSidebarCollapsed(false)}
            title="展开会话列表"
            className="w-9 h-9 rounded-full bg-[#27272a]/50 border border-[#27272a] flex items-center justify-center text-[#a1a1aa] hover:text-white hover:bg-[#1f1f23] transition-colors cursor-pointer"
          >
            <PanelLeftOpen className="w-4 h-4" />
          </button>
          <button
            onClick={() => {
              // 固定 agent 模式：+ 直通新建；多 agent 场景先展开侧栏再选择。
              if (fixedAgentId) {
                const agent = agents.find((a) => a.id === fixedAgentId);
                if (agent) void handleStartNewWithAgent(agent);
                return;
              }
              setSidebarCollapsed(false);
            }}
            title="新建会话"
            className="w-9 h-9 rounded-full bg-[#27272a]/50 border border-[#27272a] flex items-center justify-center hover:bg-[#1f1f23] transition-colors cursor-pointer"
          >
            <Plus className="w-4 h-4 text-emerald-400" />
          </button>
          <span
            className="mt-auto hidden lg:block w-2 h-2 rounded-full bg-emerald-500 animate-pulse"
            title="MEPER Agent SSE: Connected"
          />
        </div>
      ) : (
      <div className="lg:col-span-3 min-h-0 border-r border-[#27272a] bg-[#18181b]/80 flex flex-col justify-between">
        <div className="flex flex-col h-full min-h-0">
          <div className="px-4 h-16 border-b border-[#27272a] flex items-center justify-between relative">
            <div
              className="flex items-center gap-1.5 cursor-pointer group"
              onClick={() => setSidebarCollapsed(true)}
              title="收起会话列表"
            >
              <PanelLeftClose className="w-4 h-4 text-[#a1a1aa] group-hover:text-white transition-colors" />
              <span className="text-sm font-bold text-white font-sans">对话</span>
            </div>
            <div className="relative">
              <button
                onClick={() => {
                  // 固定 agent 模式：+ 直通以该 agent 新建（原下拉与选择弹窗仅服务多 agent 场景）。
                  if (fixedAgentId) {
                    const agent = agents.find((a) => a.id === fixedAgentId);
                    if (agent) void handleStartNewWithAgent(agent);
                    return;
                  }
                  setShowDropdown(!showDropdown);
                }}
                className="w-9 h-9 rounded-full bg-[#27272a]/50 border border-[#27272a] flex items-center justify-center text-white hover:bg-[#1f1f23] transition-colors cursor-pointer"
              >
                <Plus className="w-4 h-4 text-emerald-400" />
              </button>
              {showDropdown && (
                <div className="absolute right-0 mt-2 w-56 bg-[#18181b] border border-[#27272a] rounded-xl shadow-2xl p-1 z-50 animate-fade-in text-xs font-sans">
                  <div className="px-3 py-2 text-[10px] text-[#71717a] border-b border-[#27272a] font-semibold uppercase tracking-wider">
                    新建工作专区
                  </div>
                  <button
                    onClick={() => {
                      setShowAgentSelectModal(true);
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-[#121214] text-[#fafafa] flex flex-col gap-0.5 transition cursor-pointer"
                  >
                    <span className="font-semibold flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                      新建项目
                    </span>
                    <span className="text-[10px] text-[#71717a]">选择协作 Agent 启动全新会话</span>
                  </button>
                  <button
                    onClick={() => {
                      setShowAgentSelectModal(true);
                      setShowDropdown(false);
                    }}
                    className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-[#121214] text-[#fafafa] flex flex-col gap-0.5 transition border-t border-[#27272a] pt-2 cursor-pointer font-sans"
                  >
                    <span className="font-semibold text-emerald-400 flex items-center gap-1.5">
                      <Bot className="w-3.5 h-3.5 text-emerald-400 font-bold" />
                      选择 Agent 开始
                    </span>
                    <span className="text-[10px] text-[#71717a]">从真实后端创建会话</span>
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto scrollbar-custom p-2 space-y-1">
            {sessionsLoading && (
              <div className="p-3 text-[10px] text-[#71717a] font-mono">加载会话中…</div>
            )}
            {!sessionsLoading && sessions.length === 0 && (
              <div className="p-3 text-[10px] text-[#71717a] font-sans">
                {fixedAgentId ? '暂无会话。点击右上 + 新建。' : '暂无会话。点击右上 + 选择 Agent 新建。'}
              </div>
            )}
            {sessions.map((sess) => {
              const infoAgent = agents.find((a) => a.id === sess.agent_id);
              const isActive = activeSessionId === sess._id;
              return (
                <div
                  key={sess._id}
                  onClick={() => handleSelectSession(sess._id)}
                  className={`p-3 rounded-lg flex items-start gap-3 transition-all relative group cursor-pointer select-none ${
                    isActive
                      ? theme === 'light'
                        ? 'bg-slate-200'
                        : 'bg-[#121214] border border-[#27272a]/70 shadow-lg'
                      : theme === 'light'
                        ? 'hover:bg-slate-100'
                        : 'hover:bg-[#121214]/50 border border-transparent'
                  }`}
                >
                  <div className="relative">
                    <div className="w-10 h-10 rounded-xl bg-[#121214] border border-[#27272a] flex items-center justify-center text-xl shadow-inner select-none overflow-hidden">
                      <BotAvatar avatar={infoAgent?.avatar} className="w-full h-full p-1 text-xl" />
                    </div>
                    <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-[#18181b]" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between items-center mb-0.5">
                      <span className="text-xs font-bold text-white truncate font-sans">
                        {sess.title || infoAgent?.name || '未命名会话'}
                      </span>
                      <span className="text-[10px] text-[#71717a] font-mono leading-none shrink-0">
                        {formatSessionTime(sess.updated_at)}
                      </span>
                    </div>
                    <p className="text-[11px] text-[#a1a1aa] truncate font-sans leading-relaxed">
                      {sess.message_count ?? 0} 条消息 · {infoAgent?.name ?? 'Agent'}
                    </p>
                  </div>
                  <button
                    onClick={(e) => handleDeleteSession(e, sess._id)}
                    className="absolute right-2 bottom-3 opacity-0 group-hover:opacity-100 p-1 text-slate-500 hover:text-rose-400 hover:bg-[#18181b] rounded-md transition"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <div className="p-3.5 border-t border-[#27272a] bg-[#121214] rounded-none">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[10px] text-[#71717a] font-mono">MEPER Agent SSE: Connected</span>
          </div>
        </div>
      </div>
      )}

      {/* 2. CHAT STREAM PANEL */}
      <div className={`${sidebarCollapsed ? 'lg:col-span-11' : 'lg:col-span-9'} min-h-0 flex flex-col h-full bg-[#121214]`}>
        <div className="px-6 h-16 border-b border-[#27272a] bg-[#18181b]/50 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[#121214] border border-[#27272a] text-xl flex items-center justify-center overflow-hidden">
              <BotAvatar avatar={activeAgent?.avatar} className="w-full h-full p-1 text-xl" />
            </div>
            <div>
              <h3 className="text-xs font-bold text-white flex items-center gap-1.5 font-sans">
                {activeSession?.title || activeAgent?.name || '选择一个会话'}
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 text-[10px] font-mono">
                  SSE 流式
                </span>
              </h3>
              <p className="text-[11px] text-[#71717a] font-sans">
                {modelLabel(activeAgent?.model)} • {activeAgent?.name ?? '—'}
              </p>
            </div>
          </div>
          {/* Chat / Files tab switcher */}
          <div className="flex items-center gap-1 p-0.5 rounded-lg bg-[#121214] border border-[#27272a]">
            {(['chat', 'files'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setRightTab(tab)}
                className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition cursor-pointer ${
                  rightTab === tab ? 'bg-[#1E5EFF] text-white' : 'text-[#71717a] hover:text-white'
                }`}
              >
                {tab === 'chat' ? '对话' : '文件'}
              </button>
            ))}
          </div>
        </div>

        {streamError && (
          <div className="px-6 py-2 bg-rose-950/30 border-b border-rose-900/40 text-rose-400 text-[11px] font-sans">
            ⚠ {streamError}
          </div>
        )}

        {rightTab === 'files' ? (
          <SessionFilesPanel ref={filesPanelRef} sessionId={activeSessionId} />
        ) : (
        <div ref={chatScrollRef} className="flex-1 min-h-0 overflow-y-auto scrollbar-custom p-6 space-y-5">
          <div className="flex items-center justify-center">
            <span className="px-3 py-1 rounded bg-[#18181b] border border-[#27272a]/60 text-[#71717a] text-[10px] font-mono">
              对话由 MEPER Agent 引擎实时流式生成
            </span>
          </div>

          {/* 空状态仅在没有任何消息时显示（流式/会话绑定延迟期间不压在消息上方） */}
          {!activeSession && !sessionsLoading && liveMessages.length === 0 && (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <Bot className="w-10 h-10 text-[#71717a] mb-3" />
              <p className="text-sm text-[#a1a1aa] font-sans">还没有会话</p>
              <p className="text-[11px] text-[#71717a] mt-1">
                {fixedAgentId ? '点击左上 + 立即开始对话' : '点击左上 + 选择一个 Agent 开始对话'}
              </p>
            </div>
          )}

          {liveMessages.map((msg) => {
            const isUser = msg.role === 'user';
            const isAgent = msg.role === 'agent';
            // agent 消息靠 timeline 渲染（text/tool/thinking/error 按顺序）；
            // user 消息保持单个气泡。
            const hasTimeline = isAgent && !!msg.timeline && msg.timeline.length > 0;
            return (
              <div
                key={msg.id}
                className={`flex items-start gap-4 max-w-4xl animate-fade-in ${isUser ? 'ml-auto flex-row-reverse' : ''}`}
              >
                <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 select-none shadow ${
                  isUser
                    ? 'bg-[#4f46e5]/15 border border-[#4f46e5]/30 text-indigo-400'
                    : 'bg-[#18181b] border border-[#27272a] overflow-hidden'
                }`}>
                  {isUser ? (
                    <User className="w-4 h-4" />
                  ) : (
                    <BotAvatar avatar={msg.avatar} className="w-full h-full p-1.5 text-lg" />
                  )}
                </div>
                <div className="space-y-1 min-w-0 flex-1">
                  <div className={`flex items-center gap-2 text-[10px] ${isUser ? 'justify-end' : ''}`}>
                    {!isUser && (
                      <span className="font-bold text-[#a1a1aa] font-sans">
                        {msg.senderName}
                        {msg.status === 'thinking' && <span className="text-indigo-400 ml-1">· 思考中</span>}
                      </span>
                    )}
                    <span className="text-[#71717a] font-mono">{msg.timestamp}</span>
                    {!isUser && msg.usage?.total_tokens ? (
                      <span className="text-[#71717a] font-mono">
                        · {msg.usage.total_tokens.toLocaleString()} tokens
                        {msg.usage.llm_calls != null && msg.usage.llm_calls > 1
                          ? ` · ${msg.usage.llm_calls} 轮`
                          : ''}
                      </span>
                    ) : null}
                  </div>
                  {hasTimeline ? (
                    <div className="space-y-2 max-w-2xl">
                      {msg.timeline!.map((entry, idx) => {
                        const isLast = idx === msg.timeline!.length - 1;
                        if (entry.type === 'text') {
                          return (
                            <div
                              key={entry.id}
                              className="p-4 rounded-xl rounded-tl-none text-[12.5px] leading-relaxed border font-sans select-text shadow-sm bg-[#18181b] border-[#27272a] text-[#e4e4e7]"
                            >
                              <Markdown content={entry.content} />
                              {/* agent 消息的产出附件挂在最后一个 text entry 后面 */}
                              {isLast && msg.attachments && msg.attachments.length > 0 && (
                                <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-current/10">
                                  {msg.attachments.map((att, i) => (
                                    <ChatAttachmentCard
                                      key={`${msg.id}-att-${i}-${att.ref}`}
                                      att={att}
                                      sessionId={activeSessionId}
                                    />
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        }
                        if (entry.type === 'thinking') {
                          // 流式判定：消息 thinking 中，且该 entry 是末尾条目或
                          // 最后一个 thinking entry（交错思考块更新时不一定在末尾）。
                          const isLastThinking =
                            isLast || !msg.timeline!.slice(idx + 1).some((e) => e.type === 'thinking');
                          return (
                            <ThinkingEntryCard
                              key={entry.id}
                              entry={entry}
                              streaming={msg.status === 'thinking' && isLastThinking}
                              onToggle={() =>
                                updateLiveMessages((prev) =>
                                  prev.map((m) =>
                                    m.id === msg.id
                                      ? {
                                          ...m,
                                          timeline: (m.timeline ?? []).map((e) =>
                                            e.id === entry.id
                                              ? { ...e, expanded: !e.expanded, userToggled: true }
                                              : e,
                                          ),
                                        }
                                      : m,
                                  ),
                                )
                              }
                            />
                          );
                        }
                        if (entry.type === 'error') {
                          return (
                            <div
                              key={entry.id}
                              className="p-4 rounded-xl rounded-tl-none text-[12.5px] leading-relaxed border font-sans shadow-sm bg-rose-950/20 border-rose-900/40 rounded-tl-none text-rose-300"
                            >
                              {entry.content}
                            </div>
                          );
                        }
                        // tool entry
                        return (
                          <ToolEntryCard
                            key={entry.id}
                            entry={entry}
                            msgId={msg.id}
                            interrupted={!!msg.isInterrupted}
                            onAnswer={(a) => handleSendMessage(undefined, a)}
                            onDismiss={() => handleDismissInterrupt(msg.id)}
                          />
                        );
                      })}
                    </div>
                  ) : (
                  <div
                    className={`p-4 rounded-xl text-[12.5px] leading-relaxed border font-sans select-text shadow-sm ${
                      msg.status === 'thinking'
                        ? 'bg-indigo-950/20 border-indigo-900/40 rounded-tl-none text-indigo-300 italic'
                        : isUser
                          ? 'bg-[#4f46e5]/10 border-[#4f46e5]/30 rounded-tr-none text-[#fafafa]'
                          : 'bg-[#18181b] border-[#27272a] rounded-tl-none text-[#e4e4e7]'
                    }`}
                  >
                    {msg.content
                      ? <Markdown content={msg.content} />
                      : (msg.status === 'thinking' ? '…' : '')}
                    {/* 可预览附件挂在气泡内部。有正文时隔开 + 分隔线；
                        仅附件（无文字）时直接贴边，不留空隙不画线。 */}
                    {msg.attachments && msg.attachments.length > 0 && (
                      <div className={`flex flex-wrap gap-2 ${msg.content ? 'mt-3 pt-3 border-t border-current/10' : ''} ${isUser ? 'justify-end' : ''}`}>
                        {msg.attachments.map((att, i) => (
                          <ChatAttachmentCard
                            key={`${msg.id}-att-${i}-${att.ref}`}
                            att={att}
                            sessionId={activeSessionId}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                  )}
                  {/* 回答下方：消息级反馈（§8.2 v2）——赞回复→本轮技能派生加分 */}
                  {!isUser && !isStreaming && msg.requestId && (
                    <div className="flex items-center gap-0.5 pt-1">
                      <button
                        aria-label="msg-vote-up"
                        title={feedbackMap[msg.requestId]?.value === 1 ? '已点过赞' : '这轮回复有帮助'}
                        onClick={() => handleVoteMessage(msg.requestId!, 1)}
                        className={`border-0 bg-transparent rounded-md w-7 h-7 flex items-center justify-center cursor-pointer transition ${
                          feedbackMap[msg.requestId]?.value === 1 ? 'text-blue-400' : 'text-[#71717a] hover:text-blue-400'
                        }`}
                      >
                        <ThumbsUp size={15} />
                      </button>
                      <button
                        aria-label="msg-vote-down"
                        title={feedbackMap[msg.requestId]?.value === -1 ? '已点过踩' : '这轮回复没帮助'}
                        onClick={() => handleVoteMessage(msg.requestId!, -1)}
                        className={`border-0 bg-transparent rounded-md w-7 h-7 flex items-center justify-center cursor-pointer transition ${
                          feedbackMap[msg.requestId]?.value === -1 ? 'text-rose-400' : 'text-[#71717a] hover:text-rose-400'
                        }`}
                      >
                        <ThumbsDown size={15} />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          <div ref={messageEndRef} />
        </div>
        )}

        {/* Input box (chat tab only) */}
        {rightTab === 'chat' && (
        <div className="p-4 border-t border-[#27272a]">
          {inputMode === 'voice' && voiceAvailable ? (
            <ChatVoiceComposer
              agentId={activeAgent?.id}
              sessionId={activeSessionId ?? undefined}
              theme={theme}
              onExit={() => setInputMode('text')}
              onTranscriptFinal={handleVoiceTranscriptFinal}
              onTurnStarted={handleVoiceTurnStarted}
              onAgentDelta={handleVoiceAgentDelta}
              onTurnEnd={handleVoiceTurnEnd}
              onInterruptRequest={handleVoiceInterruptRequest}
              onError={setStreamError}
            />
          ) : (
          <form onSubmit={handleSendMessage} className="relative bg-[#18181b] border border-[#27272a] rounded-xl p-3 flex flex-col justify-between">
            {pendingFiles.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {pendingFiles.map((f, i) => (
                  <span key={`${f.name}-${i}`} className="flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-[10px] text-emerald-300 font-sans">
                    <FileCode className="w-3 h-3" />
                    <span className="max-w-[140px] truncate">{f.name}</span>
                    <button
                      type="button"
                      onClick={() => setPendingFiles((prev) => prev.filter((_, j) => j !== i))}
                      className="hover:text-white cursor-pointer"
                    >
                      <X className="w-2.5 h-2.5" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              rows={2}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={
                activeSession
                  ? '跟我说说你的偏好和要求，我会更懂你（Enter 发送，Shift+Enter 换行）'
                  : '请先在左侧选择或新建一个会话'
              }
              disabled={!activeSession || isStreaming}
              className="w-full bg-transparent text-xs text-white focus:outline-none resize-none font-sans placeholder-[#71717a] leading-relaxed disabled:opacity-50"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
            />
            <div className="flex items-center justify-between border-t border-[#27272a]/50 pt-2.5 mt-2">
              <div className="flex items-center gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={handleUploadFile}
                  disabled={!activeSessionId || uploading || isStreaming}
                />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={!activeSessionId || uploading || isStreaming}
                  className="w-6 h-6 rounded-full bg-[#121214] border border-[#27272a] flex items-center justify-center text-white hover:bg-[#27272a] hover:text-emerald-400 transition disabled:opacity-40"
                  title="上传文件附件"
                >
                  {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Paperclip className="w-3.5 h-3.5" />}
                </button>
                {/* Thinking-mode toggle */}
                <button
                  type="button"
                  onClick={() => setEnableThinking((v) => !v)}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold transition cursor-pointer select-none ${
                    enableThinking
                      ? 'bg-violet-500/15 border-violet-500/40 text-violet-300'
                      : 'bg-[#121214] border-[#27272a] text-[#71717a] hover:text-white'
                  }`}
                  title="思考模式（enable_thinking）— 流式返回思考过程"
                >
                  <Brain className="w-3 h-3" />
                  {enableThinking ? '思考' : '直答'}
                </button>
                <span className="text-[10px] text-[#71717a] font-sans flex items-center gap-1 select-none">
                  <CheckCircle className="w-3 h-3 text-indigo-400" />
                  {isStreaming ? '正在接收流…' : uploading ? '上传中…' : '就绪'}
                </span>
              </div>
              <div className="flex items-center gap-3">
                {voiceAvailable && (
                  <button
                    type="button"
                    onClick={() => setInputMode('voice')}
                    disabled={!activeSession || isStreaming || uploading}
                    className="w-7 h-7 rounded-full flex items-center justify-center text-[#a1a1aa] hover:bg-sky-500/10 hover:text-sky-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer transition-colors duration-200"
                    title="切换到语音对话"
                    aria-label="切换到语音对话"
                  >
                    <Mic className="w-3.5 h-3.5" />
                  </button>
                )}
                <div className="flex items-center gap-1 bg-[#121214] border border-[#27272a] rounded px-2 py-0.5 text-[10px] font-mono text-[#a1a1aa] select-none">
                  <span>{modelLabel(activeAgent?.model)}</span>
                </div>
                {isStreaming ? (
                  <button
                    type="button"
                    onClick={handleStop}
                    className="w-7 h-7 rounded-full flex items-center justify-center cursor-pointer bg-rose-600 hover:bg-rose-500 text-white"
                    title="中断"
                  >
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={(!inputText.trim() && pendingFiles.length === 0) || !activeSession}
                    className={`w-7 h-7 rounded-full flex items-center justify-center cursor-pointer transition ${
                      (inputText.trim() || pendingFiles.length > 0) && activeSession
                        ? 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-md'
                        : 'bg-[#27272a] text-[#71717a]'
                    }`}
                  >
                    <Send className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          </form>
          )}
        </div>
        )}
      </div>

      {/* 3. SELECT AGENT FOR NEW SESSION MODAL */}
      {showAgentSelectModal && (
        <div className="fixed inset-0 flex items-center justify-center p-4 z-50 animate-fade-in text-xs font-sans">
          <div className="w-full max-w-lg bg-[#18181b] border border-[#27272a] rounded-xl shadow-2xl relative overflow-hidden">
            <div className="p-4 border-b border-[#27272a] flex justify-between items-center bg-[#121214]/60">
              <h3 className="text-sm font-bold text-white flex items-center gap-1.5 font-sans">
                <Sparkles className="w-4 h-4 text-emerald-400" />
                选择协作 Agent 开始会话
              </h3>
              <button
                onClick={() => setShowAgentSelectModal(false)}
                className="text-[#71717a] hover:text-white font-bold cursor-pointer"
              >
                ✕
              </button>
            </div>
            <div className="p-5 max-h-[400px] overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-3">
              {agents.length === 0 && (
                <div className="col-span-full text-center text-[#71717a] py-8">
                  暂无可用 Agent，请先在 Agent 空间创建。
                </div>
              )}
              {agents.map((agent) => (
                <div
                  key={agent.id}
                  onClick={() => handleStartNewWithAgent(agent)}
                  className="p-3 border border-[#27272a] bg-[#121214]/50 rounded-xl hover:border-emerald-500/50 hover:bg-[#121214] cursor-pointer transition flex gap-3"
                >
                  <div className="w-10 h-10 rounded-xl bg-[#18181b] border border-[#27272a] text-xl flex items-center justify-center shrink-0 shadow-inner select-none overflow-hidden">
                    <BotAvatar avatar={agent.avatar} className="w-full h-full p-1 text-xl" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h4 className="font-bold text-white truncate">{agent.name}</h4>
                    <p className="text-[10px] text-[#71717a] line-clamp-2 mt-0.5 leading-normal">
                      {agent.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            <div className="p-4 border-t border-[#27272a] bg-[#121214] flex justify-end">
              <button
                onClick={() => setShowAgentSelectModal(false)}
                className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg cursor-pointer font-semibold"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function updateMsg(
  prev: Message[],
  id: string,
  patch: Partial<Message>,
): Message[] {
  return prev.map((m) => (m.id === id ? { ...m, ...patch } : m));
}

function formatSessionTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    if (diff < 60_000) return '刚刚';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`;
    return d.toLocaleDateString();
  } catch {
    return '';
  }
}

/* ────────────────────────────────────────────────────────────
   Tool-call rendering helpers + ToolCallCard
   Mirrors the structured tool card from frontend/src/components/chat-panel.tsx:
   a status-colored header (running/success/error) with a collapsible detail
   pane showing formatted request args + return result.
   ──────────────────────────────────────────────────────────── */

/** Per-status visual config. Uses Tailwind opacity color tokens so it renders
 *  correctly in both dark and light themes (no raw hex). `hover` deepens the
 *  same status color slightly (→ /15) for a subtle, theme-safe lift instead of
 *  overlaying an unrelated neutral that looks jarring on a colored card. */
const TOOL_STATUS_CFG: Record<
  NonNullable<Message['toolStatus']>,
  { wrap: string; hover: string; icon: string; label: string }
> = {
  pending: {
    wrap: 'bg-amber-500/10 border-amber-500/30',
    hover: 'hover:bg-amber-500/15',
    icon: 'text-amber-400',
    label: '正在生成参数',
  },
  running: {
    wrap: 'bg-amber-500/10 border-amber-500/30',
    hover: 'hover:bg-amber-500/15',
    icon: 'text-amber-400',
    label: '执行中…',
  },
  success: {
    wrap: 'bg-emerald-500/10 border-emerald-500/30',
    hover: 'hover:bg-emerald-500/15',
    icon: 'text-emerald-400',
    label: '已完成',
  },
  error: {
    wrap: 'bg-rose-500/10 border-rose-500/30',
    hover: 'hover:bg-rose-500/15',
    icon: 'text-rose-400',
    label: '失败',
  },
};

/** Pretty-print a tool's request args. Falls back to String() on failure. */
function formatToolArgs(args?: Record<string, unknown>): string {
  if (!args || Object.keys(args).length === 0) return '';
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

/** Try to pretty-print a tool result. If it parses as JSON, re-serialize it
 *  indented; otherwise return the raw string. */
function formatToolResult(raw?: string): { text: string; isJson: boolean } {
  if (!raw) return { text: '', isJson: false };
  const trimmed = raw.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) {
    return { text: raw, isJson: false };
  }
  try {
    return { text: JSON.stringify(JSON.parse(trimmed), null, 2), isJson: true };
  } catch {
    return { text: raw, isJson: false };
  }
}

const RESULT_COLLAPSE_THRESHOLD = 800;

/** 中文“字数”口径（Word/WPS 同款）：CJK 字符（含中文标点）每字计 1，
 *  连续的非 CJK 非空白串（英文单词、数字、emoji 等）整体计 1。
 *  思考内容多为英文，逐字符计数会数倍虚高于视觉感知。 */
const countZi = (s: string): number => {
  const cjkRe = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u30ff\uff00-\uffef]/;
  let n = 0;
  let inRun = false;
  for (const ch of s) {
    if (cjkRe.test(ch)) {
      n += 1;
      inRun = false;
    } else if (/\s/.test(ch)) {
      inRun = false;
    } else if (!inRun) {
      n += 1;
      inRun = true;
    }
  }
  return n;
};

/** Thinking entry — collapsible reasoning card with streaming animation.
 *  内容区固定最大高度（内部滚动），避免长思考撑爆版面；流式期间内部
 *  贴底跟随（终端效果），思考结束自动收起。 */
function ThinkingEntryCard({
  entry,
  streaming,
  onToggle,
}: {
  entry: TimelineEntry;
  /** 本轮思考正在流式输出（消息 thinking 中且该 entry 是最新条目）→ 头部脉冲 + 打字光标 */
  streaming?: boolean;
  onToggle: () => void;
}) {
  const expanded = !!entry.expanded;
  const contentRef = useRef<HTMLDivElement>(null);

  // 流式期间内容超出固定高度时，内部滚动条贴底跟随最新输出。
  useEffect(() => {
    if (streaming && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [entry.content, streaming]);

  return (
    <div className="rounded-lg border border-blue-100 bg-blue-50/50 overflow-hidden font-sans dark:border-indigo-900/40 dark:bg-indigo-950/20">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 border-0 bg-transparent cursor-pointer text-left hover:bg-blue-50 dark:hover:bg-indigo-950/40 transition-colors"
      >
        <Brain className={`text-indigo-400 ${streaming ? 'animate-pulse' : ''}`} size={13} />
        <span className="text-xs font-medium text-indigo-300">
          {streaming ? '思考中' : '思考过程'}
        </span>
        {streaming ? (
          <span className="flex items-center gap-[3px]">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-1 h-1 rounded-full bg-indigo-400 animate-thinking-dot"
                style={{ animationDelay: `${i * 0.18}s` }}
              />
            ))}
          </span>
        ) : (
          !!entry.content && (
            <span className="text-[10px] text-indigo-400/50">{countZi(entry.content)} 字</span>
          )
        )}
        <span className="text-[10px] text-indigo-400/60 ml-auto">{expanded ? '收起' : '展开'}</span>
        <ChevronRight className={`w-3 h-3 text-indigo-400/60 transition-transform ${expanded ? 'rotate-90' : ''}`} />
      </button>
      {expanded && (
        <div
          ref={contentRef}
          className="px-3 pb-2 pt-2 text-xs text-indigo-300/80 whitespace-pre-wrap leading-relaxed border-t border-indigo-900/40 italic max-h-64 overflow-y-auto scrollbar-custom"
        >
          {entry.content}
          {streaming && <span className="animate-caret-blink not-italic">▍</span>}
        </div>
      )}
    </div>
  );
}

/** Tool entry card — renders a timeline tool entry. Dispatches to
 *  ClarificationCard / WorkflowProposalCard / WorkflowTaskCard for known
 *  tool types, else a generic collapsible tool card. Reads entry fields
 *  (toolName/args/result/toolStatus) instead of flat message fields. */
function ToolEntryCard({
  entry,
  msgId,
  interrupted,
  onAnswer,
  onDismiss,
}: {
  entry: TimelineEntry;
  msgId: string;
  interrupted: boolean;
  onAnswer?: (answer: string) => void;
  onDismiss?: () => void;
}) {
  // ask_clarification 走专门的交互卡片（按 clarification_type 分样式）。
  if (entry.toolName === 'ask_clarification') {
    return (
      <ClarificationCard
        entry={entry}
        interrupted={interrupted}
        onAnswer={onAnswer}
        onDismiss={onDismiss}
      />
    );
  }
  // confirm_workflow：工作流确认卡片（从 tool_call args 渲染，tool_result 决定终态）。
  if (entry.toolName === 'confirm_workflow') {
    const args = entry.args ?? {};
    const resultText = entry.result ?? '';
    const isRejection = /取消|拒绝|忽略|cancel/i.test(resultText);
    const forceAction = entry.result ? (isRejection ? 'rejected' : 'confirmed') : undefined;
    return (
      <WorkflowProposalCard
        proposal={{
          type: 'workflow_proposal',
          workflow_name: String(args.workflow_name ?? ''),
          workflow_description: String(args.description ?? ''),
          input_preview: (args.params ?? {}) as Record<string, unknown>,
        }}
        forceAction={forceAction}
        onConfirm={(wfName) => {
          onAnswer?.(`确认执行 ${wfName}`);
          return true;
        }}
        onDismiss={onDismiss}
      />
    );
  }
  // dispatch_workflow：解析 task_created → 内嵌可展开 task 卡片。
  if (entry.toolName === 'dispatch_workflow') {
    const created = parseTaskCreated(entry.result);
    if (created) return <WorkflowTaskCard created={created} />;
  }
  const status = entry.toolStatus ?? 'running';
  const cfg = TOOL_STATUS_CFG[status];
  // render_chart：结果含 ```echarts fenced block → 详情区走 Markdown 自动出图，
  // 且默认展开（图表是工具的核心产出，不该藏在折叠里）。
  const isChartResult =
    entry.toolName === 'render_chart' && /```(?:echarts|chart)\s*\n/i.test(entry.result ?? '');
  // parse_file：成功解析（返回 markdown 首行 "# 文件名（类型，摘要）"）→ 专属
  // 文件卡片（FileText 图标 + 文件名标题 + 元信息徽标），详情区 Markdown 渲染，
  // 默认折叠（提取内容通常较长）。失败（[parse_file] 前缀错误文本）走通用卡片。
  const isParseResult =
    entry.toolName === 'parse_file' &&
    Boolean(entry.result) &&
    !entry.result!.startsWith('[parse_file]');
  const parseArgs = entry.args ?? {};
  const parseLabel = isParseResult
    ? String(parseArgs.path ?? parseArgs.file_id ?? '文件').split('/').pop()
    : '';
  const parseMetaMatch = isParseResult ? /^# .+（(.+?)）/.exec(entry.result ?? '') : null;
  const parseMeta = parseMetaMatch ? parseMetaMatch[1] : '';
  const [expanded, setExpanded] = useState(isChartResult);
  const [resultExpanded, setResultExpanded] = useState(false);

  const argsText = formatToolArgs(entry.args);
  const result = formatToolResult(entry.result);
  const hasDetail = Boolean(argsText || result.text);
  const resultTooLong = result.text.length > RESULT_COLLAPSE_THRESHOLD;
  const shownResult =
    !resultExpanded && resultTooLong
      ? result.text.slice(0, RESULT_COLLAPSE_THRESHOLD) + '…'
      : result.text;

  const StatusIcon =
    status === 'running' || status === 'pending' ? Loader2 : status === 'success' ? CheckCircle : AlertTriangle;

  return (
    <div className={`rounded-xl rounded-tl-none border overflow-hidden font-sans ${cfg.wrap}`}>
      {/* Header — click to toggle detail */}
      <button
        type="button"
        onClick={() => hasDetail && setExpanded((v) => !v)}
        className={`w-full flex items-center gap-2 px-3.5 py-2.5 bg-transparent border-0 text-left transition-colors ${
          hasDetail ? `cursor-pointer ${cfg.hover}` : 'cursor-default'
        }`}
      >
        <StatusIcon
          className={`w-3.5 h-3.5 shrink-0 ${cfg.icon} ${status === 'running' || status === 'pending' ? 'animate-spin' : ''}`}
        />
        {isParseResult ? (
          <FileText className={`w-3.5 h-3.5 shrink-0 ${cfg.icon}`} />
        ) : (
          <Wrench className={`w-3.5 h-3.5 shrink-0 ${cfg.icon}`} />
        )}
        <span className={`text-xs font-semibold truncate ${cfg.icon}`}>
          {isParseResult
            ? `文件解析 · ${parseLabel}`
            : entry.toolName ||
              (status === 'pending' || status === 'running' ? '调用工具…' : 'unknown_tool')}
        </span>
        {isParseResult && parseMeta && (
          <span className={`shrink-0 px-1.5 py-0.5 rounded text-[9px] font-medium bg-white/5 border border-white/10 ${cfg.icon} opacity-80`}>
            {parseMeta}
          </span>
        )}
        <span className={`text-[10px] ${cfg.icon} opacity-70`}>{cfg.label}</span>
        {hasDetail && (
          <span className={`ml-auto flex items-center gap-1 text-[10px] ${cfg.icon} opacity-70`}>
            {expanded ? '收起' : '详情'}
            <ChevronRight
              className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`}
            />
          </span>
        )}
      </button>

      {/* Detail pane */}
      {expanded && hasDetail && (
        <div className="border-t border-[#27272a] px-3.5 pb-3 pt-2.5 space-y-2.5">
          {argsText && (
            <div>
              <div className={`text-[10px] font-semibold mb-1 opacity-60 ${cfg.icon}`}>
                请求参数
              </div>
              <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all rounded-lg p-2 bg-[#121214] border border-[#27272a] text-[#a1a1aa] font-mono max-h-48 overflow-y-auto">
                {argsText}
              </pre>
            </div>
          )}
          {result.text && (
            <div>
              <div className={`text-[10px] font-semibold mb-1 opacity-60 ${cfg.icon} flex items-center gap-1.5`}>
                返回结果
                {result.isJson && (
                  <span className="px-1 py-0 rounded bg-[#27272a] text-[9px] opacity-80">JSON</span>
                )}
              </div>
              {isChartResult || isParseResult ? (
                // render_chart：走 Markdown 渲染（识别 ```echarts 出图 + 摘要文本）；
                // parse_file：提取内容是 markdown（标题/表格），同样走 Markdown 渲染。
                <Markdown content={result.text} />
              ) : (
                <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all rounded-lg p-2 bg-[#121214] border border-[#27272a] text-[#a1a1aa] font-mono max-h-64 overflow-y-auto">
                  {shownResult}
                </pre>
              )}
              {resultTooLong && (
                <button
                  type="button"
                  onClick={() => setResultExpanded((v) => !v)}
                  className={`mt-1 text-[10px] underline ${cfg.icon} opacity-80 hover:opacity-100 cursor-pointer`}
                >
                  {resultExpanded ? '收起结果' : `展开全部 (${result.text.length} 字符)`}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────
   ClarificationCard — ask_clarification 交互卡片
   按 clarification_type 分样式渲染问题 + 选项/按钮 + 内联输入。
   交互态（!answered && clarificationActive）选项可点、内联可填；已回答后选项
   静态高亮、答案以 indigo 气泡附在卡片下方。暗色配色用 Tailwind opacity token，
   明暗主题通用。对照 frontend/src/components/chat-panel.tsx 的 ask_clarification 渲染。
   ──────────────────────────────────────────────────────────── */
function ClarificationCard({
  entry,
  interrupted,
  onAnswer,
  onDismiss,
}: {
  entry: TimelineEntry;
  interrupted: boolean;
  onAnswer?: (answer: string) => void;
  onDismiss?: () => void;
}) {
  const args = entry.args ?? {};
  const question = args.question ? String(args.question) : '';
  // LLM 可能把 options 传成 JSON 字符串，做一次兼容解析。
  const rawOptions = args.options;
  let options: string[] = [];
  if (Array.isArray(rawOptions)) options = rawOptions as string[];
  else if (typeof rawOptions === 'string') {
    try {
      options = JSON.parse(rawOptions);
    } catch {
      /* ignore */
    }
  }
  const clarificationType = args.clarification_type
    ? String(args.clarification_type)
    : 'missing_info';
  const answered = !!entry.result;
  // 交互态：只要未回答就可交互。不再门控 interrupted 标志——该标志用于 /resume
  // 路由判断，不应影响卡片交互元素的可见性（否则 interrupt 状态没及时传递时
  // 选项/输入框会被隐藏，导致卡片「看不清楚」）。
  void interrupted; // 保留 prop 以兼容调用方，但不再用作交互门控
  const interactive = !answered;

  // fields 向导模式：ask_clarification 带 fields 时渲染多步结构化表单，
  // 单次收集多个字段。fields 可能是数组或 JSON 字符串（LLM 容错）。
  const rawFields = args.fields;
  let formFields: ClarificationField[] = [];
  if (Array.isArray(rawFields)) formFields = rawFields as ClarificationField[];
  else if (typeof rawFields === 'string') {
    try {
      const parsed = JSON.parse(rawFields);
      if (Array.isArray(parsed)) formFields = parsed as ClarificationField[];
    } catch {
      /* ignore */
    }
  }
  if (formFields.length > 0) {
    return (
      <ClarificationFormCard
        question={question}
        context={args.context ? String(args.context) : undefined}
        fields={formFields}
        answered={answered}
        result={entry.result}
        onSubmit={(jsonStr) => onAnswer?.(jsonStr)}
        onDismiss={onDismiss}
      />
    );
  }

  const [inlineText, setInlineText] = useState('');
  const submitInline = () => {
    const t = inlineText.trim();
    if (!t) return;
    onAnswer?.(t);
    setInlineText('');
  };

  const isRisk = clarificationType === 'risk_confirmation';
  const isSuggestion = clarificationType === 'suggestion';
  const verticalOptions =
    clarificationType === 'approach_choice' || clarificationType === 'ambiguous_requirement';
  const dismissed = entry.result === DISMISSED_CLARIFICATION_TEXT;

  // 配色完全复用 TOOL_STATUS_CFG 的视觉语言（已验证好看 + 协调）：
  // 外层用淡彩透明底（/10）+ 彩色边框（/30），标题文字用彩色（-400）。
  // 问题正文放在实底深色内嵌区块（bg-[#121214]，和通用工具卡 detail pane 的
  // <pre> 一致）里，用白字——这样背景好看、文字清晰，两全其美。
  const accent = isRisk
    ? { icon: 'text-amber-400', wrap: 'bg-amber-500/10 border-amber-500/30', sym: '⚠️' }
    : isSuggestion
      ? { icon: 'text-emerald-400', wrap: 'bg-emerald-500/10 border-emerald-500/30', sym: '💡' }
      : { icon: 'text-indigo-400', wrap: 'bg-indigo-500/10 border-indigo-500/30', sym: '❓' };

  return (
    <div className={`rounded-xl rounded-tl-none border overflow-hidden font-sans shadow-sm ${accent.wrap}`}>
      {/* 标题行：与通用工具卡 header 一致（透明底 + 彩色字） */}
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        {answered ? (
          <CheckCircle className={`w-3.5 h-3.5 shrink-0 ${accent.icon}`} />
        ) : (
          <Loader2 className={`w-3.5 h-3.5 shrink-0 ${accent.icon} animate-spin`} />
        )}
        <span className={`text-xs font-semibold truncate ${accent.icon}`}>澄清提问</span>
        <span className={`text-[10px] ${accent.icon} opacity-70`}>
          {answered ? (dismissed ? '已忽略' : '已回答') : '等待回答'}
        </span>
        {/* 忽略：不回答此问题，恢复自由输入（下一次发送走普通 stream） */}
        {interactive && onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto px-2 py-0.5 rounded text-[10px] text-[#a1a1aa] hover:text-[#fafafa] hover:bg-white/5 border border-transparent hover:border-[#3f3f46] transition cursor-pointer"
          >
            忽略
          </button>
        )}
      </div>

      {/* 问题正文 + 交互区：实底深色内嵌区块（bg-[#121214]）+ 白字，可读性优先 */}
      <div className="mx-2.5 mb-2.5 rounded-lg bg-[#121214] border border-[#27272a] px-3.5 py-3">
        <div className="flex items-start gap-2.5">
          <span className={`text-sm mt-0.5 select-none ${accent.icon}`}>{accent.sym}</span>
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-medium whitespace-pre-wrap leading-relaxed text-[#fafafa]">
              {question}
            </div>

            {options.length > 0 && (
              <div className={`flex gap-2 mt-3 ${verticalOptions ? 'flex-col' : 'flex-wrap'}`}>
                {options.map((opt, i) =>
                  interactive ? (
                    <button
                      key={i}
                      type="button"
                      onClick={() => onAnswer?.(opt)}
                      className="text-left px-3 py-1.5 rounded-lg text-xs border bg-[#09090b] border-[#3f3f46] text-[#fafafa] hover:border-indigo-500/60 hover:bg-indigo-500/10 transition cursor-pointer"
                    >
                      {opt}
                    </button>
                  ) : (
                    <div
                      key={i}
                      className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                        entry.result === opt
                          ? 'bg-indigo-500/20 border-indigo-500/60 text-[#fafafa] font-medium'
                          : 'bg-[#09090b] border-[#27272a] text-[#a1a1aa]'
                      }`}
                    >
                      {opt}
                      {entry.result === opt && <span className="ml-1.5 text-indigo-400">✓</span>}
                    </div>
                  ),
                )}
              </div>
            )}

            {/* risk_confirmation：确认执行 / 取消 */}
            {interactive && isRisk && (
              <div className="flex gap-2 mt-3">
                <button
                  type="button"
                  onClick={() => onAnswer?.('确认')}
                  className="px-3.5 py-1.5 rounded-lg text-xs bg-rose-600 text-white hover:bg-rose-500 transition cursor-pointer"
                >
                  确认执行
                </button>
                <button
                  type="button"
                  onClick={() => onAnswer?.('取消')}
                  className="px-3.5 py-1.5 rounded-lg text-xs bg-[#09090b] border border-[#3f3f46] text-[#fafafa] hover:bg-[#27272a] transition cursor-pointer"
                >
                  取消
                </button>
              </div>
            )}

            {/* suggestion：接受按钮 */}
            {interactive && isSuggestion && (
              <div className="flex gap-2 mt-3 items-center">
                <button
                  type="button"
                  onClick={() => onAnswer?.('接受建议')}
                  className="px-3.5 py-1.5 rounded-lg text-xs bg-emerald-600 text-white hover:bg-emerald-500 transition cursor-pointer"
                >
                  接受
                </button>
                <span className="text-[11px] text-[#a1a1aa]">或输入其他回答</span>
              </div>
            )}

            {/* 内联自由输入（交互态通用兜底） */}
            {interactive && (
              <div className="flex gap-1.5 mt-2.5">
                <input
                  type="text"
                  value={inlineText}
                  onChange={(e) => setInlineText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      submitInline();
                    }
                  }}
                  placeholder="输入你的回答…"
                  className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg text-xs border border-[#3f3f46] bg-[#09090b] text-[#fafafa] placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500/60"
                />
                <button
                  type="button"
                  onClick={submitInline}
                  disabled={!inlineText.trim()}
                  className="px-2.5 py-1.5 rounded-lg text-xs bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 transition cursor-pointer"
                >
                  发送
                </button>
              </div>
            )}

            {/* 已答：答案在卡片内部展示（紧贴问题下方，与 ClarificationFormCard 一致），
                不再渲染独立的右对齐气泡。已忽略则只给一行提示。 */}
            {dismissed && (
              <div className="mt-3 pt-3 border-t border-[#27272a] text-xs text-[#71717a]">
                已忽略此问题——可直接在输入框重新描述需求或上传文件
              </div>
            )}
            {answered && entry.result && !dismissed && !options.includes(entry.result) && (
              <div className="mt-3 pt-3 border-t border-[#27272a] flex items-baseline gap-2 text-xs">
                <span className={`shrink-0 ${accent.icon}`}>你的回答:</span>
                <span className="text-[#fafafa] font-medium break-all whitespace-pre-wrap">
                  {entry.result}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
