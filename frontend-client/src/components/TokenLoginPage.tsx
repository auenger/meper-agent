import { KeyOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Alert, Button, Form, Input, Typography } from 'antd'
import { useState } from 'react'

import { setUserToken } from '../api/client'
import { useAuthStore } from '../store/auth'

interface TokenValues {
  token: string
}

/**
 * Token 登录页 —— apikey 模式下，独立部署（非 iframe）时让用户输入通用 token。
 * 提交后 setUserToken 存内存，触发外层重新渲染进入 ClientApp。
 */
export function TokenLoginPage({ onSuccess }: { onSuccess: () => void }) {
  const theme = useAuthStore((state) => state.theme)
  const toggleTheme = useAuthStore((state) => state.toggleTheme)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (values: TokenValues) => {
    const token = values.token.trim()
    if (!token) {
      setError('请输入 Token')
      return
    }
    setSubmitting(true)
    setError(null)
    // 存内存，触发外层重渲染
    setUserToken(token)
    onSuccess()
    setSubmitting(false)
  }

  return (
    <main className="login-page">
      <Button
        className="theme-button"
        type="text"
        icon={theme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
        onClick={toggleTheme}
        aria-label="切换主题"
      />
      <section className="login-panel" aria-labelledby="token-login-title">
        <div className="login-brand">
          <img src="/AFLogo.png" alt="Agent Flow" />
          <div>
            <Typography.Title id="token-login-title" level={2}>
              Agent Flow
            </Typography.Title>
            <Typography.Text type="secondary">
              输入您的 Token 开始对话
            </Typography.Text>
          </div>
        </div>

        <Form<TokenValues>
          layout="vertical"
          requiredMark={false}
          onFinish={submit}
          size="large"
        >
          <Form.Item
            label="通用 Token"
            name="token"
            rules={[{ required: true, message: '请输入 Token' }]}
          >
            <Input.Password
              prefix={<KeyOutlined />}
              placeholder="meper_xxxxxxxx"
              autoComplete="off"
              autoFocus
            />
          </Form.Item>
          {error ? (
            <Alert className="login-error" type="error" showIcon message={error} />
          ) : null}
          <Button type="primary" htmlType="submit" block loading={submitting}>
            进入
          </Button>
        </Form>
        <Typography.Text className="login-footnote" type="secondary">
          Token 由管理员分配，用于标识您的身份
        </Typography.Text>
      </section>
    </main>
  )
}
