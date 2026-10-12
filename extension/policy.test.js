const test = require('node:test');
const assert = require('node:assert');
const { initialState, nextAction, STUCK_TURNS, TURNS_PER_PAGE, EXTRA_TURNS, IMAGE_WAITS } = require('./policy.js');

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
  assert.deepStrictEqual(replay([a, a, a, a, ...Array(10).fill(b)]).actions.slice(5), ['save', 'turn:next', 'turn:next',
    'turn:next', 'turn:next-key', 'turn:next-key', 'turn:next-key', 'turn:next-key-left', 'turn:next-key-left',
    'turn:next-key-left', 'done:turns stopped at page 2 of 300']);
  const z = screen('z', 296, 300);
  assert.strictEqual(replay([a, a, a, a, z, z, z, z]).actions.at(-1), 'done:turns stopped at page 296 of 300');
  const x = screen('x'), y = screen('y');
  assert.strictEqual(replay([x, x, x, x, y, y, y, y]).actions.at(-1), 'done:turns stopped on an unnumbered screen');
});

test('a reader that will not go back to page 1 fails the capture where it stuck', () => {
  const c = screen('c', 3, 4);
  assert.deepStrictEqual(replay([c, c, c, c, c, c, c, c, c, c]).actions, ['save', 'turn:prev', 'turn:prev', 'turn:prev',
    'turn:prev-key', 'turn:prev-key', 'turn:prev-key', 'turn:prev-key-right', 'turn:prev-key-right',
    'turn:prev-key-right', 'fail:could not page back past page 3 of 4 by button or key']);
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
  const b = screen('b', 2, 9), c = screen('c', 3, 9), d = screen('d', 4, 9), e = screen('e', 5, 9);
  assert.deepStrictEqual(replay([b, c, d, e]).actions, ['save', 'turn:prev', 'turn:prev-key', 'turn:prev-key-right',
    'fail:paging back went forward from page 4 to 5 by button and both keys']);
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
