/**
 * BuiltinToolsPage — lists system-provided tools (bash/read/write/...).
 *
 * Backed by toolsApi.listBuiltins(). Each card shows the tool icon (mapped from
 * name), description, and its parameter schema as tags. Client-side search over
 * name + description.
 *
 * Ported from frontend/src/pages/tools-page.tsx, rendered with native Tailwind
 * (lucide icons instead of @ant-design/icons).
 */
import { useMemo, useState, type FC } from 'react';
import { Search, Terminal, BookOpen, FilePen, FileSearch, Wrench, Code, Loader2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { toolsApi, toolKeys, type BuiltinTool } from '../services/tools-api';

const BUILTIN_KEYS = ['builtin-tools'] as const;

/** Map a builtin tool name → lucide icon + accent color. */
function iconFor(name: string): { Icon: typeof Terminal; color: string } {
  const n = name.toLowerCase();
  if (n.includes('bash') || n.includes('shell') || n.includes('exec')) {
    return { Icon: Terminal, color: 'text-emerald-400 bg-emerald-500/10' };
  }
  if (n.includes('run_code') || n.includes('code')) {
    return { Icon: Code, color: 'text-amber-400 bg-amber-500/10' };
  }
  if (n.includes('read') || n.includes('fetch') || n.includes('get')) {
    return { Icon: BookOpen, color: 'text-sky-400 bg-sky-500/10' };
  }
  if (n.includes('write') || n.includes('edit') || n.includes('create') || n.includes('update')) {
    return { Icon: FilePen, color: 'text-indigo-400 bg-indigo-500/10' };
  }
  if (n.includes('parse') || n.includes('extract')) {
    return { Icon: FileSearch, color: 'text-violet-400 bg-violet-500/10' };
  }
  return { Icon: Wrench, color: 'text-zinc-400 bg-zinc-500/10' };
}

/** Extract parameter names from a JSON-Schema-style parameters object. */
function paramNames(params: Record<string, unknown> | undefined): string[] {
  const props = params?.properties;
  if (props && typeof props === 'object') {
    return Object.keys(props as Record<string, unknown>);
  }
  return [];
}

export function BuiltinToolsPage() {
  const [search, setSearch] = useState('');

  const { data: tools, isLoading } = useQuery({
    queryKey: BUILTIN_KEYS,
    queryFn: () => toolsApi.listBuiltins(),
  });

  const filtered = useMemo(() => {
    const list = tools ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return list;
    return list.filter((t) =>
      [t.name, t.description].some((v) => v.toLowerCase().includes(q)),
    );
  }, [tools, search]);

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Wrench className="w-5 h-5 text-indigo-400" />
          内置工具
        </h2>
        <p className="text-xs text-[#71717a] mt-1">
          系统内置工具（bash / read / write 等），开箱即用，无需配置。
        </p>
      </div>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#71717a]" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索工具名 / 描述"
          className="w-full pl-9 pr-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-sm text-white placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 transition"
        />
      </div>

      {/* Grid */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-[#71717a]">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中…
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-[#52525b]">
          <Wrench className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-sm">暂无内置工具</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((t) => (
            <BuiltinCard key={t.name} tool={t} />
          ))}
        </div>
      )}
    </div>
  );
}

const BuiltinCard: FC<{ tool: BuiltinTool }> = ({ tool }) => {
  const { Icon, color } = iconFor(tool.name);
  const params = paramNames(tool.parameters);
  return (
    <div className="p-4 rounded-xl border border-[#27272a] bg-[#18181b] hover:border-[#3f3f46] transition">
      <div className="flex items-start gap-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${color}`}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-bold text-white font-mono">{tool.name}</h3>
          <p className="text-xs text-[#a1a1aa] mt-1 line-clamp-2 leading-relaxed">
            {tool.description || '无描述'}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-[#27272a]">
        {params.length === 0 ? (
          <span className="text-[10px] text-[#52525b]">无参数</span>
        ) : (
          params.map((p) => (
            <span
              key={p}
              className="px-1.5 py-0.5 rounded bg-[#121214] border border-[#27272a] text-[10px] text-[#a1a1aa] font-mono"
            >
              {p}
            </span>
          ))
        )}
      </div>
    </div>
  );
};
