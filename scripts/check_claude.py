"""Every `claude -p` goes through `ask_claude`, which passes `--json-schema`.

On 2026-10-12 (Steve Jobs, ledger F121) `ask_layout_claude` asked for a JSON array in prose and then
parsed `result`. The reply added a sentence, and Sonnet was given up for the book.
"""
import ast
import subprocess
import sys
from pathlib import Path

HELPER = 'ask_claude'
CLAUDE_FLAGS = {'claude', '--model', '--output-format', '--json-schema', '--allowedTools'}


def problems(source, filename='<source>'):
    """Lines where `claude -p` is not `ask_claude` passing `--json-schema`."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f'{filename}:{error.lineno}: {error.msg}']
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_run(node) or not node.args:
            continue
        argv = _argv_list(node.args[0], parents, node)
        if argv is None:
            continue
        flags = _constants(argv)
        if '-p' not in flags and '--print' not in flags:
            continue
        if not (CLAUDE_FLAGS & set(flags)):
            continue
        fn = _function_node(parents, node)
        name = fn.name if fn else ''
        if name == HELPER and '--json-schema' in flags:
            continue
        who = f'{name} calls ' if name else ''
        found.append(f'{filename}:{node.lineno}: {who}claude -p without --json-schema; '
                     f'call {HELPER}, which passes the schema')
    return found


def _is_run(node):
    func = node.func
    return ((isinstance(func, ast.Attribute) and func.attr == 'run')
            or (isinstance(func, ast.Name) and func.id == 'run'))


def _constants(list_node):
    return [elt.value for elt in list_node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]


def _function_node(parents, node):
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _argv_list(node, parents, call):
    if isinstance(node, ast.List):
        return node
    if not isinstance(node, ast.Name):
        return None
    fn = _function_node(parents, call)
    if fn is None:
        return None
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == node.id and isinstance(stmt.value, ast.List):
                    return stmt.value
    return None


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    files = subprocess.run(['git', 'ls-files', '*.py'], cwd=root, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    found = 0
    for path in files:
        for msg in problems((root / path).read_text(encoding='utf-8'), path):
            print(msg)
            found += 1
    if found:
        return 1
    print(f'CLAUDE OK: {len(files)} files')
    return 0


if __name__ == '__main__':
    sys.exit(main())
