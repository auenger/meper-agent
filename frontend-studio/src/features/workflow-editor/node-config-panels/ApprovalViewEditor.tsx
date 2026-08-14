/**
 * ApprovalViewEditor — Human 节点"审批视图"配置编辑器（studio 深色版）。
 *
 * 让工作流设计者声明 Human 节点给审批人看什么内容、怎么展示。
 * 输出 config.view = { sections: ViewSection[] }，与 ApprovalView 渲染组件
 * 和后端 human.py 的原样透传逻辑一致。
 *
 * 复用 VariableSelector 让 source 字段支持变量选择（{{node.field}}）。
 *
 * 由 frontend（antd 浅色版）翻译而来：antd Input → 原生 input；antd Select → ui Select；
 * @ant-design/icons → lucide-react；配色浅 → 深。
 */
import { Plus, Trash2, ChevronUp, ChevronDown } from 'lucide-react';
import { Select } from '../../../components/ui';
import VariableSelector from '../VariableSelector';
import type { WorkflowNode } from '../../../services/workflows-api';
import type {
  ViewSection,
  FieldsSection,
  DocumentSection,
  FilesSection,
  TableSection,
  FieldItem,
} from '../../../components/approval-view/types';

interface Props {
  sections: ViewSection[];
  onChange: (sections: ViewSection[]) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}

const SECTION_TYPES: { label: string; value: ViewSection['type'] }[] = [
  { label: '字段卡片', value: 'fields' },
  { label: '文档预览', value: 'document' },
  { label: '文件列表', value: 'files' },
  { label: '表格', value: 'table' },
];

/** 原生 input：与 HumanNodeConfig 的输入框样式一致。 */
const inputCls =
  'w-full px-2 py-1.5 rounded border border-[#27272a] bg-[#121214] text-[#fafafa] text-sm focus:outline-none focus:border-[#8B5CF6]';

