// Every paging decision of a capture, as one pure function, shared by content.js and policy.test.js.
// The reader opens where the book was last read. The capture saves every new screen while paging back to the first
// one, then pages forward to the end, skipping screens it already saved. Screens are numbered by where they sit from
// the opening screen (0): earlier ones -1, -2, ... and later ones 1, 2, ..., so reading order is the numeric order.
//
// observation: { position: {n, m} | null, sha: string | null, appOnly: bool, signedOut: bool }
// action: 'save' (state.n is the screen's number; call again with the same observation), 'wait' (observe again after a
// second), 'turn:prev', 'turn:prev-key' (the left arrow key instead of the button), 'turn:prev-key-right' (the right
// arrow key, for a book read right to left), 'turn:next',
// 'done:<proof>' or 'fail:<reason>'.

const NEXT_ATTEMPTS = 3; // Cloud Reader occasionally ignores a turn, so an edge is 3 turns that leave the image unchanged
// Caps on page turns, each direction counted on its own. On 2026-10-10 Automate the Boring Stuff (552 pages, opened at
// 517) turned for over 15 minutes with nothing to stop it. One page can take more than one turn (figures, code
// listings): 1.5 paged back from 517 to 12 in 603 turns and was stopped by a cap of one turn per page.
const TURNS_PER_PAGE = 3;
const EXTRA_TURNS = 50;
const UNKNOWN_LENGTH = 3000;
// A turn onto a screen not seen before is progress even when the page number stays: Facing the Mountain's notes run
// for more than 30 screens all numbered 540 of 540.
const STUCK_TURNS = 30;
// A turn can land before the next page image is in place: 英語教育幻想 failed with "no page image" on its third screen
// (2026-10-12). A screen without an image is observed again, once a second, this many times before the capture fails.
const IMAGE_WAITS = 10;
const APP_ONLY_REASON = 'kindle app required: Cloud Reader cannot open this book';

function initialState() {
  return { phase: 'back', saved: {}, low: 1, high: 0, n: null, last: null, pending: false, tries: 0, turns: 0, limit: 0,
    page: null, same: 0, waits: 0, keyed: false, opened: '', openedAt: null, start: '' };
}

const limitFor = (position) => TURNS_PER_PAGE * (position?.m || UNKNOWN_LENGTH) + EXTRA_TURNS;
const where = (position) => (position ? `${position.n}/${position.m}` : 'an unnumbered screen');
// The keys tried in turn when Previous page stops moving the reader back. In a book read right to left (vertical
// Japanese) the left arrow key turns forward: 英語教育幻想 (2026-10-12) stuck at 2037 with the button, then the left key
// took it forward to 2320. A turn that lands on a later page moves on to the next way of paging back.
const BACK_WAYS = [false, 'left', 'right'];
const nextWay = (keyed) => BACK_WAYS[BACK_WAYS.indexOf(keyed) + 1];
const KEY_ACTIONS = { left: 'turn:prev-key', right: 'turn:prev-key-right' };
const turn = (state) => ({ state, action: state.phase === 'back' ? (KEY_ACTIONS[state.keyed] || 'turn:prev') : 'turn:next' });
const fail = (state, reason) => ({ state, action: `fail:${reason}` });

function save(state, sha) {
  const n = state.phase === 'back' ? state.low - 1 : state.high + 1;
  const low = Math.min(state.low, n);
  const high = Math.max(state.high, n);
  return { state: { ...state, saved: { ...state.saved, [sha]: n }, n, low, high, last: sha, pending: true }, action: 'save' };
}

function edge(state, position) {
  if (state.phase === 'back') {
    // 英語教育幻想 (2026-10-12) opened at 2561 of 2566 and paged back to 784, where Previous page stopped moving it. Why
    // is not known yet; the left arrow key is tried before the capture fails, and dom-probe-stopped.json records the
    // reader's controls where it stuck.
    if (position && position.n !== 1 && nextWay(state.keyed)) return turn({ ...state, keyed: nextWay(state.keyed), tries: 0 });
    if (position && position.n !== 1) return fail(state, `could not page back past page ${position.n} of ${position.m} by button or key`);
    const start = `opened at ${state.opened}; ${-state.low} earlier screens saved back to ${position ? 'page 1' : 'the first screen'}`
      + (state.keyed ? ` (the last ones by the ${state.keyed} arrow key)` : '');
    return turn({ ...state, phase: 'fwd', tries: 0, turns: 0, same: 0, start });
  }
  const proof = position
    ? (position.n === position.m ? `page ${position.m} of ${position.m}` : `turns stopped at page ${position.n} of ${position.m}`)
    : 'turns stopped on an unnumbered screen';
  return { state, action: `done:${proof}` };
}

function nextAction(state, observation) {
  const { position, sha, appOnly, signedOut } = observation;
  if (appOnly) return fail(state, APP_ONLY_REASON);
  if (signedOut) return fail(state, 'signed out of Cloud Reader');
  if (!sha && (state.last === null || state.waits >= IMAGE_WAITS)) return fail(state, `no page image${state.waits ? ` after ${state.waits} s` : ''}`);
  if (!sha) return { state: { ...state, waits: state.waits + 1 }, action: 'wait' };
  if (state.waits) state = { ...state, waits: 0 };
  if (state.last === null) return save({ ...state, opened: where(position), openedAt: position ? position.n : null, page: position ? position.n : null, limit: limitFor(position) }, sha);
  if (state.pending) return turn({ ...state, pending: false });

  const turns = state.turns + 1;
  const limit = state.limit;
  if (turns > limit) return fail(state, `${turns} turns ${state.phase === 'back' ? 'back' : 'forward'}, more than the ${limit} a book of ${position?.m || 'unknown'} pages needs`);
  const fresh = sha !== state.last && !(sha in state.saved);
  const n = position ? position.n : null;
  const same = n !== null && n === state.page && !fresh ? state.same + 1 : 0;
  if (same >= STUCK_TURNS) return fail(state, `the reader stayed on page ${n} for ${STUCK_TURNS} turns`);
  const next = { ...state, turns, same, page: n };
  if (state.phase === 'back' && n !== null && state.page !== null && n > state.page) {
    if (!nextWay(state.keyed)) return fail(state, `paging back went forward from page ${state.page} to ${n} by button and both keys`);
    return turn({ ...next, keyed: nextWay(state.keyed), tries: 0, last: sha });
  }

  if (sha === state.last) {
    const tries = state.tries + 1;
    return tries < NEXT_ATTEMPTS ? turn({ ...next, tries }) : edge({ ...next, tries }, position);
  }
  if (sha in state.saved) return turn({ ...next, tries: 0, last: sha });
  // Forward from page 1, the screens before the opening one were all saved on the way back, though a canvas can draw
  // one again with other pixels: 英語教育幻想 (2026-10-12) saved 13 of them twice under new hashes.
  if (state.phase === 'fwd' && n !== null && state.openedAt !== null && n < state.openedAt) return turn({ ...next, tries: 0, last: sha });
  return save({ ...next, tries: 0 }, sha);
}

if (typeof module !== 'undefined') {
  module.exports = { initialState, nextAction, NEXT_ATTEMPTS, TURNS_PER_PAGE, EXTRA_TURNS, UNKNOWN_LENGTH, STUCK_TURNS, IMAGE_WAITS, APP_ONLY_REASON };
}
