/**
 * ClarificationFormCard — ask_clarification 向导模式渲染卡片（studio 深色版）。
 *
 * 当 ask_clarification 提供 fields 时，渲染为向导：一次展示一个问题，
 * 答完跳下一个，支持返回上一题修改，全部答完才提交。
 *
 * - 提供 options（3-5 个推荐）→ 显示推荐选项按钮 + 底部自由输入框
 * - 未提供 options（如密码、纯自由输入）→ 只显示输入框
 *
 * 提交时把所有答案序列化为 JSON 字符串（如 {"audience":"管理层"}）
 * 通过 onSubmit 回调传出，复用 resume 传输通道（answer: string）。
 * 已答态：把 result（JSON 串）解析后渲染为键值对摘要。
 */
import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Send, CheckCircle, Loader2 } from 'lucide-react';

/** 后端 ClarificationField 的前端镜像。 */
export interface ClarificationField {
  name: string;
  label: string;
  field_type: 'text' | 'number' | 'boolean' | 'select';
  required: boolean;
  options?: string[] | null;
  default?: string | number | boolean | null;
  description?: string | null;
}

/** 忽略标记文案——与后端 MessageService.DISMISSED_RESULT_TEXT 保持一致
 *  （dismiss 持久化的合成 tool_result 内容）。 */
export const DISMISSED_CLARIFICATION_TEXT = '(用户已忽略此问题)';

interface Props {
  question: string;
  context?: string | null;
  fields: ClarificationField[];
  answered: boolean;
  result?: string;
  onSubmit: (jsonStr: string) => void;
  /** 忽略此问题（不回答，恢复自由输入）。 */
  onDismiss?: () => void;
}

