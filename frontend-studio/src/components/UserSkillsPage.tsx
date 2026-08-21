/**
 * UserSkillsPage — 用户技能三合一页（studio 迁移版，§7/§8）。
 *
 * 数据源：/user-skills 统一市场（官方 tools + 个人 user_skills 双库合并）。
 * 三个 Tab：
 *  - 技能广场：卡片墙（积分/加载徽标、查看全文、安装/卸载、fork）
 *  - 我的技能：own 卡片（启停/发布/删除/编辑）+ 管理员官方管理提示
 *  - 我的记忆：条目增删改 + 用量
 *
 * 与 frontend 管理端功能对齐；组件为 studio 风格（lucide + tailwind，无 antd）。
 */
import { useState, useRef, useEffect } from 'react';
import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Sparkles, ThumbsUp, Zap, Download, Copy, Trash2, Eye, Plus,
  ToggleLeft, ToggleRight, Send, FileText, Brain, RefreshCw, Crown,
} from 'lucide-react';
import { getErrorMessage } from '../lib/api-client';
import { useAuthStore } from '../stores/auth-store';
import { AvatarRender } from './AvatarRender';
import { toast } from './ui/toast';
import { confirmDialog } from './ui/confirm';
import { toolsApi } from '../services/tools-api';
import {
  userSkillsApi,
  type MarketplaceItem,
  type UserSkillItem,
  type MemoryState,
} from '../services/user-skills-api';

type Tab = 'marketplace' | 'mine' | 'memory';

/** 从 SKILL.md frontmatter 提取 name；缺失时用文件名（去扩展名）。 */
function extractSkillName(content: string, filename: string): string {
  const m = content.match(/^---\s*\n[\s\S]*?\nname:\s*([^\s]+)/);
  return (m?.[1] ?? filename.replace(/\.md$|\.markdown$/i, '')).slice(0, 64);
}

