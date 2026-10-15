const test = require('node:test');
const assert = require('node:assert');
const { initialState, nextAction, RESTS, STUCK_TURNS, TURNS_PER_PAGE, EXTRA_TURNS, IMAGE_WAITS } = require('./policy.js');

// A screen as content.js observes it: sha names the image, page/pages the footer (none when the footer shows none).
const screen = (sha, page, pages) => ({ sha, position: page ? { n: page, m: pages } : null, appOnly: false, signedOut: false });

// Feeds the observations the way content.js does: after 'save' the same screen again, after a turn the next one.
function replay(observations) {
  let state = initialState();
  const actions = [];
  let i = 0;
  while (i < observations.length) {
    const result = nextAction(state, observations[i]);
    state = result.state;
    actions.push(result.action);
    if (result.action !== 'save') i++;
  }
  return { state, actions };
}

const order = (saved) => Object.entries(saved).sort((a, b) => a[1] - b[1]).map(([sha]) => sha);

test('opened mid-book: pages back to page 1 saving each screen, then forward past them to the end', () => {
  const a = screen('a', 1, 4), b = screen('b', 2, 4), c = screen('c', 3, 4), d = screen('d', 4, 4);
  const { state, actions } = replay([c, b, a, a, a, a, b, c, d, d, d, d]);
  assert.deepStrictEqual(actions, [
    'save', 'turn:prev', 'save', 'turn:prev', 'save', 'turn:prev', 'turn:prev', 'turn:prev', 'turn:next',
    'turn:next', 'turn:next', 'save', 'turn:next', 'turn:next', 'turn:next', 'done:page 4 of 4']);
  assert.deepStrictEqual(order(state.saved), ['a', 'b', 'c', 'd']);
  assert.deepStrictEqual(state.saved, { c: 0, b: -1, a: -2, d: 1 });
  assert.strictEqual(state.start, 'opened at 3/4; 2 earlier screens saved back to page 1');
});

test('opened at page 1: three turns back that change nothing confirm the start', () => {
  const a = screen('a', 1, 3), b = screen('b', 2, 3), c = screen('c', 3, 3);
  const { state, actions } = replay([a, a, a, a, b, c, c, c, c]);
  assert.deepStrictEqual(actions, ['save', 'turn:prev', 'turn:prev', 'turn:prev', 'turn:next',
    'save', 'turn:next', 'save', 'turn:next', 'turn:next', 'turn:next', 'done:page 3 of 3']);
  assert.deepStrictEqual(order(state.saved), ['a', 'b', 'c']);
});

test('an end the footer does not confirm is still the end once both keys fail; check_book.py judges how short it stopped', () => {
  const a = screen('a', 1, 300), b = screen('b', 2, 300);
  const short = replay([a, a, a, a, ...Array(40).fill(b)]).actions;
  assert.deepStrictEqual(short.slice(5, 16), ['save', 'turn:next', 'turn:next', 'turn:next', 'turn:next-key', 'turn:next-key',
    'turn:next-key', 'turn:next-key-left', 'turn:next-key-left', 'turn:next-key-left', 'rest']);
  assert.strictEqual(short.filter((x) => x === 'rest').length, RESTS);
  assert.strictEqual(short.at(-1), 'done:turns stopped at page 2 of 300');
  const z = screen('z', 296, 300);
  assert.strictEqual(replay([a, a, a, a, z, z, z, z]).actions.at(-1), 'done:turns stopped at page 296 of 300');
  const x = screen('x'), y = screen('y');
  // The first unnumbered screen is observed three more times for a footer that comes late.
  const unnumbered = replay([x, x, x, x, x, x, x, y, y, y, y]).actions;
  assert.deepStrictEqual(unnumbered.slice(0, 4), ['wait', 'wait', 'wait', 'save']);
  assert.strictEqual(unnumbered.at(-1), 'done:turns stopped on an unnumbered screen');
});

test('a reader that will not go back to page 1 fails the capture where it stuck', () => {
  const c = screen('c', 3, 4);
  const { actions } = replay(Array(41).fill(c));
  assert.deepStrictEqual(actions.slice(0, 11), ['save', 'turn:prev', 'turn:prev', 'turn:prev',
    'turn:prev-key', 'turn:prev-key', 'turn:prev-key', 'turn:prev-key-right', 'turn:prev-key-right',
    'turn:prev-key-right', 'rest']);
  assert.strictEqual(actions.filter((x) => x === 'rest').length, RESTS);
  assert.strictEqual(actions.at(-1), 'fail:could not page back past page 3 of 4 by button or key');
});

