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

test('an end the footer does not confirm is still the end; check_book.py judges how short it stopped', () => {
  const a = screen('a', 1, 300), b = screen('b', 2, 300);
  assert.strictEqual(replay([a, a, a, a, b, b, b, b]).actions.at(-1), 'done:turns stopped at page 2 of 300');
  const x = screen('x'), y = screen('y');
  assert.strictEqual(replay([x, x, x, x, y, y, y, y]).actions.at(-1), 'done:turns stopped on an unnumbered screen');
});

test('a reader that will not go back to page 1 fails the capture where it stuck', () => {
  const c = screen('c', 3, 4);
  assert.deepStrictEqual(replay([c, c, c, c, c, c, c]).actions, ['save', 'turn:prev', 'turn:prev', 'turn:prev',
    'turn:prev-key', 'turn:prev-key', 'turn:prev-key', 'fail:could not page back past page 3 of 4 by button or key']);
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
  const endless = Array.from({ length: limit + 5 }, (_, i) => screen(`f${i}`, 1 + (i % pages), pages));
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
  const { actions } = replay([b, a, blank, blank, c]);
  assert.deepStrictEqual(actions.slice(0, 7), ['save', 'turn:prev', 'save', 'turn:prev', 'wait', 'wait', 'save']);
  const stuck = replay([b, ...Array(IMAGE_WAITS + 1).fill(blank)]);
  assert.strictEqual(stuck.actions.at(-1), `fail:no page image after ${IMAGE_WAITS} s`);
  assert.strictEqual(replay([blank]).actions[0], 'fail:no page image');
});
