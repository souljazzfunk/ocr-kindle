#!/usr/bin/env python3
"""Run check_book.py's contents_chapters on converted book folders, on any machine.

    python3 .claude/skills/verify-book-checks/contents_report.py <pages folder>...

Mac-only and network modules (Vision, Quartz, google.genai, ...) are stubbed: the contents check reads only the
page files and the Markdown. Prints, per folder, the entries each pipeline found and the ones no heading covers.
"""
import importlib.abc
import importlib.machinery
import sys
import types
from pathlib import Path

STUBBED = ('google', 'anthropic', 'mistralai', 'openai', 'PIL', 'Quartz', 'Vision', 'objc', 'Foundation', 'AppKit',
           'ApplicationServices', 'requests', 'ollama', 'dotenv', 'numpy', 'fitz')


class Anything:
    def __getattr__(self, name):
        return Anything()

    def __call__(self, *args, **kwargs):
        return Anything()

    def __mro_entries__(self, bases):
        return (object,)


class Stub(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split('.')[0] in STUBBED:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)

    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__path__ = []
        module.__getattr__ = lambda name: Anything()
        return module

    def exec_module(self, module):
        pass


def main(folders):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    try:
        import check_book  # noqa: F401  the real modules where they are installed (the Mac's venv)
    except ImportError:
        sys.meta_path.insert(0, Stub())
    import check_book
    import layout
    failed = False
    for folder in map(Path, folders):
        md = next(folder.glob('*.md'))
        images = check_book.page_images(folder)
        unheaded = check_book.contents_unheaded(folder, md.read_text(encoding='utf-8'))
        print(f'{folder.parent.name}: {len(images)} pages, live text entries {len(layout.livetext_contents(images)[1])}, '
              f'vision entries {len(layout.contents_entries(images))}, without a heading {len(unheaded)}')
        for entry in unheaded[:20]:
            print(f'    {entry}')
        failed |= bool(unheaded)
    print('CONTENTS FAIL' if failed else 'CONTENTS OK')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
