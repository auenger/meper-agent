/**
 * SkillDetailPage — view/edit a Skill's files.
 *
 * Directory Skills: left file tree (getFileTree) + right editor (getFileContent
 * / updateFileContent). Single-file Skills (SKILL.md only): render instructions
 * as Markdown with an edit toggle. Native Tailwind (no antd Tree/TextArea).
 *
 * NOTE: backend ToolResponse.files is always empty (the file list is not
 * persisted to Mongo), so directory mode is decided from getFileTree's leaf
 * count rather than tool.files.
 */
import { useMemo, useState, type FC } from 'react';
import {
  ArrowLeft, Folder, FileText, Loader2, Save, Undo2, FilePen, Trash2, Pencil, FileX,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  toolsApi, toolKeys, type SkillFileTreeNode,
} from '../services/tools-api';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';
import { getErrorMessage } from '../lib/api-client';
import { Markdown } from './Markdown';
import AvatarField from './AvatarField';

export function SkillDetailPage({
  toolId,
  toolName,
  onBack,
}: {
  toolId: string;
  toolName: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: tool } = useQuery({
    queryKey: toolKeys.detail(toolId),
    queryFn: () => toolsApi.get(toolId),
  });
  const { data: treeData, isLoading: treeLoading } = useQuery({
    queryKey: toolKeys.files(toolId),
    queryFn: () => toolsApi.getFileTree(toolId),
  });

  // tool.files is always empty (backend gap); use the on-disk tree instead.
  const leafCount = treeData ? countLeaves(treeData.files) : 0;
  const isDirectory = leafCount > 1;
  // For single-file mode, edit the (only) leaf — fallback to SKILL.md.
  const singlePath = leafCount >= 1 ? (firstLeaf(treeData?.files ?? []) ?? 'SKILL.md') : 'SKILL.md';

  // Avatar: local override carries the cache-busting ?t= right after upload;
  // falls back to the persisted tool.avatar from the query.
  const [avatarOverride, setAvatarOverride] = useState<string | null>(null);
  const displayAvatar = avatarOverride ?? tool?.avatar ?? '';

  const deleteM = useMutation({
    mutationFn: () => toolsApi.remove(toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: toolKeys.all });
      toast.success('Skill 已删除');
      onBack();
    },
  });

  const handleDelete = async () => {
    const ok = await confirmDialog({
      title: `删除 Skill「${toolName}」？`,
      description: '该 Skill 的文件将被清除。若被 Agent 引用将拒绝删除。',
      okText: '删除',
      danger: true,
    });
    if (ok) deleteM.mutate();
  };

  return (
    <div className="space-y-4">
      {/* Breadcrumb / header */}
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-[#71717a]">
            <span>技能商店</span><span>/</span><span className="text-white font-semibold truncate">{toolName}</span>
          </div>
          {tool && (
            <p className="text-[11px] text-[#52525b] mt-0.5">
              v{tool.version} · {leafCount} 文件 · {tool.source}
            </p>
          )}
        </div>
        <button
          onClick={handleDelete}
          disabled={deleteM.isPending}
          className="ml-auto flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg text-rose-300 hover:text-rose-200 hover:bg-rose-950/30 border border-rose-800/40 transition cursor-pointer disabled:opacity-50"
        >
          {deleteM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          删除
        </button>
      </div>

      {/* 头像 */}
      <div className="flex items-center gap-3 px-4 py-3 bg-[#18181b] rounded-xl border border-[#27272a]">
        <span className="text-xs font-semibold text-[#a1a1aa] shrink-0">头像</span>
        <AvatarField
          value={displayAvatar}
          entityId={toolId}
          upload={toolsApi.uploadAvatar}
          onChange={(url) => {
            setAvatarOverride(url);
            // 上传时后端已写库；移除（空串）需后端清空 avatar，再统一失效缓存刷新。
            if (!url) {
              toolsApi.removeAvatar(toolId).finally(() =>
                queryClient.invalidateQueries({ queryKey: toolKeys.detail(toolId) }),
              );
            } else {
              queryClient.invalidateQueries({ queryKey: toolKeys.detail(toolId) });
            }
          }}
        />
        {!displayAvatar && (
          <span className="text-[10px] text-[#52525b]">未设置时显示默认 logo</span>
        )}
      </div>

      {treeLoading ? (
        <div className="flex items-center justify-center py-12 text-[#71717a]">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />加载…
        </div>
      ) : isDirectory ? (
        <DirectoryEditor toolId={toolId} />
      ) : (
        <SingleFileView toolId={toolId} filePath={singlePath} />
      )}
    </div>
  );
}

/** Directory mode: file tree + editor. */
function DirectoryEditor({ toolId }: { toolId: string }) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const { data: treeData, isLoading } = useQuery({
    queryKey: toolKeys.files(toolId),
    queryFn: () => toolsApi.getFileTree(toolId),
  });

  // Auto-select the first file once the tree loads.
  const firstFile = useMemo(() => firstLeaf(treeData?.files ?? []), [treeData]);
  if (firstFile && !selectedPath) {
    // setState in render guard — fine for one-time init.
    setTimeout(() => setSelectedPath(firstFile), 0);
  }

  return (
    <div className="flex gap-4 h-[calc(100vh-180px)] min-h-[400px]">
      {/* File tree */}
      <div className="w-72 shrink-0 rounded-xl border border-[#27272a] bg-[#18181b] overflow-y-auto p-3">
        {isLoading ? (
          <div className="flex items-center justify-center py-8 text-[#71717a]">
            <Loader2 className="w-4 h-4 animate-spin mr-2" /> 加载文件…
          </div>
        ) : (
          <div className="space-y-0.5">
            {(treeData?.files ?? []).map((node) => (
              <TreeRow
                key={node.key}
                node={node}
                depth={0}
                selected={selectedPath}
                onSelect={setSelectedPath}
              />
            ))}
          </div>
        )}
      </div>

      {/* Editor */}
      <div className="flex-1 min-w-0">
        {selectedPath ? (
          // key=filePath forces a fresh instance per file so the editor's
          // local/loaded state resets on switch — otherwise the previous
          // file's content sticks and is wrongly flagged as an edit.
          <FileEditor key={selectedPath} toolId={toolId} filePath={selectedPath} />
        ) : (
          <div className="flex items-center justify-center h-full text-[#52525b] text-sm border border-[#27272a] rounded-xl bg-[#18181b]">
            选择左侧文件查看内容
          </div>
        )}
      </div>
    </div>
  );
}

