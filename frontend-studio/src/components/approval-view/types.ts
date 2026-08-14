/**
 * 审批视图（Approval View）类型定义。
 *
 * 工作流设计者在 Human 节点 config.view 里声明"给审批人看什么内容、怎么展示"，
 * 前端拿到 human_context.view（本配置）+ task.variables（变量快照）后，
 * 在 JS 侧解析 {{node.field}} 引用并渲染成结构化的审批材料。
 *
 * 后端 HumanNodeExecutor 把 view 配置原样透传（不解析变量），因此这里的前端类型
 * 与后端 human.py 的 Config 示例一一对应。
 */

/** 单个字段项（fields 区块用）。 */
export interface FieldItem {
  /** 字段标签（给审批人看的名称） */
  label: string;
  /** 变量引用，如 {{input.amount}} 或 {{agent_risk.risk_level}} */
  source: string;
}

/** 区块类型：决定内容如何呈现。 */
export type SectionType = 'fields' | 'document' | 'files' | 'table';

/** 审批视图区块的基础字段。 */
interface BaseSection {
  /** 区块类型 */
  type: SectionType;
  /** 区块标题（可选，不填则不显示标题栏） */
  title?: string;
}

/** 字段卡片：结构化键值对，适合金额/状态/评分等。 */
export interface FieldsSection extends BaseSection {
  type: 'fields';
  items: FieldItem[];
}

/** 文档预览：长文本 + Markdown 渲染，适合 agent 产出的报告。 */
export interface DocumentSection extends BaseSection {
  type: 'document';
  /** 变量引用，解析后应为一个字符串（Markdown 文本） */
  source: string;
}

/** 文件预览：文件列表。 */
export interface FilesSection extends BaseSection {
  type: 'files';
  /**
   * 变量引用，解析后为文件数组（如 agent 节点的 files 输出）。
   * 当前实现：忽略 source，直接展示整任务的产物文件（复用 TaskOutputFiles）。
   * source 仅作配置留痕，后续可扩展为按节点过滤。
   */
  source: string;
}

/** 表格：多行结构化数据，适合检索结果/候选方案对比。 */
export interface TableSection extends BaseSection {
  type: 'table';
  /** 变量引用，解析后应为 list（每行是一个 dict） */
  source: string;
  /** 可选：要展示的列名（即行 dict 的 key）。不填则展示所有 key。 */
  columns?: string[];
}

/** 所有区块类型的联合。 */
export type ViewSection = FieldsSection | DocumentSection | FilesSection | TableSection;

/** 审批视图配置（Human 节点 config.view）。 */
export interface ApprovalViewConfig {
  sections: ViewSection[];
}