test('a book read right to left, whose left arrow key turns forward, pages back by the right arrow key', () => {
  // 2026-10-12: 英語教育幻想 stuck at 2037 of 2566 with the button; the left key took it forward to 2320 and the capture
  // failed, saving four later screens again as earlier ones.
  const a = screen('a', 1, 5), b = screen('b', 2, 5), c = screen('c', 3, 5);
  const d = screen('d', 4, 5), e = screen('e', 5, 5);
  const { actions, state } = replay([d, c, c, c, c, d, c, b, a, a, a, a, b, c, d, e, e, e, e]);
  assert.deepStrictEqual(actions.slice(0, 12), ['save', 'turn:prev', 'save', 'turn:prev', 'turn:prev', 'turn:prev',
    'turn:prev-key', 'turn:prev-key-right', 'turn:prev-key-right', 'save', 'turn:prev-key-right', 'save']);
  assert.strictEqual(actions.at(-1), 'done:page 5 of 5');
  assert.deepStrictEqual(order(state.saved), ['a', 'b', 'c', 'd', 'e']);
  assert.strictEqual(state.start, 'opened at 4/5; 3 earlier screens saved back to page 1 (the last ones by the right arrow key)');
});

test('paging back that goes forward by the button and both keys fails instead of saving later screens', () => {
  const p = (n) => screen(`p${n}`, n, 20);
  const { actions, state } = replay([2, 3, 4, 5, 5, 6, 7, 8, 8, 9, 10, 11, 11, 12, 13, 14].map(p));
  assert.deepStrictEqual(actions.slice(0, 6), ['save', 'turn:prev', 'turn:prev-key', 'turn:prev-key-right', 'rest', 'turn:prev']);
  assert.strictEqual(actions.at(-1), 'fail:paging back went forward from page 13 to 14 by button and both keys');
  assert.deepStrictEqual(Object.keys(state.saved), ['p2']);
});

test('after a rest, screens a wrong-way turn carried the reader past are not saved out of order', () => {
  const b = screen('b', 2, 9), c = screen('c', 3, 9), d = screen('d', 4, 9), e = screen('e', 5, 9), a = screen('a', 1, 9);
  const { actions, state } = replay([b, c, d, e, e, d, c, b, a, a, a, a]);
  assert.deepStrictEqual(actions.slice(0, 10), ['save', 'turn:prev', 'turn:prev-key', 'turn:prev-key-right', 'rest',
    'turn:prev', 'turn:prev', 'turn:prev', 'turn:prev', 'save']);
  assert.deepStrictEqual(order(state.saved), ['a', 'b']);
});

test('a reader stuck on Previous page pages back by the left arrow key', () => {
  // 2026-10-12: 英語教育幻想 stuck at 784 of 2566 with the button.
  const c = screen('c', 3, 4), b = screen('b', 2, 4), a = screen('a', 1, 4), d = screen('d', 4, 4);
  const { actions, state } = replay([c, c, c, c, b, a, a, a, a, c, d, d, d, d]);
  assert.deepStrictEqual(actions.slice(0, 9), ['save', 'turn:prev', 'turn:prev', 'turn:prev', 'turn:prev-key', 'save',
    'turn:prev-key', 'save', 'turn:prev-key']);
  assert.strictEqual(actions.at(-1), 'done:page 4 of 4');
  assert.strictEqual(state.start, 'opened at 3/4; 2 earlier screens saved back to page 1 (the last ones by the left arrow key)');
});

test('a reader stuck on Next page mid-book pages on by the right arrow key, then by the left in a book read right to left', () => {
  // 2026-10-12: 代替医療解剖 stopped at 219 of 528 when Next page stopped moving the reader, and the capture ended there.
  const a = screen('a', 1, 9), b = screen('b', 2, 9), c = screen('c', 3, 9), d = screen('d', 4, 9), i = screen('i', 9, 9);
  const ltr = replay([a, a, a, a, b, c, c, c, c, d, i, i, i, i]);
  assert.deepStrictEqual(ltr.actions.slice(7, 13), ['save', 'turn:next', 'turn:next', 'turn:next', 'turn:next-key', 'save']);
  assert.strictEqual(ltr.actions.at(-1), 'done:page 9 of 9');
  assert.deepStrictEqual(order(ltr.state.saved), ['a', 'b', 'c', 'd', 'i']);
  // Right to left, the right arrow key turns back: that turn is not saved and the left key takes over.
  const rtl = replay([a, a, a, a, b, c, c, c, c, b, d, i, i, i, i]);
  assert.deepStrictEqual(rtl.actions.slice(7, 14), ['save', 'turn:next', 'turn:next', 'turn:next', 'turn:next-key',
    'turn:next-key-left', 'save']);
  assert.deepStrictEqual(order(rtl.state.saved), ['a', 'b', 'c', 'd', 'i']);
});

