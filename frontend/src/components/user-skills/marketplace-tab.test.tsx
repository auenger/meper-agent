/**
 * @vitest-environment jsdom
 *
 * MarketplaceTab 卡片渲染测试 — 验证用户技能卡含 安装/Fork/积分/加载次数，
 * 官方技能卡不含安装（公共技能随 Agent 绑定生效）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { render, screen, within, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp } from 'antd'

/* jsdom 缺 ResizeObserver（antd Paragraph ellipsis 依赖 rc-resize-observer） */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

/* ─── Mocks ─── */

// 可变角色（vi.hoisted 保证 mock 工厂可用；测试内改 mockRole 切换视角）
const { mockRole } = vi.hoisted(() => ({ mockRole: { value: 'operator' } }))

vi.mock('../../stores/auth-store', () => ({
  useAuthStore: (sel: (s: { user: { role: string } }) => unknown) =>
    sel({ user: { role: mockRole.value } }),
}))

vi.mock('../../services/user-skills-api', () => ({
  userSkillsApi: {
    marketplace: vi.fn().mockResolvedValue([
      {
        // Mongo 裸文档形态：_id 而非 id——服务层 normalizeId 负责归一化（回归守护）
        _id: 'tool_1', kind: 'official', name: 'currency_convert', description: '汇率换算',
        status: 'published', source: 'own', binding_enabled: true,
        author_name: '官方（王管理）', installed: false, is_own: false, my_vote: 0,
        score: 8, stats: { load_count: 30, up: 9, down: 1 },
        created_at: '', updated_at: '',
      },
      {
        _id: 'usk_1', kind: 'user', name: 'weekly_report', description: 'Use when writing weekly reports.',
        status: 'published', source: 'own', binding_enabled: true,
        author_name: '张三', installed: false, is_own: false, my_vote: 0,
        score: 4, stats: { load_count: 12, up: 5, down: 1 },
        created_at: '', updated_at: '',
      },
    ]),
    install: vi.fn(),
    uninstall: vi.fn(),
    fork: vi.fn(),
    vote: vi.fn(),
    reviewList: vi.fn().mockResolvedValue([]),
    review: vi.fn(),
  },
  userSkillKeys: {
    all: ['user-skills'],
    list: () => ['user-skills', 'list'],
    detail: (id: string) => ['user-skills', 'detail', id],
    memory: () => ['user-skills', 'memory'],
    marketplace: (q: string) => ['user-skills', 'marketplace', q],
    review: (s: string) => ['user-skills', 'review', s],
  },
}))

vi.mock('../../services/tools-api', () => ({
  toolsApi: { list: vi.fn(), remove: vi.fn() },
  toolKeys: { list: () => ['tools', 'list'], lists: () => ['tools', 'lists'] },
}))

vi.mock('../../services/agent-api', () => ({ agentKeys: { all: ['agents'] } }))
vi.mock('../skill-upload-modal', () => ({ default: () => null }))

import { MarketplaceTab } from './marketplace-tab'
import { ThemeProvider } from '../../contexts/ThemeContext'
import { MemoryRouter } from 'react-router-dom'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <AntApp>
          <QueryClientProvider client={qc}>
            <MarketplaceTab />
          </QueryClientProvider>
        </AntApp>
      </ThemeProvider>
    </MemoryRouter>,
  )
}

/** 从技能名元素向上找到卡片容器（class 含 rounded-xl 的网格项） */
function cardOf(name: string): HTMLElement {
  const el = screen.getByText(name)
  const card = el.closest('div.rounded-xl')
  expect(card).not.toBeNull()
  return card as HTMLElement
}

/* ─── Tests ─── */

describe('MarketplaceTab 卡片（§7.6 逻辑单市：官方与用户统一操作）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRole.value = 'operator'
  })
  afterEach(() => cleanup())

  it('官方技能卡：官方标识 + 真实创建者 + 积分/Fork，无安装（绑定即生效）', async () => {
    renderPage()
    expect(await screen.findByText('currency_convert')).toBeInTheDocument()
    const card = within(cardOf('currency_convert'))
    expect(card.getByText('官方')).toBeInTheDocument()
    expect(card.getByText(/by 官方（王管理）/)).toBeInTheDocument() // 真实创建者
    // 统计徽标：加粗数字 + 单位分体渲染
    expect(card.getByText('30')).toBeInTheDocument()
    expect(card.getByText('次加载')).toBeInTheDocument()
    expect(card.getByText('+8')).toBeInTheDocument()
    expect(card.getByText('积分')).toBeInTheDocument()
    // §8.2 投票入口移至对话内反馈条——市场只展示积分，不再有投票按钮
    expect(card.queryByLabelText('vote-up')).toBeNull()
    expect(card.queryByLabelText('vote-down')).toBeNull()
    // 官方绑定 Agent 即全员生效——无安装/卸载；admin 无 fork（可直接编辑），
    // 本测试角色为 operator（非 admin）故 fork 可见
    expect(card.queryByRole('button', { name: /安装/ })).toBeNull()
    expect(card.getByLabelText('fork')).toBeInTheDocument()
  })

  it('用户技能卡：含 安装 按钮、Fork、积分与加载次数', async () => {
    renderPage()
    expect(await screen.findByText('weekly_report')).toBeInTheDocument()
    const card = within(cardOf('weekly_report'))
    // 作者与统计徽标（12 次加载 / +4 积分）
    expect(card.getByText(/by 张三/)).toBeInTheDocument()
    expect(card.getByText('12')).toBeInTheDocument()
    expect(card.getByText('+4')).toBeInTheDocument() // up5 - down1
    // 安装按钮 + Fork（§8.2 投票入口已移至对话内，市场无投票按钮）
    expect(card.getByRole('button', { name: /安装/ })).toBeInTheDocument()
    expect(card.getByLabelText('fork')).toBeInTheDocument()
    expect(card.queryByLabelText('vote-up')).toBeNull()
    expect(card.queryByLabelText('vote-down')).toBeNull()
  })

  it('admin 视角：用户技能卡无安装（§7.6 无个人技能），fork=收录为官方', async () => {
    mockRole.value = 'developer'
    renderPage()
    expect(await screen.findByText('weekly_report')).toBeInTheDocument()
    const card = within(cardOf('weekly_report'))
    expect(card.queryByRole('button', { name: /安装/ })).toBeNull()
    expect(card.getByLabelText('fork')).toBeInTheDocument() // 收录为官方
    // 官方卡：admin 无 fork（可直接编辑）、无安装
    const official = within(cardOf('currency_convert'))
    expect(official.queryByLabelText('fork')).toBeNull()
    expect(official.queryByRole('button', { name: /安装/ })).toBeNull()
  })
})
