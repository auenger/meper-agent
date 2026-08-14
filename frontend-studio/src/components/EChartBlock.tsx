/**
 * EChartBlock —— 统一的 echarts 渲染组件。
 *
 * 两条路径汇流到这里：
 *   1. render_chart 工具返回的 ```echarts fenced block（tool_result 区走 Markdown）
 *   2. agent 直接在回复里手写的 ```echarts fenced block（正文走 Markdown）
 * Markdown.tsx 的 code renderer 识别 language=echarts → 取 children 文本 → <EChartBlock option={text} />
 *
 * 关键设计（option 是 LLM 高风险输入）：
 *   - 分层校验（lib/echarts-option.ts）：JSON.parse + 安全校验 + 最小契约（挡静默白图）
 *   - 流式安全：括号未配平时显「渲染中…」，完整后自动出图
 *   - 失败可操作：分类占位（「重新生成」按钮为可选 prop，不传即隐藏）
 *   - 主题：读最近带 theme-dark class 的祖先（App 根节点是 theme-${theme}），
 *     MutationObserver 监听 class 变化，无需 React context 透传
 *   - 自适应：echarts-for-react 内置 autoresize，固定默认高 320px
 *   - 按需 import（echarts/core + 各 Chart + CanvasRenderer）控制体积
 */

import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import ReactECharts from 'echarts-for-react';
import { Loader2, TriangleAlert } from 'lucide-react';
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react';

import {
  type ValidationResult,
  isValidationSuccess,
  validateChartOptionString,
} from '../lib/echarts-option';

// 按需注册（控制 bundle 体积，仅注册本平台支持的图表类型）
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  GridComponent,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

// studio 色板：主色 indigo/blue 系，暗色对齐 zinc 色阶（#27272a 边框 / #18181b 底），
// 亮色对齐 slate。仅轴线/文字色按主题翻转。
const CHART_COLORS = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899'];
echarts.registerTheme('studio-light', {
  color: CHART_COLORS,
  backgroundColor: 'transparent',
  textStyle: { color: '#475569' },
  title: { textStyle: { color: '#1e293b' }, subtextStyle: { color: '#64748b' } },
  legend: { textStyle: { color: '#475569' } },
  xAxis: { axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
  yAxis: { axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
});
echarts.registerTheme('studio-dark', {
  color: CHART_COLORS,
  backgroundColor: 'transparent',
  textStyle: { color: '#a1a1aa' },
  title: { textStyle: { color: '#fafafa' }, subtextStyle: { color: '#a1a1aa' } },
  legend: { textStyle: { color: '#a1a1aa' } },
  xAxis: { axisLine: { lineStyle: { color: '#27272a' } }, axisLabel: { color: '#71717a' }, splitLine: { lineStyle: { color: '#27272a' } } },
  yAxis: { axisLine: { lineStyle: { color: '#27272a' } }, axisLabel: { color: '#71717a' }, splitLine: { lineStyle: { color: '#27272a' } } },
});

export interface EChartBlockProps {
  /** option：对象（已校验）或字符串（流式 / fenced block 原文）。 */
  option: object | string;
  className?: string;
  /** 「重新生成」回调（不传则隐藏按钮）。 */
  onRegenerate?: () => void;
}

/**
 * 读最近带 theme-dark class 的祖先 —— App 根节点是 `theme-${theme}`。
 * 用 MutationObserver 监听祖先 class 变化，主题切换无需重挂载。
 */
function useDarkMode(ref: RefObject<HTMLElement | null>): boolean {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    const findRoot = (): HTMLElement => {
      let node: HTMLElement | null = ref.current;
      while (node) {
        if (node.classList?.contains('theme-dark') || node.classList?.contains('theme-light')) return node;
        node = node.parentElement;
      }
      return document.body;
    };
    const apply = () => {
      const root = findRoot();
      setDark(!!root?.classList.contains('theme-dark'));
    };
    apply();

    const targets: HTMLElement[] = [];
    const root = findRoot();
    if (root) targets.push(root);
    if (root !== document.body) targets.push(document.body);
    const observer = new MutationObserver(apply);
    for (const t of targets) {
      observer.observe(t, { attributes: true, attributeFilter: ['class'] });
    }
    return () => observer.disconnect();
  }, [ref]);

  return dark;
}

function EChartBlockImpl({ option, className, onRegenerate }: EChartBlockProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dark = useDarkMode(containerRef);

  const raw = typeof option === 'string' ? option : JSON.stringify(option);
  const result = useMemo<ValidationResult>(
    () => validateChartOptionString(raw),
    [raw],
  );

  if (isValidationSuccess(result)) {
    return (
      <div
        ref={containerRef}
        className={`chart-block my-3 overflow-hidden rounded-xl border ${dark ? 'border-[#27272a] bg-[#18181b]' : 'border-slate-200 bg-white'} ${className ?? ''}`}
      >
        <ReactECharts
          echarts={echarts}
          option={result.option}
          theme={dark ? 'studio-dark' : 'studio-light'}
          notMerge
          lazyUpdate
          style={{ height: 320, width: '100%' }}
          opts={{ renderer: 'canvas' }}
        />
      </div>
    );
  }

  // 失败 / 流式态（type guard 后此处 result 收窄为 ValidationFailure）
  const failure = result;
  const streaming = failure.reason === 'streaming';
  return (
    <div
      ref={containerRef}
      className={`chart-block chart-block-error my-3 flex items-center gap-3 rounded-xl border px-4 py-3 text-sm ${dark ? 'border-[#27272a] bg-[#18181b] text-[#a1a1aa]' : 'border-slate-200 bg-slate-50 text-slate-500'} ${className ?? ''}`}
    >
      {streaming ? (
        <Loader2 className="h-4 w-4 animate-spin opacity-60" />
      ) : (
        <TriangleAlert className="h-4 w-4 opacity-70" />
      )}
      <span className="flex-1">{failure.message}</span>
      {!streaming && onRegenerate && (
        <button
          type="button"
          onClick={onRegenerate}
          className={`shrink-0 rounded-md border px-2 py-1 text-xs font-medium transition-colors ${dark ? 'border-[#3f3f46] text-[#d4d4d8] hover:bg-[#27272a]' : 'border-slate-300 text-slate-600 hover:bg-white'}`}
        >
          重新生成
        </button>
      )}
    </div>
  );
}

export const EChartBlock = EChartBlockImpl;
