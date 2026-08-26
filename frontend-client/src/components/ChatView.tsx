import {
  AudioOutlined,
  FileOutlined,
  MenuOutlined,
  PaperClipOutlined,
  QuestionCircleOutlined,
  PlusOutlined,
  LikeOutlined,
  LikeFilled,
} from '@ant-design/icons'
import { Attachments, Bubble, Sender } from '@ant-design/x'
import {
  Alert,
  App,
  Button,
  Empty,
  Image,
  Input,
  Modal,
  Result,
  Skeleton,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import type { UploadFile } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { useChat } from '../hooks/use-chat'
import { fetchVoiceStatus } from '../api/voice'
import type { AgentSummary } from '../types'
import { GeneratedFiles } from './GeneratedFiles'
import { MessageContent } from './MessageContent'
import { sessionFeedback, voteMessage, type SessionFeedbackItem } from '../api/chat'
import { ClarificationFormCard } from './clarification-form-card'
import { VoiceComposer } from './voice/VoiceComposer'

interface ChatViewProps {
  agent: AgentSummary | null
  agentLoading?: boolean
  sessionsLoading?: boolean
  sessionId: string | null
  onOpenNavigation: () => void
  onCreateSession: () => void
  /** 会话内容变化(如发完消息后端生成标题)时回调,用于刷新侧边栏会话列表 */
  onSessionChanged?: () => void
  /** 语音对话后端新建会话时回传新 session_id,用于切换当前会话 */
  onSessionSwitched?: (sessionId: string) => void
}

export function ChatView({
  agent,
  agentLoading,
  sessionsLoading,
  sessionId,
  onOpenNavigation,
  onCreateSession,
  onSessionChanged,
  onSessionSwitched,
}: ChatViewProps) {
  const { message } = App.useApp()
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [filesOpen, setFilesOpen] = useState(false)
  const [filesRefreshKey, setFilesRefreshKey] = useState(0)
  const [clarificationAnswer, setClarificationAnswer] = useState('')
  const [pendingPreview, setPendingPreview] = useState<{
    name: string
    url: string
  } | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const {
    messages,
    loading,
    running,
    hitl,
    loadError,
    send,
    cancel,
    answerClarification,
    voiceAppendUserMessage,
    voiceBeginAssistantTurn,
    voiceAppendDelta,
    voiceFinishTurn,
    voiceAbort,
  } = useChat(
    agent?.id ?? null,
    sessionId,
    () => setFilesRefreshKey((value) => value + 1),
    onSessionChanged,
  )

  // ── 语音输入模式 ─────────────────────────────────────────────────
  const [inputMode, setInputMode] = useState<'text' | 'voice'>('text')
  const [voiceConfigured, setVoiceConfigured] = useState(false)
  const voiceAvailable = voiceConfigured && agent?.voiceEnabled === true

  useEffect(() => {
    let cancelled = false
    fetchVoiceStatus()
      .then((status) => {
        if (!cancelled) setVoiceConfigured(status.configured === true)
      })
      .catch(() => {
        // 探测失败按未配置处理（不弹错，麦克风按钮不出现即可）
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 语音可用性变化后退出语音模式（如切换到未开启语音的 Agent）
  useEffect(() => {
    if (inputMode === 'voice' && !voiceAvailable) {
      voiceAbort()
      setInputMode('text')
    }
  }, [inputMode, voiceAvailable, voiceAbort])

  // ── 自动滚动跟随 ────────────────────────────────────────────────
  // 历史背景:.message-viewport(外层 overflow:auto)与 Bubble.List 内部
  // 的 scroll-box 形成双层滚动,Bubble.List 的 autoScroll 拿不到正确的贴底
  // 判定。这里关闭 Bubble.List 的 autoScroll,改由外层 viewport 自实现:
  // 用户贴底时跟随流式输出,上滚超过阈值就不打扰,切会话时滚到最新一条。
  const viewportRef = useRef<HTMLElement | null>(null)
  const isPinnedRef = useRef(true) // 用户是否处于「贴底」状态
  const PIN_THRESHOLD = 120 // 距底部多少 px 内视为贴底

  const scrollToBottom = (behavior: ScrollBehavior = 'auto') => {
    const el = viewportRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior })
  }

  const handleViewportScroll = () => {
    const el = viewportRef.current
    if (!el) return
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    isPinnedRef.current = distanceToBottom < PIN_THRESHOLD
  }

  // 内容尺寸变化时(流式增量、图片加载完成等),贴底则跟随
  useEffect(() => {
    const el = viewportRef.current
    if (!el) return
    // 观察直接子节点(.ant-bubble-list / skeleton / empty)的高度变化
    const observer = new ResizeObserver(() => {
      if (isPinnedRef.current) scrollToBottom('auto')
    })
    observer.observe(el)
    // 子树挂载/卸载也要重新观察
    const mo = new MutationObserver(() => {
      if (isPinnedRef.current) scrollToBottom('auto')
    })
    mo.observe(el, { childList: true, subtree: true })
    return () => {
      observer.disconnect()
      mo.disconnect()
    }
  }, [sessionId])

  // 切换会话:重置贴底并滚到最新一条
  useEffect(() => {
    isPinnedRef.current = true
    // 等首屏渲染完成后再滚
    requestAnimationFrame(() => scrollToBottom('auto'))
  }, [sessionId])

  // 消息变化(新增消息、流式增量、状态变更):贴底则跟随
  /* ── 消息级反馈（§8.2 v2）：会话各轮投票态，流结束/切会话刷新 ── */
  const [feedbackTick, setFeedbackTick] = useState(0)
  const [feedbackMap, setFeedbackMap] = useState<Record<string, SessionFeedbackItem>>({})
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    sessionFeedback(sessionId)
      .then((items) => {
        if (cancelled) return
        const map: Record<string, SessionFeedbackItem> = {}
        for (const it of items) map[it.request_id] = it
        setFeedbackMap(map)
      })
      .catch(() => { /* 端点不可用/无权限（apikey 模式）——静默降级 */ })
    return () => { cancelled = true }
  }, [sessionId, feedbackTick, loading])

  const handleVoteMessage = async (requestId: string, value: 1 | -1) => {
    if (!sessionId) return
    const current = feedbackMap[requestId]?.value ?? 0
    if (current === value) return
    try {
      await voteMessage(sessionId, requestId, value)
      setFeedbackMap((prev) => ({
        ...prev,
        [requestId]: { ...(prev[requestId] ?? { request_id: requestId, value: 0, skills: [] }), value },
      }))
    } catch {
      // 投票失败静默——不打断对话
    }
  }

  const lastMessage = messages[messages.length - 1]
  const lastMessageKey = lastMessage
    ? `${lastMessage.id}:${lastMessage.status}:${lastMessage.content.length}`
    : ''
  useEffect(() => {
    if (isPinnedRef.current) scrollToBottom('auto')
  }, [lastMessageKey, messages.length, loading])
  // ── 自动滚动跟随 END ────────────────────────────────────────────

  const attachmentItems = useMemo<UploadFile[]>(
    () =>
      files.map((file, index) => ({
        uid: `${index}:${file.name}:${file.lastModified}`,
        name: file.name,
        size: file.size,
        type: file.type,
        status: 'done',
      })),
    [files],
  )

  const addFiles = (next: File[]) => {
    setFiles((current) => {
      const merged = [...current]
      for (const file of next) {
        if (
          !merged.some(
            (item) =>
              item.name === file.name &&
              item.size === file.size &&
              item.lastModified === file.lastModified,
          )
        ) {
          merged.push(file)
        }
      }
      if (merged.length > 8) void message.warning('单次最多上传 8 个文件')
      return merged.slice(0, 8)
    })
  }

  const submit = (value: string, displayValue?: string) => {
    void send(value, files, displayValue)
    setInput('')
    setFiles([])
  }

  const submitClarification = (answer: string) => {
    const value = answer.trim()
    if (!value) return
    void answerClarification(value)
    setClarificationAnswer('')
  }

  if (!agent) {
    return (
      <main className="chat-view">
        <header className="chat-header">
          <Button
            className="mobile-nav-button"
            type="text"
            icon={<MenuOutlined />}
            onClick={onOpenNavigation}
          />
        </header>
        {agentLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', flex: 1 }}>
            <Spin size="large" />
          </div>
        ) : (
          <Result
            status="info"
            title="暂无可用 Agent"
            subTitle="请联系管理员为当前公司分配可调用的 Agent。"
          />
        )}
      </main>
    )
  }

  return (
    <main className="chat-view">
      <header className="chat-header">
        <Button
          className="mobile-nav-button"
          type="text"
          icon={<MenuOutlined />}
          onClick={onOpenNavigation}
          aria-label="打开对话列表"
        />
        <div className="chat-agent-title">
          <strong>{agent.name}</strong>
          <Typography.Text type="secondary" ellipsis>
            {agent.description || '智能 Agent'}
          </Typography.Text>
        </div>
        <Button
          type="text"
          icon={<FileOutlined />}
          disabled={!sessionId}
          onClick={() => setFilesOpen(true)}
        >
          <span className="desktop-only-label">会话文件</span>
        </Button>
      </header>

      {!sessionId ? (
        agentLoading || sessionsLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', flex: 1 }}>
            <Spin size="large" />
          </div>
        ) : (
        <div className="chat-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有对话，开始创建一个吧"
          >
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreateSession}>
              快速创建对话
            </Button>
          </Empty>
        </div>
        )
      ) : (
        <>
          <section
            className="message-viewport"
            aria-live="polite"
            ref={viewportRef}
            onScroll={handleViewportScroll}
          >
            {loading ? (
              <div className="message-loading">
                <Skeleton active avatar paragraph={{ rows: 3 }} />
                <Skeleton active paragraph={{ rows: 2 }} />
              </div>
            ) : loadError ? (
              <Alert type="error" showIcon message="历史消息加载失败" description={loadError} />
            ) : messages.length === 0 ? (
              <div className="welcome-state">
                <div className="welcome-mark">{agent.name.slice(0, 1)}</div>
                {agent.welcomeMessage ? (
                  <div className="welcome-message">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {agent.welcomeMessage}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <>
                    <Typography.Title level={2}>和 {agent.name} 开始对话</Typography.Title>
                    <Typography.Paragraph type="secondary">
                      可以直接提问，也可以上传图片、文档或数据文件。
                    </Typography.Paragraph>
                  </>
                )}
              </div>
            ) : (
              <Bubble.List
                autoScroll={false}
                items={messages.map((chatMessage) => ({
                  key: chatMessage.id,
                  role: chatMessage.role === 'user' ? 'user' : 'ai',
                  status:
                    chatMessage.status === 'loading'
                      ? 'updating'
                      : chatMessage.status,
                  content: (
                    <div>
                      <MessageContent message={chatMessage} sessionId={sessionId} />
                      {chatMessage.role === 'assistant' &&
                        chatMessage.status !== 'loading' &&
                        chatMessage.requestId && (
                          <div className="flex items-center gap-1 pt-1">
                            <Button
                              aria-label="msg-vote-up"
                              type="text"
                              size="small"
                              title={feedbackMap[chatMessage.requestId]?.value === 1 ? '已点过赞' : '这轮回复有帮助'}
                              onClick={() => void handleVoteMessage(chatMessage.requestId!, 1)}
                              className={
                                feedbackMap[chatMessage.requestId]?.value === 1
                                  ? '!text-blue-500'
                                  : '!text-gray-400 hover:!text-blue-500'
                              }
                              icon={
                                feedbackMap[chatMessage.requestId]?.value === 1 ? (
                                  <LikeFilled />
                                ) : (
                                  <LikeOutlined />
                                )
                              }
                            />
                            <Button
                              aria-label="msg-vote-down"
                              type="text"
                              size="small"
                              title={feedbackMap[chatMessage.requestId]?.value === -1 ? '已点过踩' : '这轮回复没帮助'}
                              onClick={() => void handleVoteMessage(chatMessage.requestId!, -1)}
                              className={
                                feedbackMap[chatMessage.requestId]?.value === -1
                                  ? '!text-red-500'
                                  : '!text-gray-400 hover:!text-red-500'
                              }
                              icon={<LikeOutlined style={{ transform: 'rotate(180deg)' }} />}
                            />
                          </div>
                        )}
                    </div>
                  ),
                  streaming: chatMessage.status === 'loading',
                }))}
                role={{
                  ai: {
                    placement: 'start',
                    variant: 'borderless',
                  },
                  user: {
                    placement: 'end',
                    variant: 'filled',
                  },
                }}
              />
            )}
          </section>

          <footer className="composer-dock">
            {hitl ? (
              hitl.kind === 'workflow_confirmation' ? (
                <Alert
                  className="hitl-card"
                  type="warning"
                  showIcon
                  icon={<QuestionCircleOutlined />}
                  message="工作流确认"
                  description={
                    <div className="clarification-content">
                      <div className="workflow-confirmation-row">
                        <Typography.Text type="secondary">工作流：</Typography.Text>
                        <Typography.Text strong>
                          {hitl.workflowName || '（未命名）'}
                        </Typography.Text>
                      </div>
                      {hitl.workflowDescription ? (
                        <Typography.Text type="secondary">
                          {hitl.workflowDescription}
                        </Typography.Text>
                      ) : null}
                      {hitl.inputPreview &&
                      Object.keys(hitl.inputPreview).length > 0 ? (
                        <div className="workflow-confirmation-params">
                          <Typography.Text type="secondary">输入参数：</Typography.Text>
                          <div className="workflow-confirmation-params-list">
                            {Object.entries(hitl.inputPreview).map(([key, value]) => (
                              <div key={key} className="workflow-confirmation-param">
                                <span className="workflow-confirmation-param-key">
                                  {key}
                                </span>
                                <span className="workflow-confirmation-param-value">
                                  {typeof value === 'string'
                                    ? value
                                    : JSON.stringify(value)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      <div className="clarification-options">
                        <Button
                          danger
                          type="primary"
                          loading={running}
                          onClick={() => submitClarification('拒绝')}
                        >
                          拒绝
                        </Button>
                        <Button
                          type="primary"
                          loading={running}
                          onClick={() =>
                            submitClarification(
                              `确认执行 ${hitl.workflowName ?? ''}`.trim(),
                            )
                          }
                        >
                          确认执行
                        </Button>
                      </div>
                    </div>
                  }
                />
              ) : (
                <Alert
                  className="hitl-card"
                  type="warning"
                  showIcon
                  icon={<QuestionCircleOutlined />}
                  message={
                    hitl.clarificationType === 'risk_confirmation'
                      ? '操作确认'
                      : 'Agent 需要补充信息'
                  }
                  description={
                    <div className="clarification-content">
                      <Typography.Text>{hitl.question}</Typography.Text>
                      {hitl.context ? (
                        <Typography.Text type="secondary">{hitl.context}</Typography.Text>
                      ) : null}
                      {hitl.fields && hitl.fields.length > 0 ? (
                        <ClarificationFormCard
                          question=""
                          context={hitl.context}
                          fields={hitl.fields}
                          answered={false}
                          result={undefined}
                          onSubmit={(jsonStr) => submitClarification(jsonStr)}
                        />
                      ) : (
                      <>
                      {hitl.options.length > 0 ? (
                        <div className="clarification-options">
                          {hitl.options.map((option) => (
                            <Button
                              key={option}
                              onClick={() => submitClarification(option)}
                            >
                              {option}
                            </Button>
                          ))}
                        </div>
                      ) : null}
                      {hitl.clarificationType === 'risk_confirmation' ? (
                        <div className="clarification-options">
                          <Button onClick={() => submitClarification('取消')}>取消</Button>
                          <Button
                            danger
                            type="primary"
                            onClick={() => submitClarification('确认')}
                          >
                            确认执行
                          </Button>
                        </div>
                      ) : null}
                      <div className="clarification-input">
                        <Input
                          value={clarificationAnswer}
                          onChange={(event) => setClarificationAnswer(event.target.value)}
                          onPressEnter={() => submitClarification(clarificationAnswer)}
                          placeholder="输入你的回答"
                          disabled={running}
                        />
                        <Button
                          type="primary"
                          disabled={!clarificationAnswer.trim() || running}
                          onClick={() => submitClarification(clarificationAnswer)}
                        >
                          发送
                        </Button>
                      </div>
                      </>
                      )}
                    </div>
                  }
                />
              )
            ) : null}
            {files.length > 0 ? (
              <Attachments
                className="composer-attachments"
                items={attachmentItems}
                overflow="scrollX"
                onPreview={(item) => {
                  const selected = files.find(
                    (file, index) =>
                      `${index}:${file.name}:${file.lastModified}` === item.uid,
                  )
                  if (!selected || !selected.type.startsWith('image/')) {
                    void message.info('该文件将在发送后支持下载')
                    return
                  }
                  setPendingPreview({
                    name: selected.name,
                    url: URL.createObjectURL(selected),
                  })
                }}
                onRemove={(file) => {
                  setFiles((current) =>
                    current.filter(
                      (item, index) =>
                        `${index}:${item.name}:${item.lastModified}` !== file.uid,
                    ),
                  )
                }}
              />
            ) : null}
            {agent.recommendedItems && agent.recommendedItems.length > 0 ? (
              <div className="composer-quick-actions">
                {agent.recommendedItems.map((item, index) => (
                  <Button
                    key={`${index}:${item.label}`}
                    className="quick-action"
                    disabled={running || Boolean(hitl)}
                    onClick={() => submit(item.prompt || item.label, item.label)}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
            ) : null}
            {inputMode === 'voice' && voiceAvailable ? (
              <VoiceComposer
                agentId={agent?.id}
                sessionId={sessionId ?? undefined}
                onExit={() => {
                  voiceAbort()
                  setInputMode('text')
                }}
                onTranscriptFinal={voiceAppendUserMessage}
                onTurnStarted={(info) => {
                  voiceBeginAssistantTurn()
                  // voice.start 未带 session_id 时后端新建会话并在此回传
                  if (info.session_id && info.session_id !== sessionId) {
                    onSessionSwitched?.(info.session_id)
                  }
                }}
                onAgentDelta={voiceAppendDelta}
                onTurnEnd={voiceFinishTurn}
                onInterruptRequest={voiceAppendDelta}
              />
            ) : (
            <Sender
              value={input}
              onChange={setInput}
              onSubmit={(message) => submit(message)}
              onCancel={cancel}
              onPasteFile={(pasted) => addFiles(Array.from(pasted))}
              loading={running}
              disabled={Boolean(hitl)}
              placeholder={hitl ? '请先处理待确认操作' : '输入消息，Enter 发送'}
              autoSize={{ minRows: 1, maxRows: 6 }}
              prefix={
                <>
                  {voiceAvailable && (
                    <Tooltip title="切换到语音输入">
                      <Button
                        type="text"
                        icon={<AudioOutlined />}
                        onClick={() => setInputMode('voice')}
                        aria-label="切换到语音输入"
                        disabled={running || Boolean(hitl)}
                      />
                    </Tooltip>
                  )}
                  <Button
                    type="text"
                    icon={<PaperClipOutlined />}
                    onClick={() => fileInputRef.current?.click()}
                    aria-label="上传附件"
                    disabled={running || Boolean(hitl)}
                  />
                </>
              }
            />
            )}
            <input
              ref={fileInputRef}
              className="hidden-file-input"
              type="file"
              multiple
              onChange={(event) => {
                addFiles(Array.from(event.target.files ?? []))
                event.currentTarget.value = ''
              }}
            />
            <Typography.Text className="composer-note" type="secondary">
              Agent 输出可能有误，请核对关键结果。写操作执行前会再次确认。
            </Typography.Text>
          </footer>
        </>
      )}

      <GeneratedFiles
        sessionId={sessionId}
        open={filesOpen}
        onClose={() => setFilesOpen(false)}
        refreshKey={filesRefreshKey}
      />
      <Modal
        title={pendingPreview?.name}
        open={Boolean(pendingPreview)}
        footer={null}
        onCancel={() => {
          if (pendingPreview?.url) URL.revokeObjectURL(pendingPreview.url)
          setPendingPreview(null)
        }}
        destroyOnHidden
      >
        {pendingPreview ? (
          <Image
            className="file-preview-image"
            src={pendingPreview.url}
            alt={pendingPreview.name}
            preview={false}
          />
        ) : null}
      </Modal>
    </main>
  )
}
