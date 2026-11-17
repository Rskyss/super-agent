// 第六批 6-5 通用页：钥匙串里存着的 Key（只列名字、可删）、任务和对话记录（位置、大小）。
import {Context} from '@deepseek-ai/cordis'
import {credentialRef} from '@deepseek-ai/dsh-credentials'
import {mkdtempSync,rmSync,writeFileSync,readFileSync,mkdirSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {afterEach,expect,it,vi} from 'vitest'
import {MacCredentialProvider} from '../src/credentials-provider.ts'
import {listStoredKeys,deleteStoredKey,recordsInfo} from '../src/settings-host.ts'

const dirs:string[]=[]
afterEach(()=>{vi.unstubAllEnvs();for(const d of dirs.splice(0))rmSync(d,{recursive:true,force:true})})
function setup(){
  const dir=mkdtempSync(join(tmpdir(),'sa-settings-host-'));dirs.push(dir)
  vi.stubEnv('SUPERAGENT_KEYCHAIN','1');vi.stubEnv('DSH_HOME',dir)
  const values=new Map<string,string>()
  const store={service:'com.aisoup.superagent.test.unit',get:vi.fn(async(k:string)=>values.get(k)),set:vi.fn(async(k:string,v:string)=>{values.set(k,v)}),delete:vi.fn(async(k:string)=>{values.delete(k)})}
  const provider=new MacCredentialProvider(new Context(),{dshHome:dir,store})
  const ctx={get:(key:string)=>key==='credentials'?provider:undefined}
  return{dir,values,store,provider,ctx:ctx as any}
}

it('只列真实存着的 Key 名字，不带 Key 内容；清单里有但钥匙串里没有的不列',async()=>{
  const t=setup()
  await t.provider.set(credentialRef('SUPERAGENT_ASSISTANT_API_KEY'),'sk-fixture-assistant-0001')
  t.values.set('legacy:tencent','sk-fixture-legacy-0002')
  writeFileSync(join(t.dir,'superagent-legacy-keys.json'),JSON.stringify({accounts:['legacy:tencent','legacy:bailian']}))
  const keys=await listStoredKeys(t.ctx)
  expect(keys.map(k=>k.label)).toEqual(['超级玩家的大模型','旧版复制来的 · 腾讯云 TokenHub'])
  expect(JSON.stringify(keys)).not.toMatch(/fixture/)
  expect(keys[0]!.inUse).toBe(true)
})

it('删除：正式条目从钥匙串和索引都去掉；旧版条目从钥匙串和清单都去掉',async()=>{
  const t=setup()
  await t.provider.set(credentialRef('SUPERAGENT_ASSISTANT_API_KEY'),'sk-fixture-assistant-0001')
  t.values.set('legacy:tencent','sk-fixture-legacy-0002')
  writeFileSync(join(t.dir,'superagent-legacy-keys.json'),JSON.stringify({accounts:['legacy:tencent']}))
  const [assistant,legacy]=await listStoredKeys(t.ctx)
  await deleteStoredKey(t.ctx,legacy!.id)
  expect(t.values.has('legacy:tencent')).toBe(false)
  expect(JSON.parse(readFileSync(join(t.dir,'superagent-legacy-keys.json'),'utf8')).accounts).toEqual([])
  await deleteStoredKey(t.ctx,assistant!.id)
  expect(t.values.has('ref:SUPERAGENT_ASSISTANT_API_KEY')).toBe(false)
  expect(await listStoredKeys(t.ctx)).toEqual([])
})

it('不认识的条目一律拒绝，什么都不删',async()=>{
  const t=setup()
  t.values.set('ref:OTHER','x');t.values.set('legacy:tencent','y')
  for(const bad of ['ref:OTHER','legacy:tencent','local.superagent.clerk','','../x'])
    await expect(deleteStoredKey(t.ctx,bad)).rejects.toThrow()
  expect(t.store.delete).not.toHaveBeenCalled()
})

it('没开钥匙串模式时不列',async()=>{
  const t=setup();vi.stubEnv('SUPERAGENT_KEYCHAIN','')
  expect(await listStoredKeys(t.ctx)).toEqual([])
})

it('记录文件夹：真实位置和大小',async()=>{
  const t=setup()
  mkdirSync(join(t.dir,'sub'));writeFileSync(join(t.dir,'sub','a.txt'),'x'.repeat(2048))
  const info=await recordsInfo()
  expect(info.path).toBe(t.dir)
  expect(info.bytes).toBeGreaterThanOrEqual(2048)
})

// 复查第 1 轮中等 9：一键检测也要真测超级玩家正在用的大脑（拿存着的 Key 连一次）。
import {checkAssistantModel} from '../src/settings-host.ts'
it('大脑检测：连得上且列表里有当前模型 → 通过；连不上照实说；Key 不交出去',async()=>{
  const state={configured:true,provider:'other',baseUrl:'https://api.example.com/v1',model:'m1'} as any
  const discover=vi.fn(async(req:{key:string})=>{expect(req.key).toBe('sk-real');return [{id:'m1',label:'m1'}]})
  expect(await checkAssistantModel(state,async()=>'sk-real',discover as any)).toEqual({ok:true,reason:''})
  const down=vi.fn(async()=>{throw new Error('连不上：401')})
  const r=await checkAssistantModel(state,async()=>'sk-real',down as any)
  expect(r.ok).toBe(false);expect(r.reason).toContain('401');expect(JSON.stringify(r)).not.toContain('sk-real')
  expect((await checkAssistantModel({configured:false} as any,async()=>undefined,discover as any)).ok).toBe(false)
  const gone=vi.fn(async()=>[{id:'other',label:'other'}])
  expect((await checkAssistantModel(state,async()=>'sk-real',gone as any)).reason).toContain('m1')
})

// 复查第 2 轮中等 3：单个零件检测的 30 秒要把“等后台启动”算进去，不能后台慢起来就拖到 39 秒。
import Gateway from '../src/index.ts'
it('检测零件：后台启动很慢也在 30 秒内放弃',async()=>{
  vi.useFakeTimers()
  try{
    const g=Object.create(Gateway.prototype) as any
    g.request=()=>new Promise(()=>{})   // 后台一直没起来
    const started=Date.now()
    const p=g.checkBlock('codex')
    await vi.advanceTimersByTimeAsync(30_001)
    const r=await p
    expect(r.ok).toBe(false);expect(r.reason).toContain('30 秒')
    expect(Date.now()-started).toBeLessThanOrEqual(30_100)
  }finally{vi.useRealTimers()}
})
