/// <reference types="vite/client" />
import {mkdtempSync,writeFileSync,readFileSync,chmodSync,rmSync} from 'node:fs'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {afterEach,expect,it} from 'vitest'
const modules=import.meta.glob('../src/keychain-store.ts',{eager:true}) as Record<string,any>
const dirs:string[]=[]
afterEach(()=>dirs.splice(0).forEach(p=>rmSync(p,{recursive:true,force:true})))
function setup(){const Store=modules['../src/keychain-store.ts']?.MacKeychainStore;expect(Store,'缺少只通过标准输入写Key的钥匙串适配器').toBeTypeOf('function');const dir=mkdtempSync(join(tmpdir(),'sa-keychain-fake-'));dirs.push(dir);const helper=join(dir,'fake-helper.py'),log=join(dir,'argv.json');writeFileSync(helper,`#!/usr/bin/env python3\nimport json,sys\ntext=sys.stdin.read()\ntry: body=json.loads(text) if text else {}\nexcept ValueError: body={}\njson.dump({'args':sys.argv[1:],'secretInStdin':'fixture-secret-key' in text},open(${JSON.stringify(log)},'w'))\nprint(json.dumps({'ok':True,'value':'fixture-secret-key'}))\n`);chmodSync(helper,0o755);return{Store,helper,log,store:new Store({service:'com.aisoup.superagent.test.unit',helperPath:helper})}}
it('Key只进子进程标准输入，不进命令参数；读值只在Host内返回',async()=>{const{store,log}=setup();await store.set('SUPERAGENT_TEST_KEY','fixture-secret-key');const call=JSON.parse(readFileSync(log,'utf8'));expect(call.secretInStdin).toBe(true);expect(JSON.stringify(call.args)).not.toContain('fixture-secret-key');expect(await store.get('SUPERAGENT_TEST_KEY')).toBe('fixture-secret-key');await store.delete('SUPERAGENT_TEST_KEY')})
it('不能指向旧版或任意服务名，空Key不能存',async()=>{const{Store,helper,store}=setup();expect(()=>new Store({service:'old-real-app',helperPath:helper})).toThrow();await expect(store.set('SUPERAGENT_TEST_KEY','')).rejects.toThrow()})
it('短Key全部遮住，长Key描述也不能还原完整值',()=>{const mask=modules['../src/keychain-store.ts']?.maskSecret;expect(mask).toBeTypeOf('function');expect(mask('tiny')).not.toContain('tiny');expect(mask('fixture-secret-key')).not.toContain('fixture-secret-key')})
it('辅助程序未读完输入就退出时，写入失败可控，不产生未处理的管道错误',async()=>{const{Store}=setup();const dir=mkdtempSync(join(tmpdir(),'sa-keychain-exit-'));dirs.push(dir);const helper=join(dir,'exit.py');writeFileSync(helper,'#!/usr/bin/env python3\nraise SystemExit(2)\n');chmodSync(helper,0o755);const store=new Store({service:'com.aisoup.superagent.test.unit',helperPath:helper});await expect(store.set('SUPERAGENT_TEST_KEY','fixture'.repeat(200000))).rejects.toThrow()})
