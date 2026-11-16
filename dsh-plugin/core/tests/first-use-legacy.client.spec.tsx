// @vitest-environment jsdom
/// <reference types="vite/client" />
// 第 17 项：首次使用页能直接选用旧版复制过来的 Key，流程和手动填一样（拉模型 → 亲自选 → 试聊保存）。
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { FirstUseSetup } from '../src/client/FirstUseSetup.tsx'
import type { OnboardingSetupRequest } from '../src/onboarding.ts'

afterEach(() => { cleanup(); vi.useRealTimers() })

const legacyOptions = [
  { account: 'legacy:tencent', provider: 'tencent' as const, label: '腾讯云 TokenHub', baseUrl: 'https://tokenhub.tencentmaas.com/v1' },
  { account: 'legacy:bailian', provider: 'dashscope' as const, label: '阿里百炼', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
]

it('选旧版 Key：不用填 Key，拉模型和保存都带上旧版条目名、不带 Key；模型仍要亲自选', async () => {
  const loadModels = vi.fn(async () => [{ id: 'deepseek/deepseek-flash', name: 'deepseek/deepseek-flash' }])
  const complete = vi.fn(async () => {})
  const onDone = vi.fn()
  render(<FirstUseSetup loadModels={loadModels} complete={complete} onDone={onDone} debounceMs={0} legacyOptions={legacyOptions} />)
  fireEvent.click(screen.getByRole('button', { name: '用旧版的腾讯云 TokenHub' }))
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  expect(loadModels).toHaveBeenCalledWith({ provider: 'tencent', baseUrl: 'https://tokenhub.tencentmaas.com/v1', key: '', legacyAccount: 'legacy:tencent' })
  const radio = await screen.findByRole('radio', { name: 'deepseek/deepseek-flash' })
  expect((radio as HTMLInputElement).checked).toBe(false) // 默认不代选
  fireEvent.click(radio)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认' })) })
  expect(complete).toHaveBeenCalledWith({ provider: 'tencent', baseUrl: 'https://tokenhub.tencentmaas.com/v1', key: '', legacyAccount: 'legacy:tencent', model: 'deepseek/deepseek-flash' })
  expect(onDone).toHaveBeenCalled()
})

it('选了旧版 Key 后又自己填 Key 或换服务商：不再用旧版条目', async () => {
  const loadModels = vi.fn(async (_request: OnboardingSetupRequest) => [{ id: 'm', name: 'm' }])
  render(<FirstUseSetup loadModels={loadModels} complete={vi.fn(async () => {})} onDone={vi.fn()} debounceMs={0} legacyOptions={legacyOptions} />)
  fireEvent.click(screen.getByRole('button', { name: '用旧版的阿里百炼' }))
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  fireEvent.click(screen.getByRole('button', { name: '旧版 Key 不用了，自己填' }))
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'typed-key' } })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  const last = loadModels.mock.calls.at(-1)![0]
  expect(last.key).toBe('typed-key')
  expect(last.legacyAccount).toBeUndefined()
})

it('没有旧版 Key 时不显示这一块，原样手动填', () => {
  render(<FirstUseSetup loadModels={vi.fn(async () => [])} complete={vi.fn(async () => {})} onDone={vi.fn()} />)
  expect(screen.queryByText(/旧版复制过来/)).toBeNull()
  expect(screen.getByLabelText('API Key')).toBeTruthy()
})

it('选了旧版 Key 后点别的服务商：旧版条目作废，回到手填', async () => {
  const loadModels = vi.fn(async (_request: OnboardingSetupRequest) => [{ id: 'm', name: 'm' }])
  render(<FirstUseSetup loadModels={loadModels} complete={vi.fn(async () => {})} onDone={vi.fn()} debounceMs={0} legacyOptions={legacyOptions} />)
  fireEvent.click(screen.getByRole('button', { name: '用旧版的腾讯云 TokenHub' }))
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  fireEvent.click(screen.getByRole('button', { name: 'DeepSeek' }))
  expect(screen.getByLabelText('API Key')).toBeTruthy()
  expect(screen.getByRole('button', { name: '用旧版的腾讯云 TokenHub' }).getAttribute('aria-pressed')).toBe('false')
  loadModels.mockClear()
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'typed' } })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
  expect(loadModels).toHaveBeenCalledWith({ provider: 'deepseek', baseUrl: 'https://api.deepseek.com', key: 'typed' })
})
