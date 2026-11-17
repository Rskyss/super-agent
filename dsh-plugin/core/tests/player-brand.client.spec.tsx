// @vitest-environment jsdom
/// <reference types="vite/client" />
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { ComponentType } from 'react'

const modules = import.meta.glob('../src/client/{Branding,FirstUseSetup,BlocksSettingsSection,chat-navigation}.ts*', { eager: true }) as Record<string, Record<string, any>>
afterEach(cleanup)

it('新版左栏品牌向用户显示超级玩家', () => {
  const C = modules['../src/client/Branding.tsx']!.SuperagentBrandName as ComponentType
  render(<C />)
  expect(screen.getByText('超级玩家')).toBeTruthy()
  expect(screen.queryByText('超级助手')).toBeNull()
})

it('首次配置的标题和说明使用新版名字', () => {
  const C = modules['../src/client/FirstUseSetup.tsx']!.FirstUseSetup as ComponentType<any>
  render(<C loadModels={vi.fn(async () => [])} complete={vi.fn(async () => {})} onDone={vi.fn()} />)
  expect(screen.getByRole('heading', { name: '配置超级玩家模型' })).toBeTruthy()
  expect(document.body.textContent).toContain('超级玩家使用的模型')
  expect(document.body.textContent).not.toContain('超级助手')
})

it('积木库按06a显示超级玩家在用的模型', async () => {
  const C = modules['../src/client/BlocksSettingsSection.tsx']!.BlocksSettingsSection as ComponentType<any>
  render(<C close={vi.fn()} dispatcherState={async () => ({ ok: true, value: { status: 'missing', tool: '', label: '', model: '', reason: '', verified: false } })} modelCatalog={async () => ({ ok: true, value: { groups: [{id:'test',name:'测试服务商',models:[{id:'test-model',name:'走查模型'}]}], default: {provider:'test',model:'test-model'} } })} startDispatcherChat={vi.fn(async () => {})} />)
  expect(await screen.findByText('超级玩家在用')).toBeTruthy()
  expect(document.body.textContent).not.toContain('超级助手')
})

it('旧名字的配置错误仍能识别，显示给用户时转换成新版名字', () => {
  const format = modules['../src/client/chat-navigation.ts']!.formatChatNavigationError
  expect(format(new Error('请先完成超级助手首次模型配置'))).toBe('没建上新对话：请先完成超级玩家首次模型配置')
  expect(format(new Error('请先完成超级玩家首次模型配置'))).toBe('没建上新对话：请先完成超级玩家首次模型配置')
  expect(format(new Error('读取超级助手配置状态失败'))).toBe('没建上新对话：超级玩家模型配置尚未就绪，请先完成配置或点重试')
})
