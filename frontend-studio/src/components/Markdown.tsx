import { isValidElement, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

import { EChartBlock } from './EChartBlock';

/**
 * Streaming-aware Markdown renderer for chat bubbles.
 *
 * Both user and agent messages are rendered as Markdown so pasted formatted
 * text and AI answers (code blocks, lists, tables, headings) render nicely.
 *
 * The tricky part of rendering *partial* content during a stream is that an
 * incomplete fenced code block (```) will swallow everything after it until
 * the fence closes. `closeIncompleteBlocks` patches that up before parsing
 * so the bubble stays readable while tokens are still arriving.
 *
 * ```echarts fenced blocks (render_chart tool or hand-written by the agent)
 * render as charts via EChartBlock; a partial option shows a
 * "rendering…" placeholder until the JSON is complete.
 */

export interface MarkdownProps {
  /** Raw markdown text. May be partial (mid-stream). */
  content: string;
}

export const Markdown = memo(function Markdown({ content }: MarkdownProps) {
  const safe = closeIncompleteBlocks(content ?? '');
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          // Open external links in a new tab; keep relative links as-is.
          a: ({ node, ...props }) => (
            <a {...props} target={props.href?.startsWith('http') ? '_blank' : undefined} rel="noreferrer" />
          ),
          // ```echarts / ```chart fenced block → chart (EChartBlock handles
          // streaming-partial and malformed options internally).
          code: ({ className, children, ...props }) => {
            const language = /language-([^\s]+)/.exec(className ?? '')?.[1];
            if (language === 'echarts' || language === 'chart') {
              return <EChartBlock option={String(children).replace(/\n$/, '')} />;
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
          // Unwrap <pre> around chart blocks (EChartBlock brings its own container).
          pre: ({ children, ...props }) =>
            isValidElement(children) && children.type === EChartBlock ? (
              children
            ) : (
              <pre {...props}>{children}</pre>
            ),
        }}
      >
        {safe}
      </ReactMarkdown>
    </div>
  );
});

/**
 * Patch a partial markdown string so it renders cleanly mid-stream.
 *
 * Counts unmatched ``` fences: if there is an odd number of fences the last
 * block was never closed, so we append a closing fence. This is the single
 * most common cause of "the whole rest of the message turned into a code
 * block" while streaming.
 */
function closeIncompleteBlocks(src: string): string {
  // Tally fenced-code-block delimiters (lines that start with ```).
  // A fence may be followed by a language tag, e.g. ```ts.
  const fenceMatches = src.match(/^`{3,}/gm);
  const openFences = fenceMatches ? fenceMatches.length % 2 : 0;
  let out = src;
  if (openFences) {
    out += '\n```\n';
  }
  return out;
}
