import { AudioOutlined, EditOutlined, SoundOutlined } from '@ant-design/icons'
import { Alert, Button, Dropdown, Space, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { useState } from 'react'

import { useVoiceConversation } from '../../hooks/voice/useVoiceConversation'
import { usePttSpaceTrigger } from '../../hooks/voice/usePttSpaceTrigger'
import type { VoiceConversationState } from '../../hooks/voice/useVoiceConversation'
import { StateOrb } from './StateOrb'

interface VoiceComposerProps {
  agentId?: string
  sessionId?: string
  onExit: () => void
  onTranscriptFinal: (text: string) => void
  onTurnStarted: (message: { request_id?: string; session_id?: string }) => void
  onAgentDelta: (text: string) => void
  onTurnEnd: (message: { request_id?: string; session_id?: string }) => void
  onInterruptRequest: (question: string) => void
  /** 组件内部已展示错误文本；此回调仅供上层做副作用（埋点等），可选 */
  onError?: (message: string) => void
}

const STATE_COPY: Record<VoiceConversationState, string> = {
  idle: '按住麦克风说话，松开自动发送',
  listening: '正在聆听，松开发送',
  thinking: 'Agent 正在思考，按住可打断',
  speaking: 'Agent 正在回复，按住可打断',
  error: '语音服务出现异常',
}

/** 语音输入面板：替代文本 Sender（ChatVoiceComposer 的 antd 翻译）。 */
export function VoiceComposer(props: VoiceComposerProps) {
  const voice = useVoiceConversation({
    enabled: true,
    agentId: props.agentId,
    sessionId: props.sessionId,
    onTranscriptFinal: props.onTranscriptFinal,
    onTurnStarted: props.onTurnStarted,
    onAgentDelta: props.onAgentDelta,
    onTurnEnd: props.onTurnEnd,
    onInterruptRequest: props.onInterruptRequest,
    onError: props.onError,
  })
  const canInterrupt = voice.voiceState === 'thinking' || voice.voiceState === 'speaking'

  usePttSpaceTrigger(
    voice.connected && !!props.agentId && !!props.sessionId,
    () => void voice.press(),
    voice.release,
  )

  const deviceMenu: MenuProps['items'] = [
    {
      key: 'input-group',
      label: '麦克风',
      children: voice.audioDevices.inputDevices.map((device) => ({
        key: `in:${device.deviceId}`,
        label: device.label,
      })),
    },
    {
      key: 'output-group',
      label: '扬声器',
      disabled: !voice.audioDevices.outputSelectionSupported,
      children: [
        { key: 'out:', label: '跟随系统' },
        ...voice.audioDevices.outputDevices.map((device) => ({
          key: `out:${device.deviceId}`,
          label: device.label,
        })),
      ],
    },
  ]
  const onDeviceMenuClick: MenuProps['onClick'] = ({ key }) => {
    if (key.startsWith('in:')) voice.audioDevices.selectInputDevice(key.slice(3))
    else if (key.startsWith('out:')) voice.audioDevices.selectOutputDevice(key.slice(4))
  }

  return (
    <section className="voice-composer" aria-label="语音输入">
      <span className="voice-composer-topline" aria-hidden />
      <div className="voice-composer-header">
        <Space size={6}>
          <span className={`voice-status-dot ${voice.connected ? 'ok' : 'pending'}`} aria-hidden />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {voice.connected ? '语音已连接' : '正在连接语音服务…'}
          </Typography.Text>
        </Space>
        <Space size={4}>
          <Dropdown menu={{ items: deviceMenu, onClick: onDeviceMenuClick }} trigger={['click']}>
            <Button type="text" size="small" icon={<AudioOutlined />} aria-label="选择音频设备" />
          </Dropdown>
          <Tooltip title="切换到文字输入">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={props.onExit}
              aria-label="切换到文字输入"
            >
              文字
            </Button>
          </Tooltip>
        </Space>
      </div>

      <div className="voice-composer-body">
        <StateOrb state={voice.voiceState} compact />
        <Typography.Text
          className="voice-state-copy"
          type="secondary"
          style={{ fontSize: 12 }}
          aria-live="polite"
        >
          {voice.partial ? `“${voice.partial}”` : STATE_COPY[voice.voiceState]}
        </Typography.Text>

        <Space size={8} style={{ marginTop: 8 }}>
          <Button
            type="primary"
            danger={voice.recording}
            shape="round"
            icon={<SoundOutlined />}
            disabled={!voice.connected || !props.agentId || !props.sessionId}
            onPointerDown={(event) => {
              event.preventDefault()
              try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* ignore */ }
              void voice.press()
            }}
            onPointerUp={voice.release}
            onPointerCancel={voice.release}
            aria-label={voice.recording ? '松开结束语音输入' : '按住说话'}
          >
            {voice.recording ? '松开发送' : '按住说话'}
          </Button>
          {canInterrupt && (
            <Button shape="round" onClick={voice.interrupt} aria-label="打断 Agent 回复">
              打断
            </Button>
          )}
        </Space>

        {voice.error && <VoiceErrorBanner error={voice.error} />}
      </div>
    </section>
  )
}

/** 把原始报错拆成「主行 + 可展开的技术详情」：
 *  形如「语音识别连接失败：火山 ASR 错误 45000000: {"error": …}」的错误，
 *  JSON 串之前的部分是人话 headline，完整原文放详情折叠。 */
function splitVoiceError(raw: string): { headline: string; detail?: string } {
  const text = raw.trim()
  const jsonAt = text.search(/\{["\s]/)
  if (jsonAt > 8) {
    const headline = text.slice(0, jsonAt).replace(/[\s:：]+$/, '')
    if (headline) return { headline, detail: text }
  }
  if (text.length <= 100) return { headline: text }
  return { headline: `${text.slice(0, 80)}…`, detail: text }
}

function VoiceErrorBanner({ error }: { error: string }) {
  const [expanded, setExpanded] = useState(false)
  const { headline, detail } = splitVoiceError(error)
  return (
    <Alert
      className="voice-error-banner"
      type="error"
      showIcon
      role="alert"
      message={<span className="voice-error-headline">{headline}</span>}
      description={
        detail && expanded ? (
          <pre className="voice-error-detail">{detail}</pre>
        ) : undefined
      }
      action={
        detail ? (
          <Button size="small" type="text" onClick={() => setExpanded((v) => !v)}>
            {expanded ? '收起' : '详情'}
          </Button>
        ) : undefined
      }
    />
  )
}