interface Props {
  theme?: 'dark' | 'light';
  /** 打开官方技能详情（复用 SkillDetailPage） */
  onOpenOfficial?: (id: string, name: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  private: '私有', submitted: '审核中', published: '已发布', hidden: '已隐藏',
};

export function UserSkillsPage({ theme = 'dark', onOpenOfficial }: Props) {
  const [tab, setTab] = useState<Tab>('marketplace');
  const isAdmin = useIsAdmin();

  const textMuted = theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-500';
  const activeTabCls = theme === 'dark' ? 'bg-[#27272a] text-[#fafafa]' : 'bg-slate-100 text-slate-900';

  const tabs: { id: Tab; label: string; icon: typeof Sparkles }[] = [
    { id: 'marketplace', label: '技能广场', icon: Sparkles },
    { id: 'mine', label: '我的技能', icon: FileText },
    { id: 'memory', label: '我的记忆', icon: Brain },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border-0 cursor-pointer transition ${tab === t.id ? activeTabCls : `${textMuted} hover:opacity-80`}`}
            style={tab === t.id ? undefined : { background: 'transparent' }}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'marketplace' && <MarketplaceTab theme={theme} isAdmin={isAdmin} />}
      {tab === 'mine' && <MySkillsTab theme={theme} isAdmin={isAdmin} onOpenOfficial={onOpenOfficial} />}
      {tab === 'memory' && <MemoryTab theme={theme} />}

    </div>
  );
}

function useIsAdmin(): boolean {
  const role = useAuthStore((s) => s.user?.role ?? '');
  return role === 'admin' || role === 'developer';
}


/* ══════════════ 技能广场 ══════════════ */

function MarketplaceTab({ theme, isAdmin }: {
  theme: 'dark' | 'light';
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [preview, setPreview] = useState<MarketplaceItem | null>(null);

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['usk-marketplace', q],
    queryFn: () => userSkillsApi.marketplace(q),
  });

  // admin：待审核技能合并进卡片墙排最前（同卡展示，琥珀色区分）
  const { data: pending = [] } = useQuery({
    queryKey: ['usk-review'],
    queryFn: () => userSkillsApi.reviewList('submitted'),
    enabled: isAdmin,
  });
  const all: MarketplaceItem[] = [
    ...pending.map((s) => ({ ...s, kind: 'user' as const, author_name: '用户', installed: false, is_own: false, my_vote: 0, score: 0 })),
    ...items,
  ];

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['usk-marketplace'] });
    void qc.invalidateQueries({ queryKey: ['usk-list'] });
  };

  const installM = useMutation({
    mutationFn: (v: { id: string; alias?: string }) => userSkillsApi.install(v.id, v.alias ?? ''),
    onSuccess: (r) => { invalidate(); void r; },
    onError: (e) => toast.error(getErrorMessage(e, '安装失败')),
  });
  const uninstallM = useMutation({
    mutationFn: (id: string) => userSkillsApi.uninstall(id),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '卸载失败')),
  });
  const forkM = useMutation({
    mutationFn: (id: string) => userSkillsApi.fork(id),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(getErrorMessage(e, 'fork 失败')),
  });

  if (isLoading) return <div className="py-12 text-center text-sm opacity-60">加载中…</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索技能名称/描述…"
          className={`flex-1 max-w-md px-3 py-1.5 rounded-lg text-sm border outline-none ${
            theme === 'dark' ? 'bg-[#18181b] border-[#27272a] text-[#fafafa]' : 'bg-white border-slate-200'
          }`}
        />
      </div>

      {items.length === 0 && (
        <div className="py-12 text-center text-sm opacity-60">暂无技能</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {all.map((it) => (
          <MarketCard
            key={it.id}
            item={it}
            theme={theme}
            isAdmin={isAdmin}
            onPreview={() => setPreview(it)}
            onInstall={() => installM.mutate({ id: it.id })}
            onUninstall={() => uninstallM.mutate(it.id)}
            onFork={() => forkM.mutate(it.id)}
          />
        ))}
      </div>

      {preview && <PreviewModal item={preview} theme={theme} onClose={() => setPreview(null)} />}
    </div>
  );
}

function MarketCard({ item, theme, isAdmin, onPreview, onInstall, onUninstall, onFork }: {
  item: MarketplaceItem;
  theme: 'dark' | 'light';
  isAdmin: boolean;
  onPreview: () => void;
  onInstall: () => void;
  onUninstall: () => void;
  onFork: () => void;
}) {
  const card = theme === 'dark' ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200';
  const textMuted = theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-500';
  const chip = theme === 'dark' ? 'bg-[#27272a]' : 'bg-slate-100';

  const qc = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const isPending = item.status === 'submitted';
  const isOfficial = item.kind === 'official';
  const reviewM = useMutation({
    mutationFn: (v: { action: 'approve' | 'reject'; reason?: string }) =>
      userSkillsApi.review(item.id, v.action, v.reason ?? ''),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['usk-review'] });
      void qc.invalidateQueries({ queryKey: ['usk-marketplace'] });
    },
    onError: (e) => toast.error(getErrorMessage(e, '审核失败')),
  });

  return (
    <div
      className={`rounded-xl border p-5 flex flex-col gap-3 transition ${
        isPending
          ? theme === 'dark'
            ? 'bg-amber-950/20 border-amber-700/50'
            : 'bg-amber-50 border-amber-300'
          : isOfficial
            ? 'border-amber-500/40 border-t-2 border-t-amber-500/70 hover:border-amber-400'
            : `hover:border-[#71717a] ${card}`
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          {/* 头像（官方技能 avatar；个人技能用图标占位）——对齐旧 SkillsStore 观感 */}
          <div className={`w-12 h-12 rounded-xl overflow-hidden flex items-center justify-center shrink-0 border ${
            isOfficial ? 'border-amber-500/50' : theme === 'dark' ? 'bg-[#121214] border-[#27272a]' : 'bg-slate-50 border-slate-200'
          }`}>
            {/* 与旧 SkillsStore 一致：AvatarRender 空值回退 /AFLogo.png 占位 */}
            <AvatarRender value={item.avatar ?? ''} className="w-full h-full p-1.5" />
          </div>
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="text-sm font-bold truncate flex-1 min-w-0" title={item.name}>{item.name}</span>
              {item.is_own && (
                <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded ${chip} text-blue-400`}>我的</span>
              )}
              {item.installed && (
                <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded ${chip} text-emerald-500`}>已安装</span>
              )}
            </div>
            <p className={`text-[11px] ${textMuted}`}>
              by <span className="text-indigo-400">{item.author_name}</span>
            </p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          {/* 来源徽章固定右上——卡片间严格对齐 */}
          {isOfficial ? (
            <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/40 text-amber-500 font-semibold">
              <Crown size={10} /> 官方
            </span>
          ) : (
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${chip} ${textMuted}`}>
              用户创作
            </span>
          )}
          <div className="flex items-center gap-2 text-xs">
          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${chip}`} title="对话中真实 load 次数">
            <Zap size={11} className="text-emerald-500" />
            <b>{item.stats?.load_count ?? 0}</b>
            <span className={textMuted}>次</span>
          </span>
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${chip}`}
            title="点赞回复的派生积分（👍 − 👎）"
          >
            <ThumbsUp size={11} className={(item.score ?? 0) >= 0 ? 'text-blue-400' : 'text-rose-400'} />
            <b className={(item.score ?? 0) >= 0 ? 'text-blue-400' : 'text-rose-400'}>
              {(item.score ?? 0) > 0 ? `+${item.score}` : item.score ?? 0}
            </b>
          </span>
          </div>
        </div>
      </div>

      <p className={`text-xs leading-relaxed line-clamp-2 min-h-[2rem] ${textMuted}`}>
        {item.description || '（无描述）'}
      </p>

      {isPending && isAdmin && rejecting && (
        <div className="flex items-center gap-1.5">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="驳回理由（会反馈给提交者）"
            autoFocus
            className={`flex-1 min-w-0 px-2 py-1 rounded-lg text-xs border outline-none ${
              theme === 'dark' ? 'bg-[#0f0f10] border-[#27272a] text-[#fafafa]' : 'bg-slate-50 border-slate-200'
            }`}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && reason.trim()) {
                reviewM.mutate({ action: 'reject', reason: reason.trim() });
                setRejecting(false);
              }
              if (e.key === 'Escape') setRejecting(false);
            }}
          />
          <button
            onClick={() => {
              if (reason.trim()) reviewM.mutate({ action: 'reject', reason: reason.trim() });
              setRejecting(false);
            }}
            className="text-xs border-0 bg-transparent text-rose-400 cursor-pointer shrink-0"
          >
            确认驳回
          </button>
          <button onClick={() => setRejecting(false)} className="text-xs border-0 bg-transparent cursor-pointer opacity-70 shrink-0">
            取消
          </button>
        </div>
      )}

      {/* 内联驳回理由输入（待审卡） */}
      {isPending && isAdmin && rejecting && (
        <div className="flex items-center gap-1.5">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="驳回理由（会反馈给提交者）"
            autoFocus
            className={`flex-1 min-w-0 px-2 py-1 rounded-lg text-xs border outline-none ${
              theme === 'dark' ? 'bg-[#0f0f10] border-[#27272a] text-[#fafafa]' : 'bg-slate-50 border-slate-200'
            }`}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && reason.trim()) {
                reviewM.mutate({ action: 'reject', reason: reason.trim() });
                setRejecting(false);
              }
              if (e.key === 'Escape') setRejecting(false);
            }}
          />
          <button
            onClick={() => {
              if (reason.trim()) reviewM.mutate({ action: 'reject', reason: reason.trim() });
              setRejecting(false);
            }}
            className="text-xs border-0 bg-transparent text-rose-400 cursor-pointer shrink-0"
          >
            确认驳回
          </button>
          <button onClick={() => setRejecting(false)} className="text-xs border-0 bg-transparent cursor-pointer opacity-70 shrink-0">
            取消
          </button>
        </div>
      )}

      <div className="flex items-center justify-between pt-2 border-t border-inherit">
        <button
          onClick={onPreview}
          className="flex items-center gap-1 text-xs border-0 bg-transparent cursor-pointer opacity-80 hover:opacity-100"
        >
          <Eye size={13} /> 查看全文
        </button>
        <div className="flex items-center gap-1.5">
          {isPending && isAdmin && !rejecting && (
            <>
              <button
                onClick={() => reviewM.mutate({ action: 'approve' })}
                disabled={reviewM.isPending}
                className="flex items-center gap-1 text-xs border-0 bg-transparent text-emerald-400 cursor-pointer disabled:opacity-40"
              >
                通过
              </button>
              <button
                onClick={() => setRejecting(true)}
                disabled={reviewM.isPending}
                className="flex items-center gap-1 text-xs border-0 bg-transparent text-rose-400 cursor-pointer disabled:opacity-40"
              >
                驳回
              </button>
            </>
          )}
          {item.kind === 'user' && !item.is_own && !isAdmin && (
            item.installed ? (
              <button onClick={onUninstall} className="flex items-center gap-1 text-xs border-0 bg-transparent text-rose-400 cursor-pointer">
                卸载
              </button>
            ) : (
              <button onClick={onInstall} className="flex items-center gap-1 text-xs border-0 bg-transparent text-blue-400 cursor-pointer">
                <Download size={13} /> 安装
              </button>
            )
          )}
          {/* fork：用户技能（非自己的）；官方技能仅非管理员 */}
          {((item.kind === 'user' && !item.is_own) || (item.kind === 'official' && !isAdmin)) && (
            <button
              onClick={onFork}
              className="flex items-center gap-1 text-xs border-0 bg-transparent cursor-pointer opacity-80 hover:opacity-100"
              title={isAdmin ? '收录为官方技能' : '复制为己用'}
            >
              <Copy size={13} /> {isAdmin ? '收录' : 'Fork'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function PreviewModal({ item, theme, onClose }: {
  item: MarketplaceItem;
  theme: 'dark' | 'light';
  onClose: () => void;
}) {
  const { data: detail } = useQuery({
    queryKey: ['usk-preview', item.id],
    queryFn: () => userSkillsApi.get(item.id),
  });
  const box = theme === 'dark' ? 'bg-[#18181b] border-[#27272a] text-[#fafafa]' : 'bg-white border-slate-200 text-slate-800';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-8" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50" />
      <div
        className={`relative w-full max-w-2xl max-h-[80vh] rounded-xl border flex flex-col ${box}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-inherit">
          <span className="text-sm font-semibold truncate max-w-md" title={item.name}>{item.name}</span>
          <button onClick={onClose} className="border-0 bg-transparent cursor-pointer text-sm opacity-70">✕</button>
        </div>
        <pre className="flex-1 overflow-auto p-4 text-xs leading-relaxed whitespace-pre-wrap font-mono">
          {detail?.content ?? '加载中…'}
        </pre>
      </div>
    </div>
  );
}


/* ══════════════ 我的技能 ══════════════ */

function MySkillsTab({ theme, isAdmin, onOpenOfficial }: {
  theme: 'dark' | 'light';
  isAdmin: boolean;
  onOpenOfficial?: (id: string, name: string) => void;
}) {
  const qc = useQueryClient();
  const [editTarget, setEditTarget] = useState<UserSkillItem | null>(null);
  // admin：上传创建官方技能（对齐旧 SkillsStore 的「上传 Markdown Skill」）
  const folderInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = folderInputRef.current;
    if (el) {
      el.setAttribute('webkitdirectory', '');
      el.setAttribute('directory', '');
    }
  }, []);
  const uploadM = useMutation({
    mutationFn: (files: File[]) => toolsApi.upload(files),
    onSuccess: (res) => {
      toast.success(`已创建 ${res.created.length} 个官方技能${res.errors.length ? `（${res.errors.length} 项跳过/失败）` : ''}`);
      void qc.invalidateQueries({ queryKey: ['usk-official-list'] });
      void qc.invalidateQueries({ queryKey: ['usk-marketplace'] });
    },
    onError: (e) => toast.error(getErrorMessage(e, '上传失败')),
  });
  const handleUpload = (e: { currentTarget: HTMLInputElement; }) => {
    const files = Array.from(e.currentTarget.files ?? []) as File[];
    if (files.length) uploadM.mutate(files);
    e.currentTarget.value = '';
  };

