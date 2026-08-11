import { App as AntApp, ConfigProvider, Spin, theme as antTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { XProvider } from '@ant-design/x'
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { AUTH_MODE, bootstrapAuth, getUserToken } from './api/client'
import { ClientApp } from './ClientApp'
import { LoginPage } from './components/LoginPage'
import { TokenLoginPage } from './components/TokenLoginPage'
import { useParentToken } from './hooks/use-parent-token'
import { useAuthStore } from './store/auth'
import './styles.css'

function Root() {
  const initialized = useAuthStore((state) => state.initialized)
  const accessToken = useAuthStore((state) => state.accessToken)
  const extUserName = useAuthStore((state) => state.extUserName)
  const theme = useAuthStore((state) => state.theme)
  // TokenLoginPage 提交后 setUserToken，用此 state 触发重渲染切到主界面
  const [tokenEntered, setTokenEntered] = useState(false)
  // iframe 嵌入时向宿主页请求终端用户 token（callback 模式）；apikey 模式才生效。
  useParentToken()

  useEffect(() => {
    void bootstrapAuth()
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])

  // 判定主界面显示条件：
  // - jwt 模式：有 accessToken
  // - apikey 模式：有 userToken（getUserToken 非空 或 tokenEntered 标记）
  const hasUserToken = getUserToken() !== null || tokenEntered
  const canEnterApp = AUTH_MODE === 'apikey' ? hasUserToken : !!accessToken

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
            // apikey 模式但无 userToken → token 输入页
            <TokenLoginPage onSuccess={() => setTokenEntered(true)} />
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
