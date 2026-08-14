import { App as AntApp, ConfigProvider, Spin, theme as antTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { XProvider } from '@ant-design/x'
import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'

import { AUTH_MODE, bootstrapAuth, getUserToken, setAuthErrorHandler } from './api/client'
import { ClientApp } from './ClientApp'
import { LoginPage } from './components/LoginPage'
import { TokenLoginPage, type AuthErrorType } from './components/TokenLoginPage'
import { useParentToken } from './hooks/use-parent-token'
import { useAuthStore } from './store/auth'
import './styles.css'

/** 后端 401 错误码 → 前端错误类型映射。 */
function mapAuthError(code: string): AuthErrorType {
  if (code === 'EXT_USER_NOT_BOUND') return 'not_bound'
  if (code === 'INTROSPECT_URL_NOT_CONFIGURED') return 'not_configured'
  if (code === 'EXT_USER_TOKEN_MISSING') return 'no_token'
  return 'token_invalid' // EXT_USER_TOKEN_INVALID / 其他
}

function Root() {
  const initialized = useAuthStore((state) => state.initialized)
  const accessToken = useAuthStore((state) => state.accessToken)
  const theme = useAuthStore((state) => state.theme)
  const authError = useAuthStore((state) => state.authError)
  const setAuthError = useAuthStore((state) => state.setAuthError)
  // iframe 嵌入时向宿主页请求终端用户 token（apikey 模式才生效）。
  useParentToken()

  // 注册 401 错误回调 → 写入 auth store 触发重渲染
  useEffect(() => {
    setAuthErrorHandler((code) => setAuthError(code))
    return () => setAuthErrorHandler(null)
  }, [setAuthError])

  useEffect(() => {
    void bootstrapAuth()
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])

  // 判定主界面显示条件：
  // - jwt 模式：有 accessToken
  // - apikey 模式：有 userToken（cookie 注入或 postMessage 注入）且无身份错误
  const hasUserToken = getUserToken() !== null
  const canEnterApp = AUTH_MODE === 'apikey'
    ? (hasUserToken && !authError)
    : !!accessToken

  // apikey 模式下的错误类型
  const errorType: AuthErrorType = authError
    ? mapAuthError(authError)
    : 'no_token'

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm:
          theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        cssVar: { prefix: 'meper' },
        token: {
          colorPrimary: '#315f92',
          colorInfo: '#315f92',
          borderRadius: 12,
          fontFamily:
            '"SF Pro Text", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
        },
      }}
    >
      <XProvider>
        <AntApp>
          {!initialized ? (
            <div className="boot-screen">
              <Spin size="large" />
            </div>
          ) : canEnterApp ? (
            <ClientApp />
          ) : AUTH_MODE === 'apikey' ? (
            <TokenLoginPage errorType={errorType} />
          ) : (
            <LoginPage />
          )}
        </AntApp>
      </XProvider>
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
