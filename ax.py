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


def _app(bundle, AS, AppKit):
    apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle)
    if not apps:
        return None, None
    return apps[0], AS.AXUIElementCreateApplication(apps[0].processIdentifier())


def _attr(AS, e, name):
    err, value = AS.AXUIElementCopyAttributeValue(e, name, None)
    return (err, value if err == 0 else None)


def _walk(AS, windows, max_depth):
    """Every element under the windows, depth first, as (element, depth)."""
    for w in windows or []:
        stack = [(c, 1) for c in reversed(_attr(AS, w, 'AXChildren')[1] or [])]
        while stack:
            e, depth = stack.pop()
            yield e, depth
            if depth < max_depth:
                stack.extend((c, depth + 1) for c in reversed(_attr(AS, e, 'AXChildren')[1] or []))


def dump(bundle=BUNDLE, api=api, max_depth=60):
    """The dump text, or None when the API is not there. Raises when the API refuses (no Accessibility permission)."""
    modules = api()
    if modules is None:
        return None
    AS, AppKit = modules
    app, root = _app(bundle, AS, AppKit)
    if app is None:
        return 'frontmost=false\nwindows=0\n'

    def attr(e, name):
        return _attr(AS, e, name)

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
        for e, _ in _walk(AS, [w], max_depth):
            lines.append('element|' + '|'.join([text(attr(e, a)[1]) for a in ATTRS]
                                               + [point(e, 'AXPosition', POINT), point(e, 'AXSize', SIZE)]))
    return '\n'.join(lines) + '\n'


def press(role, name, bundle=BUNDLE, api=api, max_depth=60):
    """AXPress on the first element of this role whose title or description is name. True when pressed, False when
    no such element or the press is refused, None without the API. AppleScript's `entire contents` did not find the
    Table of Contents' "Cover" button on 2026-10-11 20:45 while this did."""
    modules = api()
    if modules is None:
        return None
    AS, AppKit = modules
    app, root = _app(bundle, AS, AppKit)
    if app is None:
        return False
    for e, _ in _walk(AS, _attr(AS, root, 'AXWindows')[1], max_depth):
        if _attr(AS, e, 'AXRole')[1] == role and name in (text(_attr(AS, e, 'AXTitle')[1]),
                                                           text(_attr(AS, e, 'AXDescription')[1])):
            return AS.AXUIElementPerformAction(e, 'AXPress') == 0
    return False


def scroll(x, y, lines, api=None):
    """Scroll-wheel `lines` upward (negative: downward) with the pointer at the screen point, through Quartz."""
    try:
        import Quartz as module
    except ImportError:
        return 'no Quartz'
    api = api or module
    api.CGEventPost(api.kCGHIDEventTap, api.CGEventCreateMouseEvent(None, api.kCGEventMouseMoved, (x, y), 0))
    api.CGEventPost(api.kCGHIDEventTap, api.CGEventCreateScrollWheelEvent(None, api.kCGScrollEventUnitLine, 1, lines))
    return f'scrolled {lines} lines at {x},{y}'
