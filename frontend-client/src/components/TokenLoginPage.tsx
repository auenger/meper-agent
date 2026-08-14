import { ExclamationCircleOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Button } from 'antd'

import { useAuthStore } from '../store/auth'

export type AuthErrorType = 'no_token' | 'token_invalid' | 'not_bound' | 'not_configured'

const ERROR_MESSAGES: Record<AuthErrorType, { title: string; desc: string }> = {
  no_token: {
    title: '无法获取你的身份',
    desc: '未能从接入方获取身份信息，请通过接入方系统的正常入口访问。如果你的身份信息已更新，刷新页面重试。',
  },
  token_invalid: {
    title: '身份已过期',
    desc: '你的登录凭证已失效，请返回接入方系统重新登录后再试。',
  },
  not_bound: {
    title: '未绑定平台授权',
    desc: '你的身份尚未绑定平台 MCP 授权，请在管理后台的「外部授权」页面完成凭证绑定后再使用。',
  },
  not_configured: {
    title: '服务未配置',
    desc: '接入方的身份验证服务尚未配置，请联系管理员检查 API Key 的 introspection 端点配置。',
  },
}

/**
 * 身份错误页 —— apikey 模式下的身份验证失败提示。
 *
 * 不再让用户手动输入 token（v3 方案中 token 由接入方通过 cookie 自动注入）。
 * 根据不同的失败原因显示对应的提示信息。
 */
export function TokenLoginPage({ errorType = 'no_token' }: { errorType?: AuthErrorType; onSuccess?: () => void }) {
  const theme = useAuthStore((state) => state.theme)
  const toggleTheme = useAuthStore((state) => state.toggleTheme)
  const isDark = theme === 'dark'
  const msg = ERROR_MESSAGES[errorType]

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 20, padding: 24,
      background: isDark ? '#0f172a' : '#f8fafc',
    }}>
      <Button
        type="text"
        icon={isDark ? <SunOutlined /> : <MoonOutlined />}
        onClick={toggleTheme}
        style={{ position: 'absolute', top: 16, right: 16 }}
      />
      <div style={{
        width: 56, height: 56, borderRadius: '50%',
        display: 'grid', placeItems: 'center',
        background: isDark ? '#7f1d1d33' : '#fef2f2',
        color: '#ef4444', fontSize: 28,
      }}>
        <ExclamationCircleOutlined />
      </div>
      <div style={{ textAlign: 'center', maxWidth: 360 }}>
        <h3 style={{
          margin: '0 0 8px', fontSize: 17, fontWeight: 600,
          color: isDark ? '#f1f5f9' : '#1e293b',
        }}>
          {msg.title}
        </h3>
        <p style={{
          margin: 0, fontSize: 13, lineHeight: 1.7,
          color: isDark ? '#94a3b8' : '#64748b',
        }}>
          {msg.desc}
        </p>
      </div>
      {errorType === 'no_token' && (
        <Button onClick={() => window.location.reload()} size="large">
          刷新重试
        </Button>
      )}
    </div>
  )
}
