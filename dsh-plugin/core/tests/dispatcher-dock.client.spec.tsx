// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {DispatcherCardView} from '../src/client/DispatcherDock.tsx'
import type {DispatcherConnectResult,DispatcherProposal} from '../src/types.ts'
afterEach(cleanup)
const proposal:DispatcherProposal={id:'one',session:'chat-1',tool:'codex',label:'Codex',card:'CARD-001',title:'接调度员',what:'做什么',how:'怎么接',needYou:'需要你',scope:'影响范围',createdAt:1}
it('接入确认卡被新提议替换后，旧检测结果不能操作新卡',async()=>{let finish!:(r:DispatcherConnectResult)=>void;const connect=vi.fn(()=>new Promise<DispatcherConnectResult>(r=>{finish=r}));const onConnected=vi.fn(),onDismiss=vi.fn();const view=render(<DispatcherCardView proposal={proposal} connect={connect} onConnected={onConnected} onDismiss={onDismiss}/>);fireEvent.click(screen.getByRole('button',{name:'确认，检测并启用'}));view.rerender(<DispatcherCardView proposal={{...proposal,id:'two',title:'新的接入'}} connect={connect} onConnected={onConnected} onDismiss={onDismiss}/>);await act(async()=>finish({ok:true,status:'ready',tool:'codex',label:'Codex',model:'test-model',reason:'',startedPlan:false,planNote:'',card:'CARD-001'}));expect(onConnected).not.toHaveBeenCalled();expect(screen.getByText('新的接入')).toBeTruthy();expect((screen.getByRole('button',{name:'确认，检测并启用'}) as HTMLButtonElement).disabled).toBe(false)})
