"""Every way capture_app reads the Kindle app's screen is checked against trees recorded on the Mac.

Of the twenty Kindle-app fixes of 2026-10-11 (#56-#76), most were a reading written from a hand-typed or remembered
tree that the real app did not match: names in the description not the title (#63), the slider found by its
description (#65), a cover with a 0x0 frame (#67), a toolbar with no slider in the Accessibility API's tree (#76).
Each cost a run on the Mac to find. A new reading of the screen is added here with the recorded tree it was read
from, and expect.json says what each tree must read as."""
import json
from pathlib import Path

import pytest

import capture_app

FIXTURES = Path(__file__).parent / 'fixtures' / 'kindle'
EXPECT = json.loads((FIXTURES / 'expect.json').read_text(encoding='utf-8'))
FLAGS = ('in_library', 'chrome_shown', 'menu_open')


def tree(name):
    return capture_app.elements(lambda script: (0, (FIXTURES / name).read_text(encoding='utf-8'), ''))


def test_every_recorded_tree_has_its_expectations_and_vice_versa():
    recorded = {p.name for p in FIXTURES.glob('*.txt')}
    assert recorded == set(EXPECT), 'add the recording to expect.json, or the expected file to fixtures/kindle'
    for name, expect in EXPECT.items():
        assert set(expect) == {*FLAGS, 'location', 'covers', 'tabs', 'toc'}, name


@pytest.mark.parametrize('name', sorted(EXPECT))
def test_each_recorded_tree_reads_as_expected(name):
    found, expect = tree(name), EXPECT[name]
    assert found, 'no element line parsed'
    for flag in FLAGS:
        assert getattr(capture_app, flag)(found) is expect[flag], f'{flag} on {name}'
    where = capture_app.location_of(found)
    assert (list(where) if where else None) == expect['location'], f'location on {name}'
    for title in expect['covers']:
        cover = capture_app.cover_of(title, found)
        assert cover is not None and capture_app.framed(cover), f'cover of {title} on {name}'
    for title in expect['tabs']:
        assert capture_app.tab_of(title, found) is not None, f'tab of {title} on {name}'
    assert [e['description'] or e['title'] for e in capture_app.toc_entries(found)] == expect['toc'], f'toc on {name}'


def test_each_flag_is_seen_true_and_false_in_the_recordings():
    # A flag no recording turns on, or none turns off, is a reading nobody has checked against the app.
    for flag in FLAGS:
        values = {EXPECT[name][flag] for name in EXPECT}
        assert values == {True, False}, f'{flag} needs a recorded tree where it is {({True, False} - values).pop()}'