test('a reader flipping between screens it already showed on one page is stuck after 30 turns', () => {
  const a = screen('a', 1, 9), p = screen('p', 5, 9), q = screen('q', 5, 9);
  const flips = Array.from({ length: STUCK_TURNS }, (_, i) => (i % 2 ? q : p));
  const { actions } = replay([a, a, a, a, p, q, ...flips]);
  assert.strictEqual(actions.at(-1), 'fail:the reader stayed on page 5 for 30 turns');
  assert.strictEqual(actions.filter((x) => x === 'turn:next').length, 3 + STUCK_TURNS - 1);
});

test('new screens under one page number are progress, not a stuck reader', () => {
  // Facing the Mountain on 1.6: the notes ran past 30 screens all showing page 540 of 540.
  const notes = Array.from({ length: STUCK_TURNS * 2 }, (_, i) => screen(`note${i}`, 540, 540));
  const first = screen('first', 1, 540);
  const { actions } = replay([first, first, first, first, ...notes, notes.at(-1), notes.at(-1), notes.at(-1)]);
  assert.strictEqual(actions.at(-1), 'done:page 540 of 540');
});

test('turns past three per page stop the capture, each direction counted on its own', () => {
  const pages = 10;
  const limit = TURNS_PER_PAGE * pages + EXTRA_TURNS;
  const start = screen('s', 1, pages);
  const endless = Array.from({ length: limit + 5 }, (_, i) => screen(`f${i}`, 1 + Math.floor((i * pages) / (limit + 5)), pages));
  const { actions } = replay([start, start, start, start, ...endless]);
  assert.strictEqual(actions.at(-1), `fail:${limit + 1} turns forward, more than the ${limit} a book of ${pages} pages needs`);
});

test('a book that only opens in the Kindle app is refused before anything is saved', () => {
  const { actions } = replay([{ sha: null, position: null, appOnly: true, signedOut: false }]);
  assert.deepStrictEqual(actions, ['fail:kindle app required: Cloud Reader cannot open this book']);
});

test('a signed-out reader is refused', () => {
  assert.deepStrictEqual(replay([{ sha: null, position: null, appOnly: false, signedOut: true }]).actions,
    ['fail:signed out of Cloud Reader']);
});

test('a screen whose image is not there yet is observed again before the capture fails', () => {
  const blank = { sha: null, position: { n: 81, m: 2566 }, appOnly: false, signedOut: false };
  const b = screen('b', 92, 2566), a = screen('a', 82, 2566), c = screen('c', 81, 2566);
  const { actions } = replay([b, b, a, blank, blank, c]); // the jump to the start does not move this reader
  assert.deepStrictEqual(actions.slice(1, 8), ['save', 'turn:prev', 'save', 'turn:prev', 'wait', 'wait', 'save']);
  const stuck = replay([b, b, ...Array(IMAGE_WAITS + 1).fill(blank)]);
  assert.strictEqual(stuck.actions.at(-1), `fail:no page image after ${IMAGE_WAITS} s`);
  assert.strictEqual(replay([blank]).actions[0], 'fail:no page image');
});

test('forward from page 1, a screen before the opening one is not saved again under a new hash', () => {
  // 2026-10-12: 英語教育幻想 saved 13 screens twice; the canvas drew them again with other pixels.
  const a = screen('a', 1, 4), b = screen('b', 2, 4), c = screen('c', 3, 4), d = screen('d', 4, 4);
  const b2 = screen('b2', 2, 4);
  const { state, actions } = replay([c, b, a, a, a, a, b2, c, d, d, d, d]);
  assert.deepStrictEqual(order(state.saved), ['a', 'b', 'c', 'd']);
  assert.strictEqual(actions.at(-1), 'done:page 4 of 4');
});

