import {Context} from '@deepseek-ai/cordis'
import {credentialRef} from '@deepseek-ai/dsh-credentials'
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it,vi} from 'vitest'
vi.mock('@deepseek-ai/dsh-atomic-write',async original=>{const m=await original<typeof import('@deepseek-ai/dsh-atomic-write')>();return{...m,writeFileAtomic:vi.fn(m.writeFileAtomic)}})
import {writeFileAtomic} from '@deepseek-ai/dsh-atomic-write'
import {MacCredentialProvider,KEYCHAIN_META_FILENAME} from '../src/credentials-provider.ts'
import {readOnboardingStateOnHost} from '../src/onboarding-host.ts'
const dirs:string[]=[];afterEach(()=>{vi.unstubAllEnvs();vi.mocked(writeFileAtomic).mockReset();for(const d of dirs.splice(0))rmSync(d,{recursive:true,force:true})})
function setup(){const dir=mkdtempSync(join(tmpdir(),'sa-credential-transaction-'));dirs.push(dir);const values=new Map<string,string>();const store={service:'com.aisoup.superagent.test.unit',get:vi.fn(async(k:string)=>values.get(k)),set:vi.fn(async(k:string,v:string)=>{values.set(k,v)}),delete:vi.fn(async(k:string)=>{values.delete(k)})};const provider=new MacCredentialProvider(new Context(),{dshHome:dir,store});return{dir,values,store,provider}}
it('地址元数据损坏时失败不能先覆盖原钥匙串值',async()=>{const t=setup();t.values.set('ref:SUPERAGENT_TEST_KEY','old-fixture');writeFileSync(join(t.dir,KEYCHAIN_META_FILENAME),'{broken');await expect(t.provider.set(credentialRef('SUPERAGENT_TEST_KEY'),'new-fixture')).rejects.toThrow();expect(t.values.get('ref:SUPERAGENT_TEST_KEY')).toBe('old-fixture')})
it('新条目的元数据落盘失败，不能留下半保存的钥匙串值',async()=>{const t=setup();vi.mocked(writeFileAtomic).mockRejectedValueOnce(Error('fixture disk full'));await expect(t.provider.set(credentialRef('SUPERAGENT_TEST_KEY'),'new-fixture')).rejects.toThrow();expect(t.values.has('ref:SUPERAGENT_TEST_KEY')).toBe(false)})
it('凭据子入口与网关分别加载时仍识别同一个正式钥匙串提供者',async()=>{vi.stubEnv('SUPERAGENT_KEYCHAIN','1');const t=setup();const built=await import('../lib/types/credentials-provider.js');const provider=new built.MacCredentialProvider(new Context(),{dshHome:t.dir,store:t.store});const ctx={get:(key:string)=>key==='credentials'?provider:key==='agentDefaultModel'?{currentSelection:()=>({provider:'',model:''})}:key==='settings'?{describe:()=>[]}:undefined};const state=await readOnboardingStateOnHost(ctx as any);expect(state.error).toBeUndefined();expect(state.required).toBe(true)})
it('第17项：只能读 legacy: 开头的旧版复制条目，别的条目（含正式凭据）一律拒绝',async()=>{const{values,store,provider}=setup();values.set('legacy:tencent','fixture-legacy');values.set('ref:SUPERAGENT_ASSISTANT_API_KEY','fixture-ref');expect(await provider.readLegacyKey('legacy:tencent')).toBe('fixture-legacy');store.get.mockClear();for(const bad of ['ref:SUPERAGENT_ASSISTANT_API_KEY','tencent','','legacy'])await expect(provider.readLegacyKey(bad)).rejects.toThrow();expect(store.get).not.toHaveBeenCalled()})
