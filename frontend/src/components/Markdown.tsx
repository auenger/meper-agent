import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

/**
 * Markdown 渲染组件。
 *
 * 用于审批视图的 document 区块、以及任何需要把长文本（agent 报告等）
 * 渲染成结构化 Markdown 的场景。支持 GFM（表格、删除线、任务列表）
 * 和代码高亮。
 */
export interface MarkdownProps {
  content: string;
}

export const Markdown = memo(function Markdown({ content }: MarkdownProps) {
  return (
    <div className="prose prose-sm max-w-none prose-headings:text-[#0F172A] prose-p:text-[#334155] prose-a:text-[#6366F1] prose-code:text-[#C026D3] prose-code:bg-[#F1F5F9] prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-[#0F172A] prose-pre:text-[#E2E8F0]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          a: ({ node, ...props }) => (
            <a
              {...props}
              target={props.href?.startsWith('http') ? '_blank' : undefined}
              rel="noreferrer"
            />
          ),
        }}
      >
        {content ?? ''}
      </ReactMarkdown>
    </div>
  );
});
