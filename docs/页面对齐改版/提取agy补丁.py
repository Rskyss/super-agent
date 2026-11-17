"""提取 agy 的代码交付供总调度审查，不自动应用，不允许测试/保护文件进入补丁。"""
import argparse
import json
import re
from pathlib import Path


def unified_transport(response):
    """只转换补丁封装格式；不增改源代码语义。"""
    match = re.search(r'```diff\n(.*?)```', response, re.S)
    if not match:
        raise ValueError('no complete patch in response')
    lines = match.group(1).splitlines()
    output = ['*** Begin Patch']
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(('diff --git ', 'index ')) or not line:
            index += 1
            continue
        if not line.startswith('--- '):
            raise ValueError(f'unknown unified metadata: {line[:80]}')
        old = line[4:]
        index += 1
        if index >= len(lines) or not lines[index].startswith('+++ '):
            raise ValueError('missing unified target')
        target = lines[index][4:]
        index += 1
        name = target[2:] if target.startswith('b/') else target
        if target == '/dev/null':
            raise ValueError('deletion is not allowed')
        adding = old == '/dev/null'
        if not adding and (old[2:] if old.startswith('a/') else old) != name:
            raise ValueError('renaming is not allowed')
        output.append(('*** Add File: ' if adding else '*** Update File: ') + name)
        while index < len(lines) and not lines[index].startswith(('--- ', 'diff --git ')):
            line = lines[index]
            if line.startswith('@@'):
                if not adding:
                    output.append('@@')
            elif line.startswith('\\ No newline'):
                pass
            elif adding and not line.startswith('+'):
                raise ValueError('unprefixed new-file source')
            else:
                output.append(line if line else ' ')
            index += 1
    output.append('*** End Patch')
    return '\n'.join(output) + '\n'


def extract(response_file):
    source = Path(response_file)
    raw = source.read_text()
    offset = raw.find('{"conversation_id"')
    result = json.loads(raw[offset:])
    if result.get('status') != 'SUCCESS':
        raise ValueError('agy did not return SUCCESS')
    response = result.get('response', '')
    start = response.find('*** Begin Patch')
    end = response.rfind('*** End Patch')
    if start < 0 or end < start:
        patch = unified_transport(response)
    else:
        patch = response[start:end + len('*** End Patch')] + '\n'
    if re.search(r'^\*\*\* Delete File:', patch, re.M):
        raise ValueError('physical deletion is outside this hide-and-preserve task')
    files = re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', patch, re.M)
    for name in files:
        path = Path(name)
        valid = name.startswith('dsh-plugin/core/src/') or name.startswith('dsh-plugin/core/locale/')
        valid = valid or name in ['dsh-plugin/core/cordis.patch.yml', 'dsh-plugin/core/package.json',
                                 'dsh-plugin/core/tsconfig.client.json', 'dsh-plugin/core/tsconfig.host.json']
        valid = valid or (name.startswith('docs/页面对齐改版/') and (
            name.endswith('/已藏清单.md') or re.search(r'/agy-[^/]+-回执\.md$', name)))
        if not valid or path.is_absolute() or '..' in path.parts or 'tests' in path.parts:
            raise ValueError(f'patch path outside allowed implementation scope: {name}')
    output = source.with_suffix('.patch')
    # agy sometimes returns raw new-file bodies. Normalize transport prefixes only.
    normalized = []
    adding = False
    for line in re.sub(r'^@@[^\n]*$', '@@', patch, flags=re.M).splitlines():
        if line.startswith('*** '):
            adding = line.startswith('*** Add File:')
        elif adding and not line.startswith('+'):
            line = '+' + line
        normalized.append(line)
    output.write_text('\n'.join(normalized) + '\n')
    print(json.dumps({'status': result['status'], 'conversation': result.get('conversation_id'),
                      'seconds': result.get('duration_seconds'), 'patch': str(output), 'files': files}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('response_file')
    extract(parser.parse_args().response_file)