  /** 普通用户：上传 md 创建个人技能（frontmatter name 优先，fallback 文件名；
   *  文件夹模式取 SKILL.md，无则第一个 md）。 */
  const userUploadM = useMutation({
    mutationFn: async (files: File[]) => {
      const mds = files.filter((f) => /\.md$|\.markdown$/i.test(f.name));
      if (!mds.length) throw new Error('未找到 .md 文件');
      const pick = mds.find((f) => f.name === 'SKILL.md') ?? mds[0];
      // 多 md 各建一个；文件夹模式（webkitRelativePath 同目录）只建 SKILL.md
      const list = mds.some((f) => f.name === 'SKILL.md' && (f as File & { webkitRelativePath?: string }).webkitRelativePath)
        ? mds.filter((f) => f.name === 'SKILL.md')
        : mds;
      for (const f of list) {
        const content = await f.text();
        const name = extractSkillName(content, f.name);
        await userSkillsApi.create(name, content);
      }
      return list.length;
    },
    onSuccess: (n) => {
      toast.success(`已创建 ${n} 个个人技能`);
      invalidate();
    },
    onError: (e) => toast.error(getErrorMessage(e, '创建失败')),
  });
  const handleUserUpload = (e: { currentTarget: HTMLInputElement }) => {
    const files = Array.from(e.currentTarget.files ?? []) as File[];
    if (files.length) userUploadM.mutate(files);
    e.currentTarget.value = '';
  };

