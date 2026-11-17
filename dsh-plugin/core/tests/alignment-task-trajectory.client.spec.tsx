// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it} from 'vitest'
import {TaskTrajectory} from '../src/client/TaskTrajectory.tsx'
afterEach(cleanup)
const base={id:'r1',time:'09:18',actor:'Codex',summary:'写测试完成',seconds:2,usage:null,raw:'真实报告：新增了两条测试'}
function setup(row:any=base){render(<TaskTrajectory sessionId={'s' as any} cardOfSession={async()=> 'CARD-022'} trajectory={async()=>({distribution:[{actor:'Codex',seconds:2}],rows:[row]})}/>)}
it('05t按图直接给用时分布与五列表，不露卡号计数刷新和重复标题',async()=>{
 setup();await screen.findByRole('table')
 expect(screen.getByRole('columnheader',{name:'时间'})).toBeTruthy()
 expect(screen.queryByRole('heading',{name:'流转记录'})).toBeNull()
 expect(screen.queryByText(/CARD-022/)).toBeNull();expect(screen.queryByText(/每 2 秒刷新/)).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'看原文'}));expect(screen.getByText('真实报告：新增了两条测试')).toBeTruthy()
})
it('05t明确存在的运行输出和改动各自可看，原文仍能打开',async()=>{
 setup({...base,output:'测试输出：3条全部通过',diff:'真实修改：问候函数'})
 fireEvent.click(await screen.findByRole('button',{name:'看运行输出'}))
 expect(screen.getByText('测试输出：3条全部通过',{exact:true})).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'看改动'}));expect(screen.getByText('真实修改：问候函数',{exact:true})).toBeTruthy()
 fireEvent.click(screen.getByRole('button',{name:'看原文'}));expect(screen.getByText('真实报告：新增了两条测试',{exact:true})).toBeTruthy()
})
it('05t未记录输出或改动时不编造可点的历史内容',async()=>{
 setup();await screen.findByRole('table')
 expect(screen.queryByRole('button',{name:'看运行输出'})).toBeNull();expect(screen.queryByRole('button',{name:'看改动'})).toBeNull()
})
it('05t分布头说明总记录时长与其中等你，阶段标签保留',async()=>{
 render(<TaskTrajectory sessionId={'time-s' as any} cardOfSession={async()=> 'CARD-022'} trajectory={async()=>({rows:[],distribution:[{actor:'写方案',seconds:58},{actor:'等你批',seconds:200},{actor:'跑测试',seconds:142}]})}/> )
 expect(await screen.findByText(/总.*其中等你/)).toBeTruthy()
 expect(screen.getByText(/写方案/)).toBeTruthy();expect(screen.getByText(/等你批/)).toBeTruthy();expect(screen.getByText(/跑测试/)).toBeTruthy()
})