test('a book opened far from its start jumps to the start before it saves anything, and pages back as before when the jump does not move it', () => {
  const end = screen('end', 389, 389), a = screen('a', 1, 389), b = screen('b', 2, 389);
  const jumped = replay([end, a, a, a, a, b, b, b, b]);
  assert.deepStrictEqual(jumped.actions.slice(0, 6), ['jump:start', 'save', 'turn:prev', 'turn:prev', 'turn:prev', 'turn:next']);
  assert.deepStrictEqual(jumped.state.saved, { a: 0, b: 1 });
  assert.strictEqual(jumped.state.start, 'opened at 389/389, jumped to 1/389; 0 earlier screens saved back to page 1');
  const y = screen('y', 388, 389);
  const stayed = replay([end, end, y, y, y, y, y]);
  assert.deepStrictEqual(stayed.actions.slice(0, 4), ['jump:start', 'save', 'turn:prev', 'save']);
  assert.deepStrictEqual(stayed.state.saved, { end: 0, y: -1 });
  assert.strictEqual(replay([screen('c', 3, 4)]).actions[0], 'save');
});

test('a reader that stops turning mid-book both ways rests and starts over with the button before the capture gives up', () => {
  // Facing the Mountain (2026-10-12): Next page and the right key stopped at 275 of 540, the left key went back to 274.
  const a = screen('a', 1, 540), b = screen('b', 274, 540), c = screen('c', 275, 540), d = screen('d', 276, 540);
  const resumed = replay([a, a, a, a, b, c, c, c, c, c, c, c, b, b, c, d]);
  assert.deepStrictEqual(resumed.actions.slice(9, 19), ['turn:next', 'turn:next', 'turn:next-key', 'turn:next-key',
    'turn:next-key', 'turn:next-key-left', 'rest', 'turn:next', 'turn:next', 'save']);
  assert.deepStrictEqual(order(resumed.state.saved), ['a', 'b', 'c', 'd']);
  const stuck = replay([a, a, a, a, ...Array(40).fill(c)]);
  assert.strictEqual(stuck.actions.filter((x) => x === 'rest').length, RESTS);
  assert.strictEqual(stuck.actions.at(-1), 'done:turns stopped at page 275 of 540');
});

test('after the jump to the start, a screen without its image yet is waited for', () => {
  // 2026-10-12, extension 2.7: History of Jazz and Steve Jobs jumped from 180 and 238 and failed "no page image" at once.
  const end = screen('end', 180, 389), blank = { sha: null, position: null, appOnly: false, signedOut: false };
  const a = screen('a', 1, 389);
  assert.deepStrictEqual(replay([end, blank, blank, a]).actions.slice(0, 4), ['jump:start', 'wait', 'wait', 'save']);
  assert.strictEqual(replay([end, ...Array(3 * IMAGE_WAITS + 1).fill(blank)]).actions.at(-1), `fail:no page image after ${3 * IMAGE_WAITS} s`);
});

test('a reader stuck after Roman-numbered front matter pages on by the arrow key', () => {
  // 2026-10-13: Automate the Boring Stuff paged from "Page x of 552" to "Page xx of 552", then Next page stopped moving the
  // reader on a screen whose footer was empty, and the capture ended there as done after 52 screens.
  const fs = require('node:fs');
  const path = require('node:path');
  const trace = fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'traces', 'B07VSXS4NK-2026-10-13.jsonl'), 'utf8')
    .trim().split('\n').map((line) => JSON.parse(line));
  let state = initialState();
  const actions = trace.map(({ observation }) => {
    const result = nextAction(state, observation);
    state = result.state;
    return result.action;
  });
  assert.deepStrictEqual(actions.slice(0, -1), trace.slice(0, -1).map((step) => step.action));
  assert.strictEqual(trace.at(-1).action, 'done:turns stopped on an unnumbered screen');
  assert.strictEqual(actions.at(-1), 'turn:next-key');
});

test('a reader stuck on an unnumbered screen far from the end pages on by the arrow key', () => {
  // 2026-10-12: Automate the Boring Stuff jumped from 70 of 552 to location 1 of 15988, the footer then showed no
  // position, and Next page stopped moving the reader on screen 18 (the contents). The capture ended there as done.
  const fs = require('node:fs');
  const path = require('node:path');
  const trace = fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'traces', 'B07VSXS4NK-2026-10-12.jsonl'), 'utf8')
    .trim().split('\n').map((line) => JSON.parse(line));
  let state = initialState();
  const actions = trace.map(({ observation }) => {
    const result = nextAction(state, observation);
    state = result.state;
    return result.action;
  });
  assert.deepStrictEqual(actions.slice(0, -1), trace.slice(0, -1).map((step) => step.action));
  assert.strictEqual(trace.at(-1).action, 'done:turns stopped on an unnumbered screen');
  assert.strictEqual(actions.at(-1), 'turn:next-key');
  // Unnumbered last screens of a book whose last numbered page was near the end still end it.
  const a = screen('a', 1, 9), h = screen('h', 9, 9), u = screen('u');
  assert.strictEqual(replay([a, a, a, a, h, u, u, u, u]).actions.at(-1), 'done:turns stopped on an unnumbered screen');
});