/** Recursively render a tree node (folders expandable, files selectable). */
const TreeRow: FC<{
  node: SkillFileTreeNode;
  depth: number;
  selected: string | null;
  onSelect: (path: string) => void;
}> = ({ node, depth, selected, onSelect }) => {
  const [open, setOpen] = useState(true);
  const pad = { paddingLeft: `${depth * 12 + 8}px` };

  if (!node.is_leaf) {
    return (
      <div>
        <button
          onClick={() => setOpen((o) => !o)}
          style={pad}
          className="w-full flex items-center gap-1.5 py-1 text-xs text-[#a1a1aa] hover:text-white hover:bg-[#27272a] rounded transition cursor-pointer"
        >
          <Folder className="w-3.5 h-3.5 text-sky-400 shrink-0" />
          <span className="truncate font-medium">{node.title}</span>
        </button>
        {open && node.children?.map((child) => (
          <TreeRow key={child.key} node={child} depth={depth + 1} selected={selected} onSelect={onSelect} />
        ))}
      </div>
    );
  }

  const isSel = selected === node.key;
  return (
    <button
      onClick={() => onSelect(node.key)}
      style={pad}
      className={`w-full flex items-center gap-1.5 py-1 text-xs rounded transition cursor-pointer ${
        isSel ? 'bg-indigo-500/10 text-indigo-300' : 'text-[#a1a1aa] hover:text-white hover:bg-[#27272a]'
      }`}
    >
      <FileText className="w-3.5 h-3.5 text-[#71717a] shrink-0" />
      <span className="truncate font-mono">{node.title}</span>
    </button>
  );
};

