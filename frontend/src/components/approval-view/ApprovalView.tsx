/**
 * ApprovalView — 审批视图渲染组件（antd 版）。
 *
 * 当 Human 节点配置了 config.view 时，审批卡片不再只显示 title + description
 * 纯文字，而是按 view.sections 渲染结构化的"审批材料包"。
 *
 * 变量解析采用前端策略：接收 human_context.view（配置模板）+ variables
 * （变量快照），用 resolveVar 在 JS 侧解析 {{node.field}} 引用。
 *
 * 四种区块类型：fields（字段卡片）/ document（文档预览）/ files（文件）/ table（表格）。
 * 配色与 frontend 浅色主题一致：slate 色系 + 审批紫 #8B5CF6 accent。
 */
import type { ReactNode } from 'react';
import { FileTextOutlined, PaperClipOutlined, TableOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { Markdown } from '../Markdown';
import { TaskOutputFiles } from '../task-result-card';
import { resolveVar, formatDisplay } from './resolveVar';
import type {
  ApprovalViewConfig,
  ViewSection,
  FieldsSection,
  DocumentSection,
  FilesSection,
  TableSection,
} from './types';

interface ApprovalViewProps {
  view: ApprovalViewConfig;
  variables: Record<string, unknown>;
  taskId: string;
}

export function ApprovalView({ view, variables, taskId }: ApprovalViewProps) {
  const sections = view?.sections;
  if (!Array.isArray(sections) || sections.length === 0) return null;

  return (
    <div className="border border-[#8B5CF6]/30 bg-[#F5F3FF] rounded-lg p-3 space-y-3">
      {sections.map((section, idx) => (
        <SectionRenderer key={idx} section={section} variables={variables} taskId={taskId} />
      ))}
    </div>
  );
}

/** 区块标题栏：图标 + 标题。 */
function SectionTitle({ icon, title }: { icon: ReactNode; title?: string }) {
  if (!title) return null;
  return (
    <div className="flex items-center gap-1.5 text-sm font-medium text-[#6D28D9] mb-1.5">
      <span className="shrink-0" style={{ color: '#8B5CF6' }}>
        {icon}
      </span>
      <span>{title}</span>
    </div>
  );
}

/** 按 section.type 分发。 */
function SectionRenderer({
  section,
  variables,
  taskId,
}: {
  section: ViewSection;
  variables: Record<string, unknown>;
  taskId: string;
}) {
  switch (section.type) {
    case 'fields':
      return <FieldsBlock section={section} variables={variables} />;
    case 'document':
      return <DocumentBlock section={section} variables={variables} />;
    case 'files':
      return <FilesBlock section={section} taskId={taskId} />;
    case 'table':
      return <TableBlock section={section} variables={variables} />;
    default:
      return null;
  }
}

/** fields 区块：键值对网格。 */
function FieldsBlock({ section, variables }: { section: FieldsSection; variables: Record<string, unknown> }) {
  const items = Array.isArray(section.items) ? section.items : [];
  if (items.length === 0) return null;
  return (
    <div>
      <SectionTitle icon={<UnorderedListOutlined />} title={section.title} />
      <div className="bg-white rounded-lg border border-[#E2E8F0] divide-y divide-[#E2E8F0]">
        {items.map((item, idx) => {
          const val = resolveVar(item.source, variables);
          const display = formatDisplay(val);
          return (
            <div key={idx} className="flex items-baseline gap-3 px-3 py-1.5">
              <span className="text-xs text-[#94A3B8] shrink-0 min-w-[5em]">{item.label}</span>
              <span
                className={`text-sm whitespace-pre-wrap break-words ${display ? 'text-[#0F172A]' : 'text-[#CBD5E1]'}`}
              >
                {display || '—'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** document 区块：长文本 + Markdown。 */
function DocumentBlock({ section, variables }: { section: DocumentSection; variables: Record<string, unknown> }) {
  const resolved = resolveVar(section.source, variables);
  const content = typeof resolved === 'string' ? resolved : resolved == null ? '' : formatDisplay(resolved);
  return (
    <div>
      <SectionTitle icon={<FileTextOutlined />} title={section.title} />
      <div className="bg-white rounded-lg border border-[#E2E8F0] p-3 max-h-64 overflow-y-auto">
        {content ? (
          <Markdown content={content} />
        ) : (
          <span className="text-xs text-[#CBD5E1]">暂无内容</span>
        )}
      </div>
    </div>
  );
}

/** files 区块：复用 TaskOutputFiles（按 taskId 拉取整任务产物）。 */
function FilesBlock({ section, taskId }: { section: FilesSection; taskId: string }) {
  return (
    <div>
      <SectionTitle icon={<PaperClipOutlined />} title={section.title} />
      <TaskOutputFiles taskId={taskId} />
    </div>
  );
}

/** table 区块：多行数据表格。 */
function TableBlock({ section, variables }: { section: TableSection; variables: Record<string, unknown> }) {
  const resolved = resolveVar(section.source, variables);
  const rows = Array.isArray(resolved) ? resolved.filter((r) => r != null) : [];

  if (rows.length === 0) {
    return (
      <div>
        <SectionTitle icon={<TableOutlined />} title={section.title} />
        <div className="bg-white rounded-lg border border-[#E2E8F0] p-3">
          <span className="text-xs text-[#CBD5E1]">暂无数据</span>
        </div>
      </div>
    );
  }

  // 列名：优先配置的 columns，否则取所有行 key 的并集（保持出现顺序）
  let columns = section.columns?.filter(Boolean);
  if (!columns || columns.length === 0) {
    const seen = new Set<string>();
    columns = [];
    for (const row of rows) {
      if (typeof row === 'object' && row !== null) {
        for (const key of Object.keys(row as object)) {
          if (!seen.has(key)) {
            seen.add(key);
            columns.push(key);
          }
        }
      }
    }
  }

  return (
    <div>
      <SectionTitle icon={<TableOutlined />} title={section.title} />
      <div className="bg-white rounded-lg border border-[#E2E8F0] overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC]">
              {columns.map((col) => (
                <th key={col} className="text-left px-3 py-1.5 font-medium text-[#64748B] whitespace-nowrap">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              <tr key={idx} className="border-b border-[#E2E8F0]/50 last:border-0">
                {columns.map((col) => (
                  <td key={col} className="px-3 py-1.5 text-[#334155] align-top whitespace-nowrap">
                    {formatDisplay((row as Record<string, unknown>)?.[col]) || '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