test('a first screen without its footer yet waits for it, then jumps to the start from far in', () => {
  // So You Want to Talk About Race (2026-10-13): no position on the first screen, 580 of 3301 a second later.
  const late = { ...screen('a'), footer: '' };
  const far = screen('a', 580, 3301);
  assert.deepStrictEqual(replay([late, far]).actions, ['wait', 'jump:start']);
});

test('paging back, an unnumbered screen that stops turning far from page 1 is not taken for the start', () => {
  // So You Want to Talk About Race (2026-10-13) froze on an unnumbered screen after page 197 and went forward from there.
  const b = screen('b', 12, 400), c = screen('c', 13, 400), x = screen('x');
  const { actions, state } = replay([c, b, ...Array(60).fill(x)]);
  // Near page 1 it is the cover, as before.
  const near = [screen('c', 3, 400), screen('b', 2, 400), x, x, x, x, screen('y')];
  assert.strictEqual(replay(near).state.phase, 'fwd');
  assert.ok(!actions.includes('turn:next'));
  assert.strictEqual(state.phase, 'back');
  assert.strictEqual(actions.at(-1), 'fail:could not page back past page 12 of 400 by button or key');
});

test('a reader that froze in a hidden window rests until the window comes to the front', () => {
  // So You Want to Talk About Race (2026-10-13, 2.14): hidden throughout while the Mac was in use, it froze at 178 of 3301.
  const a = screen('a', 1, 540), c = screen('c', 275, 540), d = screen('d', 276, 540);
  const hidden = (s) => ({ ...s, hidden: true });
  const { actions, state } = replay([a, a, a, a, ...Array(60).fill(hidden(c)), c, d]);
  assert.ok(actions.filter((x) => x === 'rest').length > RESTS);
  assert.ok(!actions.some((x) => x.startsWith('done:') && x.includes('275')));
  assert.ok('d' in state.saved);
});

test('a hidden reader that stops turning near the end rests instead of ending the capture', () => {
  // The History of Jazz (2026-10-13 23:10 JST, 2.15): the display went off, the window was hidden, and the reader stopped
  // turning on an unnumbered screen after 389 of 389. The capture ended there without its Acknowledgments and Index.
  const hidden = (s) => ({ ...s, hidden: true });
  const a = screen('a', 1, 389), y = screen('y', 389, 389), z = screen('z', null, null), ix = screen('ix', null, null);
  const { actions, state } = replay([a, a, a, a, y, ...Array(40).fill(hidden(z)), z, ix, ix, ix, ix, ix, ix, ix, ix, ix, ix]);
  const ended = actions.findIndex((x) => x.startsWith('done:'));
  assert.ok(actions.slice(0, ended).includes('rest'));
  assert.ok('ix' in state.saved);
  assert.ok(actions[ended].startsWith('done:'));
});

test('screens past a change of the footer unit are saved, not taken for screens before the last saved page', () => {
  // So You Want to Talk About Race (2026-10-14): after location 3080 of 3301 the footer showed "256 of 256" for the
  // last 34 screens. They were compared with location 3080, taken for screens already passed and not saved, and the
  // capture ended as done short of its end.
  const fs = require('node:fs');
  const path = require('node:path');
  const trace = fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'traces', 'B07QBQF4GS-2026-10-14.jsonl'), 'utf8')
    .trim().split('\n').map((line) => JSON.parse(line));
  const switched = trace.findIndex((step, i) => i > 0 && step.observation.position?.m === 256);
  let state = initialState();
  const step = (observation) => {
    const result = nextAction(state, observation);
    state = result.state;
    return result.action;
  };
  assert.deepStrictEqual(trace.slice(0, switched).map(({ observation }) => step(observation)),
    trace.slice(0, switched).map(({ action }) => action));
  // From the switch on, each recorded screen stays until the policy turns away from it, as the reader does.
  const saved = new Set();
  let action;
  for (const { observation } of trace.slice(switched)) {
    action = step(observation);
    if (action === 'save') {
      saved.add(observation.sha);
      action = step(observation);
    }
  }
  assert.deepStrictEqual(saved, new Set(trace.slice(switched).map(({ observation }) => observation.sha)));
  assert.strictEqual(action, 'done:page 256 of 256');
});