/** Load + edit a single file, with dirty/save and a Markdown preview toggle. */
function FileEditor({ toolId, filePath }: { toolId: string; filePath: string }) {
  const queryClient = useQueryClient();
  const [local, setLocal] = useState<string>('');
  const [loaded, setLoaded] = useState(false);
  const [mode, setMode] = useState<'edit' | 'preview'>('edit');

  const { data: file, isLoading, error } = useQuery({
    queryKey: toolKeys.fileContent(toolId, filePath),
    queryFn: () => toolsApi.getFileContent(toolId, filePath),
  });

  // Sync remote content into local once per load.
  if (!loaded && file !== undefined) {
    setTimeout(() => { setLocal(file.content); setLoaded(true); }, 0);
  }

  const isDirty = loaded && local !== (file?.content ?? '');
  const isMarkdown = /\.md$/i.test(filePath);
  const showPreview = mode === 'preview' && isMarkdown;

  const saveM = useMutation({
    mutationFn: (content: string) => toolsApi.updateFileContent(toolId, filePath, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: toolKeys.detail(toolId) });
      queryClient.invalidateQueries({ queryKey: toolKeys.fileContent(toolId, filePath) });
    },
  });

  return (
    <div className="h-full flex flex-col rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#27272a] shrink-0">
        <span className="text-[11px] font-mono text-[#a1a1aa] truncate">{filePath}</span>
        <div className="flex items-center gap-2">
          {isMarkdown && (
            <div className="flex items-center bg-[#121214] border border-[#27272a] rounded-lg p-0.5">
              <button
                onClick={() => setMode('edit')}
                className={`px-2 py-0.5 text-[10px] rounded-md transition cursor-pointer ${mode === 'edit' ? 'bg-indigo-600 text-white font-semibold' : 'text-[#a1a1aa] hover:text-white'}`}
              >
                编辑
              </button>
              <button
                onClick={() => setMode('preview')}
                className={`px-2 py-0.5 text-[10px] rounded-md transition cursor-pointer ${mode === 'preview' ? 'bg-indigo-600 text-white font-semibold' : 'text-[#a1a1aa] hover:text-white'}`}
              >
                预览
              </button>
            </div>
          )}
          {isDirty && <span className="text-[10px] text-amber-400 font-semibold">未保存</span>}
          <button
            onClick={() => file && setLocal(file.content)}
            disabled={!isDirty}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] text-[#a1a1aa] hover:text-white hover:bg-[#27272a] disabled:opacity-40 transition cursor-pointer"
          >
            <Undo2 className="w-3 h-3" /> 撤销
          </button>
          <button
            onClick={() => saveM.mutate(local)}
            disabled={!isDirty || saveM.isPending}
            className="flex items-center gap-1 px-3 py-1 rounded-lg text-[11px] bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40 transition cursor-pointer font-semibold"
          >
            {saveM.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            保存
          </button>
        </div>
      </div>

      {/* Editor / preview area */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-[#71717a]"><Loader2 className="w-5 h-5 animate-spin mr-2" />加载…</div>
      ) : error ? (
        <div className="flex-1 flex flex-col items-center justify-center text-center px-6 gap-2 text-[#71717a]">
          <FileX className="w-6 h-6 text-rose-400" />
          <p className="text-xs font-semibold">文件加载失败</p>
          <p className="text-[10px] text-[#52525b] max-w-md">{getErrorMessage(error, '文件不存在或无法读取')}</p>
        </div>
      ) : showPreview ? (
        <div className="flex-1 overflow-y-auto p-5">
          <Markdown content={local} />
        </div>
      ) : (
        <textarea
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          className="flex-1 w-full p-4 bg-transparent text-[#fafafa] font-mono text-xs leading-relaxed resize-none focus:outline-none"
          spellCheck={false}
        />
      )}
      <div className="px-4 py-1.5 border-t border-[#27272a] text-[10px] text-[#52525b] shrink-0">
        {local.length} 字符
      </div>
    </div>
  );
}

/** Single-file Skill (SKILL.md only): Markdown preview + edit toggle. */
function SingleFileView({ toolId, filePath }: { toolId: string; filePath: string }) {
  const [editing, setEditing] = useState(false);
  const { data: tool, isLoading } = useQuery({
    queryKey: toolKeys.detail(toolId),
    queryFn: () => toolsApi.get(toolId),
  });

  if (editing) {
    return (
      <div className="h-[calc(100vh-180px)] min-h-[400px] flex flex-col gap-3">
        <button
          onClick={() => setEditing(false)}
          className="self-start flex items-center gap-1 text-[11px] text-[#a1a1aa] hover:text-white cursor-pointer"
        >
          <ArrowLeft className="w-3 h-3" /> 返回预览
        </button>
        <div className="flex-1 min-h-0">
          <FileEditor key={filePath} toolId={toolId} filePath={filePath} />
        </div>
      </div>
    );
  }

  const content = tool?.instructions || tool?.description || '';

  return (
    <div className="rounded-xl border border-[#27272a] bg-[#18181b] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#27272a]">
        <div className="flex items-center gap-2">
          <FilePen className="w-4 h-4 text-indigo-400" />
          <span className="text-sm font-bold text-white">工具说明</span>
        </div>
        <button
          onClick={() => setEditing(true)}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] bg-indigo-600 hover:bg-indigo-500 text-white transition cursor-pointer font-semibold"
        >
          <Pencil className="w-3 h-3" /> 编辑
        </button>
      </div>
      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-[#71717a]"><Loader2 className="w-5 h-5 animate-spin mr-2" />加载…</div>
      ) : content ? (
        <div className="p-5 max-h-[60vh] overflow-y-auto">
          <Markdown content={content} />
        </div>
      ) : (
        <p className="p-5 text-xs text-[#52525b]">（无说明）</p>
      )}
    </div>
  );
}

/** Count leaf files in a tree. */
function countLeaves(nodes: SkillFileTreeNode[]): number {
  return nodes.reduce(
    (sum, n) => sum + (n.is_leaf ? 1 : countLeaves(n.children ?? [])),
    0,
  );
}

/** Find the first leaf path in a tree (for auto-select). */
function firstLeaf(nodes: SkillFileTreeNode[]): string | null {
  for (const n of nodes) {
    if (n.is_leaf) return n.key;
    const child = firstLeaf(n.children ?? []);
    if (child) return child;
  }
  return null;
}
