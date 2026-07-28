/**
 * KbVectorDetailPage — manage a vector-type Knowledge Base.
 *
 * Three panels:
 *   1. Document list (name / status / progress / chunks / actions)
 *   2. Upload (pdf/docx/md/txt; fires async indexing on the backend)
 *   3. Retrieval test (query → top-k chunks with score + source)
 *
 * Indexing is async (Celery): after upload the list shows pending/parsing/
 * embedding states; the user can reindex failed docs or refresh to poll.
 */
import { useState, type ChangeEvent } from 'react';
import {
  ArrowLeft, Loader2, Search, Trash2, Upload, RefreshCw, FileText, AlertCircle, CheckCircle2,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { knowledgeApi, knowledgeKeys, type KbDocument, type KbSearchResultItem } from '../services/knowledge-api';
import { confirmDialog } from './ui/confirm';
import { toast } from './ui/toast';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const STATUS_META: Record<
  KbDocument['parse_status'],
  { label: string; cls: string; icon: typeof CheckCircle2 }
> = {
  pending: { label: '排队中', cls: 'text-zinc-400', icon: Loader2 },
  parsing: { label: '解析中', cls: 'text-amber-400', icon: Loader2 },
  embedding: { label: '向量化', cls: 'text-sky-400', icon: Loader2 },
  completed: { label: '已完成', cls: 'text-emerald-400', icon: CheckCircle2 },
  failed: { label: '失败', cls: 'text-rose-400', icon: AlertCircle },
};

export function KbVectorDetailPage({
  kbId,
  kbName,
  onBack,
}: {
  kbId: string;
  kbName: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<KbSearchResultItem[] | null>(null);

  const docsQ = useQuery({
    queryKey: knowledgeKeys.documents(kbId),
    queryFn: () => knowledgeApi.listDocuments(kbId),
    // Poll while any doc is in-flight (pending/parsing/embedding).
    refetchInterval: (q) => {
      const inFlight = q.state.data?.items.some(
        (d) => d.parse_status === 'pending' || d.parse_status === 'parsing' || d.parse_status === 'embedding',
      );
      return inFlight ? 3000 : false;
    },
  });
  const docs = docsQ.data?.items ?? [];

  const uploadM = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.uploadDocuments(kbId, files),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kbId) });
      if (res.errors.length > 0) {
        toast.error(`${res.errors.length} 个文件上传失败`);
      } else if (res.created.length > 0) {
        toast.success(`已上传 ${res.created.length} 个文件，正在后台索引…`);
      }
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : '上传失败'),
  });

  const deleteDocM = useMutation({
    mutationFn: (docId: string) => knowledgeApi.deleteDocument(kbId, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kbId) });
      toast.success('文档已删除');
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : '删除失败'),
  });

  const reindexM = useMutation({
    mutationFn: (docId: string) => knowledgeApi.reindexDocument(kbId, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.documents(kbId) });
      toast.success('已重新派发索引任务');
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : '重试失败'),
  });

  const searchM = useMutation({
    mutationFn: () => knowledgeApi.search(kbId, query, 5),
    onSuccess: (res) => setSearchResults(res.results),
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : '检索失败'),
  });

  const handleUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    uploadM.mutate(files);
    e.target.value = '';
  };

  const handleDelete = async (doc: KbDocument) => {
    const ok = await confirmDialog({
      title: `删除文档「${doc.name}」？`,
      description: '该文档的所有切片和向量将被清除。',
      okText: '删除',
      danger: true,
    });
    if (ok) deleteDocM.mutate(doc.id);
  };

  const handleSearch = () => {
    if (!query.trim()) return;
    searchM.mutate();
  };

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-white hover:bg-[#27272a] transition cursor-pointer">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <div className="flex items-center gap-2 text-xs text-[#71717a]">
            <span>知识库</span><span>/</span><span className="text-white font-semibold">{kbName}</span>
            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-950/40 border border-emerald-700/40 text-emerald-400">向量库</span>
          </div>
          <p className="text-[11px] text-[#52525b] mt-0.5">上传文档自动解析→切片→向量化；agent/workflow 可用 kb_search 检索</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* ── Documents ── */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold text-white flex items-center gap-2">
              <FileText className="w-3.5 h-3.5 text-emerald-400" />
              文档 ({docs.length})
            </h3>
            <div className="flex items-center gap-2">
              <button
                onClick={() => docsQ.refetch()}
                className="p-1 rounded-lg text-[#71717a] hover:text-white hover:bg-[#27272a] transition cursor-pointer"
                title="刷新"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${docsQ.isFetching ? 'animate-spin' : ''}`} />
              </button>
              <label className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] bg-emerald-600 hover:bg-emerald-500 text-white font-semibold cursor-pointer transition">
                {uploadM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                上传
                <input
                  type="file"
                  accept=".pdf,.docx,.md,.markdown,.txt"
                  multiple
                  className="hidden"
                  onChange={handleUpload}
                />
              </label>
            </div>
          </div>

          {docs.length === 0 ? (
            <div className="text-center py-12 text-xs text-[#52525b] border border-dashed border-[#27272a] rounded-xl">
              还没有文档，点击右上角上传 PDF/Word/Markdown。
            </div>
          ) : (
            <div className="space-y-2">
              {docs.map((doc) => {
                const meta = STATUS_META[doc.parse_status] ?? STATUS_META.pending;
                const Icon = meta.icon;
                const inFlight = doc.parse_status === 'pending' || doc.parse_status === 'parsing' || doc.parse_status === 'embedding';
                return (
                  <div key={doc.id} className="bg-[#18181b] border border-[#27272a] rounded-lg p-3 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-white truncate">{doc.name}</span>
                          <span className={`inline-flex items-center gap-1 text-[10px] font-bold ${meta.cls} shrink-0`}>
                            <Icon className={`w-3 h-3 ${inFlight ? 'animate-spin' : ''}`} />
                            {meta.label}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-[#71717a] font-mono mt-0.5">
                          <span>{doc.file_type.toUpperCase()}</span>
                          <span>{formatSize(doc.file_size)}</span>
                          {doc.chunk_count > 0 && <span>{doc.chunk_count} 切片</span>}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {doc.parse_status === 'failed' && (
                          <button
                            onClick={() => reindexM.mutate(doc.id)}
                            className="p-1 rounded text-amber-500 hover:text-amber-400 hover:bg-amber-950/20 transition cursor-pointer"
                            title="重新索引"
                          >
                            <RefreshCw className={`w-3 h-3 ${reindexM.isPending ? 'animate-spin' : ''}`} />
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(doc)}
                          className="p-1 rounded text-rose-500 hover:text-rose-400 hover:bg-rose-950/20 transition cursor-pointer"
                          title="删除"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                    {/* progress bar */}
                    {inFlight && (
                      <div className="h-1 bg-[#27272a] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-emerald-500 transition-all"
                          style={{ width: `${doc.parse_progress}%` }}
                        />
                      </div>
                    )}
                    {doc.parse_status === 'failed' && doc.parse_error && (
                      <p className="text-[10px] text-rose-400 break-all">{doc.parse_error}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Retrieval test ── */}
        <div className="space-y-3">
          <h3 className="text-xs font-bold text-white flex items-center gap-2">
            <Search className="w-3.5 h-3.5 text-indigo-400" />
            检索测试
          </h3>
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
              placeholder="输入查询文本…"
              className="flex-1 px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-white text-xs focus:outline-none focus:border-indigo-600 transition"
            />
            <button
              onClick={handleSearch}
              disabled={searchM.isPending || !query.trim()}
              className="px-3 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-semibold cursor-pointer transition flex items-center gap-1"
            >
              {searchM.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              检索
            </button>
          </div>

          {searchResults !== null && (
            <div className="space-y-2">
              {searchResults.length === 0 ? (
                <p className="text-xs text-[#71717a] py-6 text-center">无匹配结果（可能文档尚未索引完成或阈值过高）。</p>
              ) : (
                searchResults.map((r, i) => (
                  <div key={i} className="bg-[#18181b] border border-[#27272a] rounded-lg p-3 space-y-1.5">
                    <div className="flex items-center justify-between text-[10px] font-mono">
                      <span className="text-[#a1a1aa] truncate">{r.source_file}{r.page ? ` · P${r.page}` : ''}</span>
                      <span className="text-emerald-400 shrink-0 ml-2">{r.score.toFixed(3)}</span>
                    </div>
                    <p className="text-xs text-[#d4d4d8] leading-relaxed line-clamp-4">{r.text}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
