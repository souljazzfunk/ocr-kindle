import sys
import types

import pytest

import ax
import capture_app


class Pt:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Sz:
    def __init__(self, w, h):
        self.width, self.height = w, h


def fake_api(tree, active=True):
    """A pyobjc stand-in: elements are dicts of attribute -> value, children under 'AXChildren'."""
    class AS:
        @staticmethod
        def AXUIElementCreateApplication(pid):
            return tree

        @staticmethod
        def AXUIElementCopyAttributeValue(e, name, out):
            if name in e:
                return 0, e[name]
            return -25205, None  # kAXErrorNoValue

        @staticmethod
        def AXValueGetValue(v, kind, out):
            return True, v

    class Running:
        def processIdentifier(self):
            return 42

        def isActive(self):
            return active

    class AppKit:
        class NSRunningApplication:
            @staticmethod
            def runningApplicationsWithBundleIdentifier_(bundle):
                return [Running()] if bundle == ax.BUNDLE else []
    return lambda: (AS, AppKit)


def test_the_dump_walks_the_windows_depth_first_in_the_lines_the_parser_reads():
    cover = {'AXRole': 'AXButton', 'AXTitle': '', 'AXDescription': 'For 91 Days in Bolivia, Powell, Michael, ',
             'AXValue': 'Book Not Downloaded', 'AXPosition': Pt(1332.0, 1536.0), 'AXSize': Sz(147.0, 220.0)}
    grid = {'AXRole': 'AXGroup', 'AXTitle': 'Library Grid View', 'AXPosition': Pt(489, 1273), 'AXSize': Sz(1024, 710),
            'AXChildren': [cover]}
    page = {'AXRole': 'AXStaticText', 'AXValue': 'The Unfairly Maligned Coca Leaf\nConsider | a product.' + 'x' * 500,
            'AXPosition': Pt(600, 1300), 'AXSize': Sz(800, 600)}
    window = {'AXTitle': 'Kindle', 'AXPosition': Pt(489, 1187), 'AXSize': Sz(1024, 843), 'AXChildren': [grid, page]}
    text = ax.dump(api=fake_api({'AXWindows': [window]}))
    lines = text.splitlines()
    assert lines[:3] == ['frontmost=true', 'windows=1', 'window|Kindle|489,1187|1024,843']
    assert lines[3] == 'element|AXGroup||Library Grid View|||489,1273|1024,710'
    assert lines[4] == 'element|AXButton|||For 91 Days in Bolivia, Powell, Michael, |Book Not Downloaded|1332,1536|147,220'
    # A page's text sits in a static text's value (probe of 2026-10-11 19:20): newlines and bars would break the
    # line, and the whole page is more than the parser needs.
    assert lines[5].startswith('element|AXStaticText|||')
    assert len(lines) == 6 and '\n' not in lines[5] and lines[5].count('|') == 7 and len(lines[5]) < 500
    found = capture_app.elements(lambda script: (0, text, ''))
    assert capture_app.in_library(found) and capture_app.cover_of('For 91 Days in Bolivia', found) == found[1]
    assert capture_app.elements(lambda s: (0, ax.dump(api=fake_api({'AXWindows': [window]}, active=False)), ''))


def test_a_refused_api_is_named_and_a_missing_one_returns_nothing():
    with pytest.raises(RuntimeError, match='Accessibility permission'):
        ax.dump(api=fake_api({}))
    assert ax.dump(api=lambda: None) is None
    assert ax.dump(api=fake_api({'AXWindows': []}), bundle='com.example.absent') == 'frontmost=false\nwindows=0\n'


def test_the_tree_is_read_through_the_api_only_for_the_real_osascript(monkeypatch):
    # A fake script in the tests is used as given; the real reader goes through the API where pyobjc has it.
    monkeypatch.setattr(ax, 'usable', lambda: True)
    monkeypatch.setattr(ax, 'dump', lambda: 'frontmost=true\nwindows=1\nelement|AXGroup||Library Grid View|||1,1|2,2\n')
    assert capture_app.in_library(capture_app.elements(capture_app.osascript))
    assert capture_app.elements(lambda s: (0, 'element|AXGroup|||page||1,1|2,2\n', ''))[0]['description'] == 'page'
    monkeypatch.setattr(ax, 'usable', lambda: False)
    monkeypatch.setattr(capture_app, 'osascript', lambda s: (0, 'element|AXGroup|||fallback||1,1|2,2\n', ''))
    assert capture_app.elements(capture_app.osascript)[0]['description'] == 'fallback'


@pytest.mark.skipif(sys.platform != 'darwin', reason='pyobjc is installed on macOS only')
def test_the_framework_is_installed_on_the_mac():
    assert ax.api() is not None and ax.usable()