test('front matter numbered another way than the pages is the start once the reader has shown page 1', () => {
  // 2026-10-14: In the Plex paged back from page 1 of 456 through its Roman pages to location 7 of 10362, which no turn
  // moved; the right arrow key then went forward and the capture failed. マニャーナの法則 stuck the same way at location
  // 3 of 2389 after page 1 of 246.
  const roman = (sha, numeral) => ({ ...screen(sha), footer: `Page ${numeral} of 456 ● 0%` });
  const cover = screen('cover', 7, 10362), i = roman('i', 'I'), viii = roman('viii', 'VIII');
  const one = screen('one', 1, 456), two = screen('two', 2, 456), three = screen('three', 456, 456);
  const { state, actions } = replay([one, viii, i, cover, cover, cover, cover, i, viii, one, two, three, three, three, three]);
  assert.deepStrictEqual(actions.slice(0, 11), ['save', 'turn:prev', 'save', 'turn:prev', 'save', 'turn:prev', 'save',
    'turn:prev', 'turn:prev', 'turn:prev', 'turn:next']);
  assert.strictEqual(actions.at(-1), 'done:page 456 of 456');
  assert.deepStrictEqual(order(state.saved), ['cover', 'i', 'viii', 'one', 'two', 'three']);
  assert.strictEqual(state.start, 'opened at 1/456; 3 earlier screens saved back to 7/10362');
  // Without page 1 shown, a screen that stops turning far from it is still no start.
  const stuck = replay(Array(41).fill(screen('loc', 40, 2389))).actions;
  assert.strictEqual(stuck.at(-1), 'fail:could not page back past page 40 of 2389 by button or key');
});

test('a screen within a few locations of 1 that no way pages back from is the start, page 1 shown or not', () => {
  // 2026-10-15: マニャーナの法則 reopened at location 3 of 2389, where its last capture had stopped, so the reader never
  // showed page 1 of 246 before it stuck; the capture failed with one screen saved. check_book.py still judges the start.
  const loc3 = screen('loc3', 3, 2389), loc4 = screen('loc4', 4, 2389), one = screen('one', 1, 246), end = screen('end', 246, 246);
  const { state, actions } = replay([loc3, loc3, loc3, loc3, loc3, loc3, loc3, loc3, loc3, loc3, loc4, one, end, end, end, end]);
  assert.strictEqual(actions.at(-1), 'done:page 246 of 246');
  assert.deepStrictEqual(order(state.saved), ['loc3', 'loc4', 'one', 'end']);
  assert.strictEqual(state.start, 'opened at 3/2389; 0 earlier screens saved back to 3/2389 (the last ones by the right arrow key)');
});

test('a screen at location 7 of 10362 that no way pages back from is the start of a book that long', () => {
  // 2026-10-15: In the Plex reopened at location 7 of 10362, where its last capture had stopped, and never showed page 1
  // of 456; the capture failed with one screen saved, 6 locations from 1 being past the 5 allowed.
  const loc7 = screen('loc7', 7, 10362), loc8 = screen('loc8', 8, 10362), one = screen('one', 1, 456), end = screen('end', 456, 456);
  const { state, actions } = replay([loc7, loc7, loc7, loc7, loc7, loc7, loc7, loc7, loc7, loc7, loc8, one, end, end, end, end]);
  assert.strictEqual(actions.at(-1), 'done:page 456 of 456');
  assert.deepStrictEqual(order(state.saved), ['loc7', 'loc8', 'one', 'end']);
});

test('the turn cap counts the finer numbering once the reader has shown it', () => {
  // 2026-10-15: In the Plex paged forward from location 7 of 10362 through 456 pages at about 3 screens a page and was
  // stopped by the cap for 456 pages (1418 turns) at page 441. A book numbered by location gets 3 turns a location.
  const pages = 10;
  const screens = Array.from({ length: TURNS_PER_PAGE * pages + EXTRA_TURNS + 20 }, (_, i) => screen(`s${i}`, 1 + Math.floor((i * pages) / (TURNS_PER_PAGE * pages + EXTRA_TURNS + 20)), pages));
  const one = screen('one', 1, pages), loc = screen('loc', 2, 300), last = screens.at(-1);
  const { actions } = replay([one, loc, loc, loc, loc, ...screens.slice(1), last, last, last]);
  assert.strictEqual(actions.at(-1), `done:page ${pages} of ${pages}`);
});
