/**
 * Skills page — 统一技能中心（技能广场 / 我的技能 / 我的记忆）。
 *
 * - 技能广场：官方技能 + 用户发布技能合并展示（标注所有者），安装/投票/fork；
 *   管理员可创建/编辑/删除官方技能并审核用户发布（v6 §7/§8）
 * - 我的技能 / 我的记忆：个人资产管理（v6 §4/§6.5）
 */
import { useState } from 'react'
import { Tabs } from 'antd'
import { ShopOutlined, UserOutlined } from '@ant-design/icons'
import { MarketplaceTab } from '../components/user-skills/marketplace-tab'
import { MySkillsTab, MyMemoryTab } from '../components/user-skills/my-skills-tab'

export default function SkillsPage() {
  const [tab, setTab] = useState('marketplace')

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'marketplace',
            label: (
              <span>
                <ShopOutlined style={{ marginRight: 6 }} />
                技能广场
              </span>
            ),
            children: <MarketplaceTab />,
          },
          {
            key: 'mine',
            label: (
              <span>
                <UserOutlined style={{ marginRight: 6 }} />
                我的技能
              </span>
            ),
            children: <MySkillsTab />,
          },
          {
            key: 'memory',
            label: '我的记忆',
            children: <MyMemoryTab />,
          },
        ]}
      />
    </div>
  )
}