export default function ApprovalViewEditor({ sections, onChange, currentNodeId, allNodes }: Props) {
  const update = (idx: number, patch: Partial<ViewSection>) => {
    const next = sections.map((s, i) => (i === idx ? ({ ...s, ...patch } as ViewSection) : s));
    onChange(next);
  };
  const remove = (idx: number) => onChange(sections.filter((_, i) => i !== idx));
  const move = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= sections.length) return;
    const next = [...sections];
    [next[idx], next[target]] = [next[target], next[idx]];
    onChange(next);
  };
  const add = () => {
    onChange([
      ...sections,
      { type: 'fields', title: '', items: [{ label: '', source: '' }] } as FieldsSection,
    ]);
  };

  return (
    <div className="space-y-2">
      {sections.map((section, idx) => (
        <div key={idx} className="rounded-lg border border-[#27272a] bg-[#18181b] p-3 space-y-2">
          {/* 行头：类型 + 排序/删除 */}
          <div className="flex items-center gap-1.5">
            <Select
              className="flex-1"
              value={section.type}
              onChange={(val) => update(idx, { type: val as ViewSection['type'] })}
              options={SECTION_TYPES}
            />
            <button
              type="button"
              onClick={() => move(idx, -1)}
              disabled={idx === 0}
              className="p-1.5 text-[#71717a] hover:text-[#fafafa] disabled:opacity-30 cursor-pointer"
              title="上移"
            >
              <ChevronUp className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={() => move(idx, 1)}
              disabled={idx === sections.length - 1}
              className="p-1.5 text-[#71717a] hover:text-[#fafafa] disabled:opacity-30 cursor-pointer"
              title="下移"
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={() => remove(idx)}
              className="p-1.5 text-[#71717a] hover:text-rose-400 cursor-pointer"
              title="删除区块"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
          <div>
            <label className="block text-xs text-[#71717a] mb-1">区块标题（可选）</label>
            <input
              className={inputCls}
              value={section.title ?? ''}
              onChange={(e) => update(idx, { title: e.target.value })}
              placeholder="如：申请信息"
            />
          </div>
          <SectionFields
            section={section}
            idx={idx}
            update={update}
            currentNodeId={currentNodeId}
            allNodes={allNodes}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg border border-dashed border-[#3f3f46] text-xs text-[#71717a] hover:text-[#a78bfa] hover:border-[#8B5CF6] hover:bg-[#8B5CF6]/10 transition-colors cursor-pointer"
      >
        <Plus className="w-3.5 h-3.5" /> 添加审批内容区块
      </button>
    </div>
  );
}

/** 按 section.type 渲染不同的配置表单。 */
function SectionFields({
  section,
  idx,
  update,
  currentNodeId,
  allNodes,
}: {
  section: ViewSection;
  idx: number;
  update: (idx: number, patch: Partial<ViewSection>) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}) {
  switch (section.type) {
    case 'fields':
      return (
        <FieldsConfig
          section={section as FieldsSection}
          idx={idx}
          update={update}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
        />
      );
    case 'document':
      return (
        <DocumentConfig
          section={section as DocumentSection}
          idx={idx}
          update={update}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
        />
      );
    case 'files':
      return (
        <FilesConfig
          section={section as FilesSection}
          idx={idx}
          update={update}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
        />
      );
    case 'table':
      return (
        <TableConfig
          section={section as TableSection}
          idx={idx}
          update={update}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
        />
      );
    default:
      return null;
  }
}

function SourcePicker({
  value,
  onChange,
  currentNodeId,
  allNodes,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
  placeholder?: string;
}) {
  return (
    <VariableSelector
      value={value}
      onChange={onChange}
      currentNodeId={currentNodeId}
      allNodes={allNodes}
      textarea={false}
      rows={1}
      placeholder={placeholder}
    />
  );
}

function FieldsConfig({
  section,
  idx,
  update,
  currentNodeId,
  allNodes,
}: {
  section: FieldsSection;
  idx: number;
  update: (idx: number, patch: Partial<ViewSection>) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}) {
  const items = section.items ?? [];
  const updateItem = (i: number, patch: Partial<FieldItem>) => {
    const items2 = items.map((it, j) => (j === i ? { ...it, ...patch } : it));
    update(idx, { items: items2 } as Partial<FieldsSection>);
  };
  const removeItem = (i: number) =>
    update(idx, { items: items.filter((_, j) => j !== i) } as Partial<FieldsSection>);
  const addItem = () =>
    update(idx, { items: [...items, { label: '', source: '' }] } as Partial<FieldsSection>);

  return (
    <div className="space-y-1.5">
      <label className="block text-xs text-[#71717a] mb-1">字段项</label>
      {items.map((item, i) => (
        <div key={i} className="rounded border border-[#27272a] bg-[#09090b] p-2 space-y-1.5">
          <div className="flex gap-1.5">
            <input
              className={`${inputCls} flex-1`}
              value={item.label ?? ''}
              onChange={(e) => updateItem(i, { label: e.target.value })}
              placeholder="标签，如：申请金额"
            />
            <button
              type="button"
              onClick={() => removeItem(i)}
              className="p-1.5 text-[#71717a] hover:text-rose-400 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
          <SourcePicker
            value={item.source ?? ''}
            onChange={(v) => updateItem(i, { source: v })}
            currentNodeId={currentNodeId}
            allNodes={allNodes}
            placeholder="{{node.field}}"
          />
        </div>
      ))}
      <button
        type="button"
        onClick={addItem}
        className="w-full flex items-center justify-center gap-1 px-2 py-1 rounded text-xs text-[#71717a] hover:text-[#fafafa] hover:bg-[#27272a] cursor-pointer"
      >
        <Plus className="w-3 h-3" /> 添加字段
      </button>
    </div>
  );
}

function DocumentConfig({
  section,
  idx,
  update,
  currentNodeId,
  allNodes,
}: {
  section: DocumentSection;
  idx: number;
  update: (idx: number, patch: Partial<ViewSection>) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}) {
  return (
    <div>
      <label className="block text-xs text-[#71717a] mb-1">内容来源（长文本/Markdown）</label>
      <SourcePicker
        value={section.source ?? ''}
        onChange={(v) => update(idx, { source: v } as Partial<DocumentSection>)}
        currentNodeId={currentNodeId}
        allNodes={allNodes}
        placeholder="{{agent_id.report}}"
      />
    </div>
  );
}

function FilesConfig({
  section,
  idx,
  update,
  currentNodeId,
  allNodes,
}: {
  section: FilesSection;
  idx: number;
  update: (idx: number, patch: Partial<ViewSection>) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}) {
  return (
    <div>
      <label className="block text-xs text-[#71717a] mb-1">文件来源</label>
      <SourcePicker
        value={section.source ?? ''}
        onChange={(v) => update(idx, { source: v } as Partial<FilesSection>)}
        currentNodeId={currentNodeId}
        allNodes={allNodes}
        placeholder="{{agent_id.files}}"
      />
      <div className="text-[10px] text-[#71717a] mt-1">当前展示整任务的产物文件（按 Task 自动拉取）</div>
    </div>
  );
}

function TableConfig({
  section,
  idx,
  update,
  currentNodeId,
  allNodes,
}: {
  section: TableSection;
  idx: number;
  update: (idx: number, patch: Partial<ViewSection>) => void;
  currentNodeId: string;
  allNodes: WorkflowNode[];
}) {
  return (
    <div className="space-y-1.5">
      <div>
        <label className="block text-xs text-[#71717a] mb-1">数据来源（列表变量）</label>
        <SourcePicker
          value={section.source ?? ''}
          onChange={(v) => update(idx, { source: v } as Partial<TableSection>)}
          currentNodeId={currentNodeId}
          allNodes={allNodes}
          placeholder="{{node.list_field}}"
        />
      </div>
      <div>
        <label className="block text-xs text-[#71717a] mb-1">展示列（逗号分隔，可空）</label>
        <input
          className={inputCls}
          value={(section.columns ?? []).join(', ')}
          onChange={(e) =>
            update(idx, {
              columns: e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            } as Partial<TableSection>)
          }
          placeholder="如：方案, 成本, 周期（留空展示全部）"
        />
      </div>
    </div>
  );
}
