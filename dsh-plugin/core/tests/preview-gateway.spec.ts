import { expect, it } from 'vitest'
import Gateway from '../src/index.ts'

it('查询预览状态必须把当前卡号交给已有后台接口',async()=>{
 const gateway=Object.create(Gateway.prototype) as any
 gateway.get=async(path:string,query:Record<string,string>)=>{
  if(path!=='/api/clerk/preview' || query.card!=='CARD-011')throw new Error('需要项目和卡片')
  return {running:true,url:'http://127.0.0.1:3099/'}
 }
 expect(await gateway.previewStatus('CARD-011')).toEqual({running:true,url:'http://127.0.0.1:3099/'})
})
