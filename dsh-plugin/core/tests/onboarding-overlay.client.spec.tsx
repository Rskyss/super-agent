// @vitest-environment jsdom
import {expect,it} from 'vitest'
import {OnboardingUiStore} from '../src/client/OnboardingOverlay.tsx'
it('后台仍需配置时，完成回调不能绕过首次引导',()=>{const store=new OnboardingUiStore();const state={keychainEnabled:true,required:true,configured:false};store.applyHostState(state);store.markCompleted(state);expect(store.getSnapshot().required).toBe(true);expect(store.getSnapshot().modalOpen).toBe(true)})
