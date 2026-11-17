// @vitest-environment jsdom
/// <reference types="vite/client" />
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
const modules = import.meta.glob('../src/client/FirstUseSetup.tsx', { eager: true }) as Record<string, Record<string, ComponentType<any>>>
afterEach(cleanup)
function setup(initial?: string) {
  const C = modules['../src/client/FirstUseSetup.tsx']!.FirstUseSetup!
  const requests: any[] = []
  render(<C initialProvider={initial} loadModels={async (r: any) => { requests.push(r); return [{ id: 'fixture-chat', name: 'Fixture Chat' }] }} complete={vi.fn(async () => {})} onDone={vi.fn()} debounceMs={5} />)
  return requests
}

it('两个腾讯各用自己的默认地址拉模型，切换时旧Key和模型清空', async () => {
  const requests = setup()
  fireEvent.click(screen.getByRole('button', { name: '腾讯云 TokenHub' }))
  expect(screen.getByText('默认接口：https://tokenhub.tencentmaas.com/v1')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fixture-tokenhub-key' } })
  await screen.findByRole('radio', { name: 'Fixture Chat' })
  expect(requests[0]).toMatchObject({ provider: 'tencent-tokenhub', baseUrl: 'https://tokenhub.tencentmaas.com/v1' })
  fireEvent.click(screen.getByRole('button', { name: '腾讯混元' }))
  expect((screen.getByLabelText('API Key') as HTMLInputElement).value).toBe('')
  expect(screen.queryByRole('radio', { name: 'Fixture Chat' })).toBeNull()
  expect(screen.getByText('默认接口：https://api.hunyuan.cloud.tencent.com/v1')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'fixture-hunyuan-key' } })
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]).toMatchObject({ provider: 'tencent', baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1' })
})

it('以前保存腾讯云配置时，重新打开引导显示已选腾讯混元', () => {
  setup('tencent')
  expect(screen.getByRole('button', { name: '腾讯混元' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByText('默认接口：https://api.hunyuan.cloud.tencent.com/v1')).toBeTruthy()
})