export function ClarificationFormCard({
  question,
  context,
  fields,
  answered,
  result,
  onSubmit,
  onDismiss,
}: Props) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.default !== null && f.default !== undefined) init[f.name] = f.default;
    }
    return init;
  });

  const total = fields.length;
  const current = fields[step] ?? fields[0];
  const isLast = step >= total - 1;
  const options = (current?.options ?? []) as string[];

  const commitAnswer = (val: unknown) => {
    setAnswers((prev) => ({ ...prev, [current.name]: val }));
  };

  const handleInputChange = (val: string) => {
    if (val === '') commitAnswer(undefined);
    else commitAnswer(current.field_type === 'number' ? Number(val) : val);
  };

  const handleNext = () => {
    const val = answers[current.name];
    const empty = val === undefined || val === '' || val === null;
    if (current.required && current.field_type !== 'boolean' && empty) return;
    if (isLast) {
      const out: Record<string, unknown> = {};
      for (const f of fields) {
        const v = answers[f.name];
        if (v === undefined || v === '' || v === null) continue;
        out[f.name] = v;
      }
      onSubmit(JSON.stringify(out));
    } else {
      setStep((s) => Math.min(s + 1, total - 1));
    }
  };

  const handlePrev = () => setStep((s) => Math.max(s - 1, 0));

  const currentVal = answers[current.name];
  const isAnswered =
    current.field_type === 'boolean'
      ? currentVal !== undefined
      : currentVal !== undefined && currentVal !== '' && currentVal !== null;

  const freeInputValue =
    typeof currentVal === 'string' && currentVal !== '' && !options.includes(currentVal)
      ? currentVal
      : currentVal !== undefined && current.field_type === 'number'
        ? String(currentVal)
        : '';

  const answeredValues = useMemo<Record<string, unknown>>(() => {
    if (!result) return {};
    try {
      const parsed = JSON.parse(result);
      return typeof parsed === 'object' && parsed !== null ? parsed : {};
    } catch {
      return {};
    }
  }, [result]);

  const renderValue = (f: ClarificationField, val: unknown): string => {
    if (val === undefined || val === null) return '—';
    if (f.field_type === 'text' && f.name.toLowerCase().includes('key')) {
      const s = String(val);
      if (s.length <= 8) return '••••';
      return `${s.slice(0, 3)}••••${s.slice(-3)}`;
    }
    if (f.field_type === 'boolean') return val ? '是' : '否';
    return String(val);
  };

  if (answered) {
    // 已忽略：不解析字段摘要（忽略标记不是 JSON），给一行提示即可。
    if (result === DISMISSED_CLARIFICATION_TEXT) {
      return (
        <div className="rounded-xl rounded-tl-none border border-indigo-500/30 bg-indigo-500/10 overflow-hidden font-sans shadow-sm opacity-70">
          <div className="flex items-center gap-2 px-3.5 py-2.5">
            <CheckCircle className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
            <span className="text-xs font-semibold text-indigo-400 truncate">澄清提问</span>
            <span className="text-[10px] text-zinc-500 opacity-70">已忽略</span>
          </div>
          <div className="mx-2.5 mb-2.5 rounded-lg bg-[#121214] border border-[#27272a] px-3.5 py-3">
            <div className="text-xs text-[#a1a1aa]">
              已忽略此问题——可直接在输入框重新描述需求或上传文件
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="rounded-xl rounded-tl-none border border-indigo-500/30 bg-indigo-500/10 overflow-hidden font-sans shadow-sm">
        <div className="flex items-center gap-2 px-3.5 py-2.5">
          <CheckCircle className="w-3.5 h-3.5 shrink-0 text-emerald-400" />
          <span className="text-xs font-semibold text-indigo-400 truncate">澄清提问</span>
          <span className="text-[10px] text-emerald-400 opacity-70">已回答</span>
        </div>
        <div className="mx-2.5 mb-2.5 rounded-lg bg-[#121214] border border-[#27272a] px-3.5 py-3">
          <div className="flex items-start gap-2.5">
            <span className="text-sm mt-0.5 select-none text-indigo-400">📋</span>
            <div className="flex-1 min-w-0">
              {question && (
                <div className="text-[13px] font-medium text-[#fafafa] whitespace-pre-wrap leading-relaxed mb-2">
                  {question}
                </div>
              )}
              <div className="space-y-1">
                {fields.map((f) => (
                  <div key={f.name} className="flex items-baseline gap-2 text-xs">
                    <span className="text-indigo-400 shrink-0">{f.label}:</span>
                    <span className="text-[#fafafa] font-medium break-all">
                      {renderValue(f, answeredValues[f.name])}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl rounded-tl-none border border-indigo-500/30 bg-indigo-500/10 overflow-hidden font-sans shadow-sm">
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        <Loader2 className="w-3.5 h-3.5 shrink-0 text-amber-400 animate-spin" />
        <span className="text-xs font-semibold text-indigo-400 truncate">澄清提问</span>
        <span className="text-[10px] text-amber-400 opacity-70">等待回答</span>
      </div>
      <div className="mx-2.5 mb-2.5 rounded-lg bg-[#121214] border border-[#27272a] px-3.5 py-3">
        <div className="flex items-start gap-2.5">
          <span className="text-sm mt-0.5 select-none text-indigo-400">📋</span>
          <div className="flex-1 min-w-0">
            {question && <div className="text-xs text-[#a1a1aa] mb-1.5">{question}</div>}
            {context && (
              <div className="text-[11px] text-[#a1a1aa] mb-2 whitespace-pre-wrap">{context}</div>
            )}

            <div className="text-[11px] text-[#a1a1aa] mb-2">
              第 {step + 1} / {total} 题
            </div>

            <div className="text-[13px] text-[#fafafa] font-medium mb-2">
              {current.label}
              {current.required && current.field_type !== 'boolean' && (
                <span className="text-rose-400 ml-0.5">*</span>
              )}
            </div>
            {current.description && (
              <p className="text-[11px] text-[#a1a1aa] mb-2">{current.description}</p>
            )}

            {current.field_type === 'boolean' && (
              <div className="flex gap-2 mb-3">
                <button
                  type="button"
                  onClick={() => commitAnswer(true)}
                  className={`px-3 py-1.5 rounded-lg text-xs border transition ${
                    currentVal === true
                      ? 'bg-indigo-500/20 border-indigo-500/60 text-[#fafafa] font-medium'
                      : 'bg-[#09090b] border-[#3f3f46] text-[#fafafa] hover:bg-[#27272a]'
                  }`}
                >
                  是
                </button>
                <button
                  type="button"
                  onClick={() => commitAnswer(false)}
                  className={`px-3 py-1.5 rounded-lg text-xs border transition ${
                    currentVal === false
                      ? 'bg-indigo-500/20 border-indigo-500/60 text-[#fafafa] font-medium'
                      : 'bg-[#09090b] border-[#3f3f46] text-[#fafafa] hover:bg-[#27272a]'
                  }`}
                >
                  否
                </button>
              </div>
            )}

            {current.field_type !== 'boolean' && options.length > 0 && (
              <div className="flex flex-col gap-1.5 mb-2">
                {options.map((opt) => {
                  const selected = currentVal === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => commitAnswer(opt)}
                      className={`text-left px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                        selected
                          ? 'bg-indigo-500/20 border-indigo-500/60 text-indigo-200 font-medium'
                          : 'bg-[#09090b] border-[#3f3f46] text-[#d4d4d8] hover:border-indigo-500/50 hover:bg-indigo-500/10 cursor-pointer'
                      }`}
                    >
                      {opt}
                      {selected && <span className="ml-1.5 text-indigo-400">✓</span>}
                    </button>
                  );
                })}
              </div>
            )}

            {current.field_type !== 'boolean' && (
              <input
                type={current.field_type === 'number' ? 'number' : 'text'}
                value={freeInputValue}
                placeholder="或在此输入自定义内容…"
                onChange={(e) => handleInputChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (current.required && !isAnswered) return;
                    handleNext();
                  }
                }}
                className="w-full px-2.5 py-1.5 rounded-lg text-xs bg-[#09090b] border border-[#3f3f46] text-[#e4e4e7] placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500/60 focus:ring-1 focus:ring-indigo-500/30 transition-colors"
              />
            )}

            <div className="flex justify-between items-center mt-3">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={handlePrev}
                  disabled={step === 0}
                  className="inline-flex items-center gap-1 text-xs text-[#d4d4d8] hover:text-[#e4e4e7] disabled:opacity-30 disabled:cursor-not-allowed transition"
                >
                  <ChevronLeft size={13} />
                  上一题
                </button>
                {onDismiss && (
                  <button
                    type="button"
                    onClick={onDismiss}
                    className="text-xs text-[#71717a] hover:text-[#a1a1aa] transition cursor-pointer"
                  >
                    忽略此问题
                  </button>
                )}
              </div>
              <button
                type="button"
                onClick={handleNext}
                disabled={current.required && current.field_type !== 'boolean' && !isAnswered}
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition cursor-pointer"
              >
                {isLast ? (
                  <>
                    <Send size={13} />
                    提交
                  </>
                ) : (
                  <>
                    下一题
                    <ChevronRight size={13} />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