  const { data: mine = [] } = useQuery({
    queryKey: ['usk-list'],
    queryFn: () => userSkillsApi.list(),
  });
  const officials = useQuery({
    queryKey: ['usk-official-list'],
    queryFn: () => userSkillsApi.marketplace(''),
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['usk-list'] });

  const toggleM = useMutation({
    mutationFn: (s: UserSkillItem) => userSkillsApi.update(s.id, { binding_enabled: !s.binding_enabled }),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '操作失败')),
  });
  const submitM = useMutation({
    mutationFn: (id: string) => userSkillsApi.submit(id),
    onSuccess: () => { invalidate(); void qc.invalidateQueries({ queryKey: ['usk-marketplace'] }); },
    onError: (e) => toast.error(getErrorMessage(e, '提交失败')),
  });
  const deleteM = useMutation({
    mutationFn: (id: string) => userSkillsApi.remove(id),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '删除失败')),
  });
  const uninstallMineM = useMutation({
    mutationFn: (id: string) => userSkillsApi.uninstall(id),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '卸载失败')),
  });
  const avatarM = useMutation({
    mutationFn: (v: { id: string; file: File }) => userSkillsApi.uploadAvatar(v.id, v.file),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '头像上传失败')),
  });
  const handleAvatarUpload = (s: UserSkillItem, e: { currentTarget: HTMLInputElement }) => {
    const f = e.currentTarget.files?.[0];
    if (f) avatarM.mutate({ id: s.id, file: f });
    e.currentTarget.value = '';
  };

  const card = theme === 'dark' ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200';
  const textMuted = theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-500';
  const chip = theme === 'dark' ? 'bg-[#27272a]' : 'bg-slate-100';


  return (
    <div className="space-y-6">
      {/* 管理员提示：无个人技能，create 产出官方 */}
      {isAdmin && (
        <div className={`rounded-xl border p-3 text-xs ${card} ${textMuted}`}>
          管理员没有个人技能——会话内或手动创建的技能直接进入<b>官方池</b>（可在广场编辑/绑定 Agent）。
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">{isAdmin ? '官方技能' : '我的技能'}</h3>
          <div className="flex items-center gap-2">
            {/* 两种创建入口（admin=官方技能走 /tools/upload；用户=个人技能前端解析后走 /user-skills） */}
            <label
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow transition cursor-pointer"
              title={isAdmin ? '上传一个或多个 .md 文件，创建官方技能' : '上传 .md 文件，创建个人技能'}
            >
              <Plus size={13} /> 上传 Markdown Skill
              <input
                type="file"
                accept=".md,.markdown"
                multiple
                className="hidden"
                onChange={isAdmin ? handleUpload : handleUserUpload}
              />
            </label>
            <label
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-sky-600 hover:bg-sky-500 text-white shadow transition cursor-pointer"
              title="上传整个技能目录（个人技能取其中的 SKILL.md）"
            >
              <Plus size={13} /> 上传 Skill 文件夹
              <input
                ref={folderInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={isAdmin ? handleUpload : handleUserUpload}
              />
            </label>
          </div>
        </div>

        {/* 管理员：官方墙（从广场数据过滤） */}
        {isAdmin ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {(officials.data ?? []).filter((m) => m.kind === 'official').map((m) => (
              <div key={m.id} className={`rounded-xl border p-4 flex flex-col gap-2 ${card}`}>
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-12 h-12 rounded-xl overflow-hidden flex items-center justify-center shrink-0 border ${
                theme === 'dark' ? 'bg-[#121214] border-[#27272a]' : 'bg-slate-50 border-slate-200'
              }`}>
                <AvatarRender value={m.avatar ?? ''} className="w-full h-full p-1.5" />
              </div>
                  <div className="flex items-center justify-between gap-2 min-w-0 flex-1">
                    <span className="text-sm font-bold truncate">{m.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${chip} ${textMuted} shrink-0`}>官方</span>
                  </div>
                </div>
                <p className={`text-xs line-clamp-2 ${textMuted}`}>{m.description}</p>
                <div className="pt-2 border-t border-inherit">
                  <button
                    onClick={() => onOpenOfficial?.(m.id, m.name)}
                    className="flex items-center gap-1 text-xs border-0 bg-transparent text-blue-400 cursor-pointer"
                  >
                    编辑（官方详情）
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {mine.map((s) => (
              <div key={s.id} className={`rounded-xl border p-4 flex flex-col gap-2 ${card}`}>
                <div className="flex items-center gap-3 min-w-0">
                  {s.source === 'own' ? (
                    <label
                      className="relative w-12 h-12 rounded-xl overflow-hidden flex items-center justify-center shrink-0 border cursor-pointer group ${
                        theme === 'dark' ? 'bg-[#121214] border-[#27272a]' : 'bg-slate-50 border-slate-200'
                      }"
                      title="点击更换头像（PNG/JPEG/WEBP ≤2MB）"
                    >
                      <AvatarRender value={s.avatar ?? ''} className="w-full h-full p-1.5" />
                      <span className="absolute inset-0 hidden group-hover:flex items-center justify-center bg-black/50 text-[9px] text-white">
                        换头像
                      </span>
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        onChange={(ev) => handleAvatarUpload(s, ev)}
                      />
                    </label>
                  ) : (
                    <div className={`w-12 h-12 rounded-xl overflow-hidden flex items-center justify-center shrink-0 border ${
                      theme === 'dark' ? 'bg-[#121214] border-[#27272a]' : 'bg-slate-50 border-slate-200'
                    }`}>
                      <AvatarRender value={s.avatar ?? ''} className="w-full h-full p-1.5" />
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2 min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-sm font-bold truncate">{s.alias || s.name}</span>
                      {s.derived_from_name && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${chip} ${textMuted} shrink-0`} title={`fork 自 ${s.derived_from_name}`}>
                          fork
                        </span>
                      )}
                    </div>
                    {s.source === 'installed' ? (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${chip} text-emerald-500 shrink-0`}>已安装</span>
                    ) : (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${chip} ${textMuted} shrink-0`}>
                        {STATUS_LABEL[s.status] ?? s.status}
                      </span>
                    )}
                  </div>
                </div>
                <p className={`text-xs line-clamp-2 min-h-[2rem] ${textMuted}`}>{s.description || '（无描述）'}</p>
              <div className="flex items-center justify-between pt-2 border-t border-inherit">
                  <button
                    onClick={() => toggleM.mutate(s)}
                    className={`flex items-center gap-1 text-xs border-0 bg-transparent cursor-pointer ${s.binding_enabled ? 'text-emerald-400' : textMuted}`}
                    title="控制该技能是否注入对话"
                  >
                    {s.binding_enabled ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                    {s.binding_enabled ? '已启用' : '已停用'}
                  </button>
                  <div className="flex items-center gap-2 text-xs">
                    {s.source === 'installed' ? (
                      <button
                        onClick={() => uninstallMineM.mutate(s.id)}
                        className="flex items-center gap-0.5 border-0 bg-transparent text-rose-400 cursor-pointer"
                        title="卸载（不影响原作者的技能）"
                      >
                        卸载
                      </button>
                    ) : (
                      <>
                        <button onClick={() => setEditTarget(s)} className="border-0 bg-transparent text-blue-400 cursor-pointer">编辑</button>
                        {s.status === 'private' && (
                          <button onClick={() => submitM.mutate(s.id)} className="flex items-center gap-0.5 border-0 bg-transparent cursor-pointer opacity-80">
                            <Send size={11} /> 发布
                          </button>
                        )}
                        <button onClick={() => deleteM.mutate(s.id)} className="flex items-center gap-0.5 border-0 bg-transparent text-rose-400 cursor-pointer">
                          <Trash2 size={12} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {mine.length === 0 && <div className={`col-span-full py-8 text-center text-sm ${textMuted}`}>还没有技能——可上传创建、对话中让 Agent 保存、或去技能广场安装</div>}
          </div>
        )}
      </section>

      {editTarget && (
        <SkillEditModal
          theme={theme}
          target={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); invalidate(); }}
        />
      )}
    </div>
  );
}

function SkillEditModal({ theme, target, onClose, onSaved }: {
  theme: 'dark' | 'light';
  target: UserSkillItem;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = true;
  const { data: detail } = useQuery({
    queryKey: ['usk-detail', target?.id],
    queryFn: () => userSkillsApi.get(target!.id),
    enabled: isEdit,
  });
  const [name, setName] = useState('');
  const [content, setContent] = useState('');

  // 已加载详情后初始化（一次性）
  const [initialized, setInitialized] = useState(false);
  if (isEdit && detail && !initialized) {
    setName(detail.name);
    setContent(detail.content);
    setInitialized(true);
  }

  const saveM = useMutation({
    mutationFn: async () => {
      if (isEdit) await userSkillsApi.update(target!.id, { content });
      else await userSkillsApi.create(name, content);
    },
    onSuccess: onSaved,
    onError: (e) => toast.error(getErrorMessage(e, '保存失败')),
  });

  const box = theme === 'dark' ? 'bg-[#18181b] border-[#27272a] text-[#fafafa]' : 'bg-white border-slate-200 text-slate-800';
  const inputCls = `w-full px-3 py-1.5 rounded-lg text-sm border outline-none ${
    theme === 'dark' ? 'bg-[#0f0f10] border-[#27272a]' : 'bg-slate-50 border-slate-200'
  }`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-8">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className={`relative w-full max-w-2xl max-h-[85vh] rounded-xl border flex flex-col ${box}`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-inherit">
          <span className="text-sm font-semibold truncate max-w-md" title={target.name}>{`编辑：${target.name}`}</span>
          <button onClick={onClose} className="border-0 bg-transparent cursor-pointer text-sm opacity-70">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-3">
          {!isEdit && (
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="技能名（字母/数字/-/_，≤64 字符）"
              className={inputCls}
            />
          )}
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={'---\nname: my-skill\ndescription: Use when …（触发条件 + 一句话行为）\n---\n# 指令正文…'}
            rows={16}
            className={`${inputCls} font-mono text-xs leading-relaxed resize-none`}
          />
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-inherit">
          <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-sm border-0 bg-transparent cursor-pointer opacity-80">取消</button>
          <button
            onClick={() => saveM.mutate()}
            disabled={saveM.isPending || (!isEdit && !name.trim()) || !content.trim()}
            className="px-4 py-1.5 rounded-lg text-sm border-0 bg-blue-600 text-white cursor-pointer disabled:opacity-40"
          >
            {saveM.isPending ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}


/* ══════════════ 我的记忆 ══════════════ */

function MemoryTab({ theme }: { theme: 'dark' | 'light' }) {
  const qc = useQueryClient();
  const { data: memory } = useQuery<MemoryState>({
    queryKey: ['usk-memory'],
    queryFn: () => userSkillsApi.getMemory(),
  });
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['usk-memory'] });

  const saveM = useMutation({
    mutationFn: (entries: string[]) => userSkillsApi.setMemory(entries),
    onSuccess: invalidate,
    onError: (e) => toast.error(getErrorMessage(e, '保存失败')),
  });

  const entries = memory?.entries ?? [];
  const box = theme === 'dark' ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200';
  const textMuted = theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-500';
  const inputCls = `flex-1 px-3 py-1.5 rounded-lg text-sm border outline-none ${
    theme === 'dark' ? 'bg-[#0f0f10] border-[#27272a] text-[#fafafa]' : 'bg-slate-50 border-slate-200'
  }`;

  return (
    <div className="space-y-4 max-w-2xl">
      <div className={`flex items-center justify-between rounded-xl border px-4 py-3 ${box}`}>
        <span className={`text-xs ${textMuted}`}>{memory?.usage ?? '加载中…'}</span>
        <button
          onClick={async () => {
                const ok = await confirmDialog({ title: '清空全部记忆？', description: '所有持久偏好记录将被删除，对话中的 Agent 将不再记得这些偏好。', okText: '清空', danger: true });
                if (ok) saveM.mutate([]);
              }}
          className="flex items-center gap-1 text-xs border-0 bg-transparent text-rose-400 cursor-pointer"
        >
          <RefreshCw size={12} /> 清空
        </button>
      </div>

      <div className="space-y-2">
        {entries.map((e, i) => (
          <div key={i} className={`flex items-center gap-2 rounded-xl border px-3 py-2 ${box}`}>
            {editing?.index === i ? (
              <>
                <input
                  value={editing.text}
                  onChange={(ev) => setEditing({ index: i, text: ev.target.value })}
                  className={inputCls}
                  autoFocus
                />
                <button
                  onClick={() => {
                    const next = [...entries];
                    next[i] = editing.text.trim();
                    saveM.mutate(next);
                    setEditing(null);
                  }}
                  className="text-xs border-0 bg-transparent text-blue-400 cursor-pointer shrink-0"
                >
                  保存
                </button>
                <button onClick={() => setEditing(null)} className="text-xs border-0 bg-transparent cursor-pointer opacity-70 shrink-0">取消</button>
              </>
            ) : (
              <>
                <span className="flex-1 text-sm">{e}</span>
                <button onClick={() => setEditing({ index: i, text: e })} className="text-xs border-0 bg-transparent text-blue-400 cursor-pointer shrink-0">编辑</button>
                <button
                  onClick={() => saveM.mutate(entries.filter((_, j) => j !== i))}
                  className="text-xs border-0 bg-transparent text-rose-400 cursor-pointer shrink-0"
                >
                  删除
                </button>
              </>
            )}
          </div>
        ))}
        {entries.length === 0 && (
          <div className={`py-10 text-center text-sm ${textMuted}`}>
            还没有记忆——对话中的持久偏好（如"偏好中文"）会由 Agent 自动保存
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="手动添加一条记忆（如：周报要极简）"
          className={inputCls}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter' && draft.trim()) {
              saveM.mutate([...entries, draft.trim()]);
              setDraft('');
            }
          }}
        />
        <button
          onClick={() => {
            if (!draft.trim()) return;
            saveM.mutate([...entries, draft.trim()]);
            setDraft('');
          }}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm border-0 bg-blue-600 text-white cursor-pointer"
        >
          <Plus size={13} /> 添加
        </button>
      </div>
    </div>
  );
}

