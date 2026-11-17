"""真实窗口全流程的测试替身；只允许临时走查项目及 CLI 检测目录，不调用真实模型。"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path('/tmp/superagent-align-20261116').resolve()
PROJECT = BASE / 'fullflow-project'
WORKTREES = BASE / 'fullflow-project-worktrees'


def main():
    tool = Path(sys.argv[0]).name
    args = sys.argv[1:]
    cwd = Path.cwd().resolve()
    prompt = args[args.index('-p') + 1] if '-p' in args else (args[-1] if args else '')
    question = cwd / '题目.txt'
    in_project = cwd == PROJECT or WORKTREES in cwd.parents
    is_probe = question.is_file() and ('检测' in cwd.name or 'check' in cwd.name.lower())
    if not (in_project or is_probe):
        raise RuntimeError('受控CLI拒绝未知工作目录')

    def reply(text):
        if '-o' in args:
            out = Path(args[args.index('-o') + 1]).resolve()
            if not (BASE in out.parents or Path('/private/var/folders') in out.parents):
                raise RuntimeError('受控CLI拒绝未知输出路径')
            out.write_text(text)
        if tool == 'agy':
            print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': text}}, ensure_ascii=False))
        else:
            print(text)

    def event(kind):
        with (BASE / 'controlled-cli-events.jsonl').open('a') as log:
            log.write(json.dumps({'role': kind, 'tool': tool, 'folder': cwd.name}) + '\n')

    if is_probe:
        match = re.search(r'写进新文件\s+([^\s]+)\s+的第一行', prompt)
        if not match or Path(match[1]).name != match[1]:
            raise RuntimeError('未识别检测小任务')
        count = len(subprocess.check_output(['ls'], cwd=cwd, text=True).splitlines())
        (cwd / match[1]).write_text(f'{int(question.read_text().strip()) + 1}\n{count}\n')
        event('probe')
        return reply('已实际读取题目、运行ls并写入答案。')
    if tool == 'codex' and '--output-schema' in args:
        sys.stdin.read()
        plan = {'summary': '为测试页面增加中文问候', 'steps': ['先补问候行为测试', '实现问候并保持原测试通过', '复查后交给你验收'],
                'result': '输入名字显示中文问候；空名字显示你好。', 'not_do': '不联网，不安装依赖，不改其他功能。',
                'questions': [], 'allowed': ['web/index.html', 'web/greeting.mjs'], 'test_command': 'npm test',
                'profile': '# 项目说明\n本机问候页面；Node内置测试，无第三方依赖。',
                'view_how': '在预览页面输入名字，检查问候。', 'view_command': 'npm run dev',
                'view_url': 'http://127.0.0.1:48931/web/'}
        if (BASE / 'rephoto-null-input').exists():
            plan['summary'] = '完善中文问候的空值名字处理'
            plan['result'] = '输入名字显示中文问候；没有名字（含空值）显示你好。'
        event('plan')
        return reply(json.dumps(plan, ensure_ascii=False))
    if tool == 'codex' and ('只写测试，不改产品代码' in prompt or '只改测试文件，不改产品代码' in prompt):
        tests = cwd / 'tests'
        tests.mkdir(exist_ok=True)
        (tests / 'greeting.test.mjs').write_text("import test from 'node:test';\nimport assert from 'node:assert/strict';\nimport { greet } from '../web/greeting.mjs';\ntest('名字生成问候',()=>assert.equal(greet('小明'),'你好，小明'));\ntest('没有名字仍问好',()=>assert.equal(greet(''),'你好'));\n")
        if (BASE / 'rephoto-null-input').exists():
            with (tests / 'greeting.test.mjs').open('a') as stream:
                stream.write("test('空值名字也问好',()=>assert.equal(greet(null),'你好'));\n")
        event('test')
        return reply('已补真实空值名字测试；尚未修改产品代码。' if (BASE / 'rephoto-null-input').exists() else '已新增两条真实行为测试；尚未修改产品代码。')
    if tool == 'agy' and ('开发' in prompt or '实现' in prompt or '代码' in prompt):
        # Opt-in bounded gate lets the real UI observe a live worker before edits.
        if (BASE / 'hold-developer').exists() and WORKTREES in cwd.parents:
            event('develop_wait')
            deadline = time.monotonic() + 180
            while not (BASE / 'release-developer').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('controlled developer gate timed out')
                time.sleep(0.2)
        (cwd / 'web/greeting.mjs').write_text("export function greet(name) { return name.trim() ? `你好，${name.trim()}` : '你好'; }\n")
        if (BASE / 'rephoto-null-input').exists():
            (cwd / 'web/greeting.mjs').write_text("export function greet(name) { const clean = String(name ?? '').trim(); return clean ? `你好，${clean}` : '你好'; }\n")
        (cwd / 'web/index.html').write_text('''<!doctype html><html lang="zh"><meta charset="utf-8"><title>问候走查</title><body><h1>问候走查</h1><label>你的名字<input id="name"></label><button id="greet">问候</button><p id="answer" aria-live="polite"></p><script type="module">import {greet} from './greeting.mjs';document.querySelector('#greet').onclick=()=>{document.querySelector('#answer').textContent=greet(document.querySelector('#name').value)};</script></body></html>''')
        event('develop')
        return reply('汇报：已补齐空值名字问候，仅修改方案允许的页面和函数；未改测试。' if (BASE / 'rephoto-null-input').exists() else '汇报：已实现问候，仅修改方案允许的页面和函数；未改测试。')
    if tool == 'codex' and ('复查' in prompt or '审查' in prompt):
        tests = subprocess.run(['npm', 'test'], cwd=cwd, capture_output=True, text=True)
        diff = subprocess.run(['git', 'diff', '--check'], cwd=cwd, capture_output=True, text=True)
        event('review')
        if (BASE / 'fail-review').exists() and tests.returncode == diff.returncode == 0:
            counter = BASE / ('review-attempt-' + cwd.name + '.txt')
            count = int(counter.read_text()) + 1 if counter.exists() else 1
            counter.write_text(str(count))
            cases = ['重复点击的场景仍需用户决定', '空白输入的交互仍需用户决定', '窄窗口文字的显示仍需用户决定']
            case = cases[min(count - 1, len(cases) - 1)]
            return reply(f'结论：不通过\n1. 【中等】【这次改的】受控分支：{case}。\n说明：这是测试替身指定的第{count}轮问题，仅用于核验三轮停下分支，不代表真实缺陷。')
        return reply('结论：通过\n已实际运行npm test及git diff --check，均通过。' if tests.returncode == diff.returncode == 0 else '结论：不通过\n实际测试或差异检查失败。')
    raise RuntimeError('受控CLI拒绝未知角色或调用，不转交真实工具')


if __name__ == '__main__':
    main()
