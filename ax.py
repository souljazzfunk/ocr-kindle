"""The Kindle window's elements read in-process through the Accessibility API (pyobjc), as the lines that
capture_app.elements parses: element|role|subrole|title|description|value|x,y|w,h.

The AppleScript dump (entire contents of window 1, then eight properties of every element, then the menu bar) took
114 s for 234 lines on 2026-10-11 20:02: each property read is an Apple event the app answers slowly, and the open
of a book read the tree several times, so a book took 3-4 minutes to open (Laiken, 19:59: far too long). In-process
AX calls answer in milliseconds."""
import sys

BUNDLE = 'com.amazon.Lassen'
ATTRS = ('AXRole', 'AXSubrole', 'AXTitle', 'AXDescription', 'AXValue')
POINT, SIZE = 1, 2  # kAXValueCGPointType, kAXValueCGSizeType
VALUE_CHARS = 400  # a page's whole text can sit in a static text's value (probe of 2026-10-11 19:20)


def api():
    """The pyobjc modules, or None where they are missing (Linux, or the framework not installed)."""
    try:
        import ApplicationServices
        import AppKit
    except ImportError:
        return None
    return ApplicationServices, AppKit


def usable(api=api):
    return sys.platform == 'darwin' and api() is not None


def text(v):
    if v is None:
        return ''
    if isinstance(v, (list, tuple)):
        return ','.join(text(x) for x in v)
    return str(v).replace('\n', ' ').replace('|', ' ')[:VALUE_CHARS]


def dump(bundle=BUNDLE, api=api, max_depth=60):
    """The dump text, or None when the API is not there. Raises when the API refuses (no Accessibility permission)."""
    modules = api()
    if modules is None:
        return None
    AS, AppKit = modules
    apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
    if not apps:
        return 'frontmost=false\nwindows=0\n'
    app = apps[0]
    root = AS.AXUIElementCreateApplication(app.processIdentifier())

    def attr(e, name):
        err, value = AS.AXUIElementCopyAttributeValue(e, name, None)
        return (err, value if err == 0 else None)

    def point(e, name, kind):
        _, v = attr(e, name)
        if v is None:
            return ''
        got = AS.AXValueGetValue(v, kind, None)
        val = got[1] if isinstance(got, tuple) else got
        if val is None:
            return ''
        parts = (val.x, val.y) if kind == POINT else (val.width, val.height)
        return ','.join(str(int(round(p))) for p in parts)

    err, windows = attr(root, 'AXWindows')
    if err != 0:
        raise RuntimeError(f'the Accessibility API refused to read the Kindle windows (AXError {err}); the process '
                           'running capture_app.py needs Accessibility permission in System Settings')
    lines = [f'frontmost={"true" if app.isActive() else "false"}', f'windows={len(windows or [])}']
    for w in windows or []:
        lines.append('window|' + '|'.join((text(attr(w, 'AXTitle')[1]), point(w, 'AXPosition', POINT),
                                           point(w, 'AXSize', SIZE))))
        stack = [(c, 1) for c in reversed(attr(w, 'AXChildren')[1] or [])]
        while stack:
            e, depth = stack.pop()
            lines.append('element|' + '|'.join([text(attr(e, a)[1]) for a in ATTRS]
                                               + [point(e, 'AXPosition', POINT), point(e, 'AXSize', SIZE)]))
            if depth < max_depth:
                stack.extend((c, depth + 1) for c in reversed(attr(e, 'AXChildren')[1] or []))
    return '\n'.join(lines) + '\n'
