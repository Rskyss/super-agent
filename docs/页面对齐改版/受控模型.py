"""仅供本次真实窗口走查的本机 OpenAI 兼容服务，不转发请求，不记录 Key。"""
import argparse
import json
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    flaky_seen = False
    def log_message(self, *_args):
        pass

    def send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get('Authorization', '').endswith('alignment-flaky-once') and not Handler.flaky_seen:
            Handler.flaky_seen = True
            with Path('/tmp/superagent-align-20261116/model-shape.jsonl').open('a') as log:
                log.write(json.dumps({'fixture': 'first-discovery-temporarily-unavailable', 'status': 503}) + '\n')
            return self.send_json({'error': {'message': 'Temporary test outage'}}, 503)
        if self.headers.get('Authorization', '').endswith('alignment-invalid'):
            return self.send_json({'error': {'message': 'Invalid test credential'}}, 401)
        self.send_json({'object': 'list', 'data': [
            {'id': 'alignment-local', 'object': 'model', 'owned_by': 'local-test', 'name': '本机走查模型'},
            {'id': 'alignment-local-long', 'object': 'model', 'owned_by': 'local-test', 'name': '本机备用模型'},
        ]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        messages = body.get('messages', [])
        last_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get('role') == 'user'), -1)
        # DSH may append user-role workspace context after the actual prompt.
        # Match this disposable fixture's trigger across user messages, not only that context.
        text = '\n'.join(str(m.get('content', '')) for m in messages if m.get('role') == 'user')
        after_tool = any(m.get('role') == 'tool' for m in messages[last_user + 1:])
        tools = [t.get('function', {}) for t in body.get('tools', [])]
        with Path('/tmp/superagent-align-20261116/model-shape.jsonl').open('a') as log:
            log.write(json.dumps({'path': self.path, 'fields': sorted(body),
                                  'roles': [m.get('role') for m in messages],
                                  'tools': [t.get('name') for t in tools],
                                  'has_trigger': '施工验收' in text, 'after_tool': after_tool}) + '\n')
        propose = next((t.get('name') for t in tools if 'propose_task' in t.get('name', '')), None)
        dispatcher = next((t.get('name') for t in tools if 'propose_dispatcher' in t.get('name', '')), None)
        status_tool = next((t.get('name') for t in tools if 'card_status' in t.get('name', '')), None)
        change_tool = next((t.get('name') for t in tools if 'card_change' in t.get('name', '')), None)
        # Only a newer explicit request can propose again. Old triggers in conversation
        # history must not turn a later ordinary chat message into another task.
        def fresh_request(words, tool_name):
            trigger = max((i for i, m in enumerate(messages)
                           if m.get('role') == 'user'
                           and any(word in str(m.get('content', '')) for word in words)), default=-1)
            invoked = max((i for i, m in enumerate(messages)
                           if any(call.get('function', {}).get('name') == tool_name
                                  for call in m.get('tool_calls', []))), default=-1)
            return trigger >= 0 and trigger > invoked
        tool_call = None
        content = '你好，我在。'
        if not after_tool and change_tool and fresh_request(('修改验收标准',), change_tool):
            args = {'card': '', 'summary': '第 3 条：原有测试仍通过 → 原有与新增问候测试都通过',
                    'text': '修改验收标准第3条为原有与新增问候测试都通过，其他要求不变。',
                    'criteria_changes': json.dumps([{'replaceNth': 3, 'text': '原有与新增问候测试都通过'}], ensure_ascii=False)}
            tool_call = {'id': 'alignment-change-' + str(int(time.time() * 1000)), 'type': 'function',
                         'function': {'name': change_tool, 'arguments': json.dumps(args, ensure_ascii=False)}}
            content = None
        elif not after_tool and dispatcher and fresh_request(('接一个调度员', '接入调度员'), dispatcher):
            tool_call = {'id': 'alignment-dispatcher-' + str(int(time.time() * 1000)), 'type': 'function',
                         'function': {'name': dispatcher, 'arguments': '{"tool":"codex","card":""}'}}
            content = None
        elif not after_tool and propose and fresh_request(('施工验收', '对照任务'), propose):
            arguments = {'title': '页面对齐走查', 'do': '为测试项目增加一句问候，帮助检查从聊天到验收的流程。',
                         'not_do': '不改其他功能，不联网，不推送。',
                         'done_when': '① 输入名字能显示问候\n② 没有名字时显示你好\n③ 原有测试仍通过',
                         'questions': '问候用中文还是英文？｜建议：中文\n要保留原命令吗？｜建议：保留'}
            tool_call = {'id': 'alignment-proposal-' + str(int(time.time() * 1000)), 'type': 'function',
                         'function': {'name': propose, 'arguments': json.dumps(arguments, ensure_ascii=False)}}
            content = None
        elif not after_tool and status_tool and ('进度' in text or '到哪' in text):
            tool_call = {'id': 'alignment-status-' + str(int(time.time() * 1000)), 'type': 'function',
                         'function': {'name': status_tool, 'arguments': '{}'}}
            content = None
        elif after_tool:
            last_call = next((call.get('function', {}).get('name', '')
                              for m in reversed(messages)
                              for call in reversed(m.get('tool_calls', []))), '')
            if 'card_change' in last_call:
                content = '修改还没生效，确认条已贴在输入框上面，点“确认改”才交给调度员。'
            elif 'propose_dispatcher' in last_call:
                content = '请看输入框上面的调度员确认卡，确认后会实际检测本机工具。'
            else:
                content = '请看输入框上面的卡，点确认就建任务卡，之后调度员写方案给你批。'
        message = {'role': 'assistant', 'content': content}
        if tool_call:
            message['tool_calls'] = [tool_call]
        finish = 'tool_calls' if tool_call else 'stop'
        common = {'id': 'alignment-response', 'created': int(time.time()), 'model': body.get('model', 'alignment-local')}
        usage = {'prompt_tokens': 40, 'completion_tokens': 20, 'total_tokens': 60}
        if not body.get('stream'):
            return self.send_json({**common, 'object': 'chat.completion', 'choices': [
                {'index': 0, 'message': message, 'finish_reason': finish}], 'usage': usage})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        delta = {'role': 'assistant'}
        if tool_call:
            delta['tool_calls'] = [{'index': 0, **tool_call}]
        else:
            delta['content'] = content
        for item in [
            {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': usage},
        ]:
            self.wfile.write(('data: ' + json.dumps(item, ensure_ascii=False) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=48921)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'本机受控模型已启动：{args.port}', flush=True)
    server.serve_forever()
