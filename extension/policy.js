// Every paging decision of a capture, as one pure function, shared by content.js and policy.test.js.
// The reader opens where the book was last read. The capture saves every new screen while paging back to the first
// one, then pages forward to the end, skipping screens it already saved. Screens are numbered by where they sit from
// the opening screen (0): earlier ones -1, -2, ... and later ones 1, 2, ..., so reading order is the numeric order.
//
// observation: { position: {n, m} | null, sha: string | null, appOnly: bool, signedOut: bool, footer?: string }
// action: 'save' (state.n is the screen's number; call again with the same observation), 'wait' (observe again after a
// second), 'turn:prev', 'turn:prev-key' (the left arrow key instead of the button), 'turn:prev-key-right' (the right
// arrow key, for a book read right to left), 'turn:next', 'turn:next-key' (the right arrow key), 'turn:next-key-left'
// (the left arrow key, for a book read right to left),
// 'rest' (wait, then observe again), 'jump:start' (the scrubber to the book's first page, then observe again), 'done:<proof>' or 'fail:<reason>'.

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
// Paging back a whole book is slow and the reader stops going back partway: opened near the end, Python Crash Course,
// The History of Jazz and Steve Jobs (2026-10-12) each stuck about halfway back, where neither Previous page nor the left
// arrow key moved them. A book opened further in than this first jumps to its start with the reader's scrubber
// (main.js); if the jump does not move it, it pages back as before.
const JUMP_FROM = 10;
const APP_ONLY_REASON = 'kindle app required: Cloud Reader cannot open this book';

function initialState() {
  return { phase: 'back', saved: {}, low: 1, high: 0, n: null, last: null, pending: false, tries: 0, turns: 0, limit: 0,
    page: null, pages: null, same: 0, waits: 0, rests: 0, savedPage: null, keyed: false, jumped: null, opened: '', openedAt: null, start: '', known: null };
}

const limitFor = (position) => TURNS_PER_PAGE * (position?.m || UNKNOWN_LENGTH) + EXTRA_TURNS;
const where = (position) => (position ? `${position.n}/${position.m}` : 'an unnumbered screen');
// The keys tried in turn when Previous page stops moving the reader back. In a book read right to left (vertical
// Japanese) the left arrow key turns forward: 英語教育幻想 (2026-10-12) stuck at 2037 with the button, then the left key
// took it forward to 2320. A turn that lands on a later page moves on to the next way of paging back.
// Forward is the mirror: 代替医療解剖 (2026-10-12) stopped at 219 of 528 when Next page stopped moving the reader.
const WAYS = { back: [false, 'left', 'right'], fwd: [false, 'right', 'left'] };
const nextWay = (state) => WAYS[state.phase][WAYS[state.phase].indexOf(state.keyed) + 1];
const KEY_ACTIONS = { back: { left: 'turn:prev-key', right: 'turn:prev-key-right' },
  fwd: { right: 'turn:next-key', left: 'turn:next-key-left' } };
const turn = (state) => ({ state, action: KEY_ACTIONS[state.phase][state.keyed] || (state.phase === 'back' ? 'turn:prev' : 'turn:next') });
// The last pages a footer counts can be blank or the back matter's: 英語教育幻想 ended at 2561 of 2566. Short of them,
// a forward edge tries the keys before it ends the capture.
// A front-matter page numbered in Roman ("Page xx of 552 ● 2%", Automate the Boring Stuff 2026-10-13) has no position,
// but it is near the start: the screen after it that stops turning is judged as page 0, far from the end.
const frontMatter = (footer) => {
  const m = (footer || '').match(/Page [ivxlcdm]+ of (\d+)/i);
  return m ? { n: 0, m: Number(m[1]) } : null;
};
const nearEnd = (position) => position.m - position.n <= Math.max(5, Math.ceil(position.m * 0.02));
const fail = (state, reason) => ({ state, action: `fail:${reason}` });
// A reader can stop turning both ways for a while: Facing the Mountain (2026-10-12) stuck at 275 of 540 by the button and
// both keys, and History of Jazz ignored single turns more and more often before it stuck. Where every way has failed
// mid-book, the capture rests (content.js waits REST_MS) and starts over with the button, this many times per book.
const RESTS = 3;
const rest = (state) => ({ state: { ...state, keyed: false, tries: 0, same: 0, rests: state.rests + 1, pending: true }, action: 'rest' });

function save(state, sha) {
  const n = state.phase === 'back' ? state.low - 1 : state.high + 1;
  const low = Math.min(state.low, n);
  const high = Math.max(state.high, n);
  return { state: { ...state, saved: { ...state.saved, [sha]: n }, n, low, high, last: sha, pending: true, savedPage: state.page }, action: 'save' };
}

function edge(state, position) {
  if (state.phase === 'back') {
    // 英語教育幻想 (2026-10-12) opened at 2561 of 2566 and paged back to 784, where Previous page stopped moving it. Why
    // is not known yet; the left arrow key is tried before the capture fails, and dom-probe-stopped.json records the
    // reader's controls where it stuck.
    if (position && position.n !== 1 && nextWay(state)) return turn({ ...state, keyed: nextWay(state), tries: 0 });
    if (position && position.n !== 1 && state.rests < RESTS) return rest(state);
    if (position && position.n !== 1) return fail(state, `could not page back past page ${position.n} of ${position.m} by button or key`);
    const start = `opened at ${state.opened}; ${-state.low} earlier screens saved back to ${position ? 'page 1' : 'the first screen'}`
      + (state.keyed ? ` (the last ones by the ${state.keyed} arrow key)` : '');
    return turn({ ...state, phase: 'fwd', keyed: false, tries: 0, turns: 0, same: 0, start });
  }
  // An unnumbered screen is judged by the last numbered one: Automate the Boring Stuff (2026-10-12) showed no position
  // after the jump to location 1 of 15988 and stuck on its contents, 18 screens in.
  const at = position || state.known;
  if (at && !nearEnd(at) && nextWay(state)) return turn({ ...state, keyed: nextWay(state), tries: 0 });
  if (at && !nearEnd(at) && state.rests < RESTS) return rest(state);
  const proof = position
    ? (position.n === position.m ? `page ${position.m} of ${position.m}` : `turns stopped at page ${position.n} of ${position.m}`)
    : 'turns stopped on an unnumbered screen';
  return { state, action: `done:${proof}` };
}

function nextAction(state, observation) {
  const { position, sha, appOnly, signedOut, footer } = observation;
  if (appOnly) return fail(state, APP_ONLY_REASON);
  if (signedOut) return fail(state, 'signed out of Cloud Reader');
  // Right after the jump to the start the reader shows no page for a while (History of Jazz, 2026-10-12), so it gets longer.
  const waits = state.jumped && state.last === null ? 3 * IMAGE_WAITS : IMAGE_WAITS;
  if (!sha && ((state.last === null && !state.jumped) || state.waits >= waits)) return fail(state, `no page image${state.waits ? ` after ${state.waits} s` : ''}`);
  if (!sha) return { state: { ...state, waits: state.waits + 1 }, action: 'wait' };
  if (state.waits) state = { ...state, waits: 0 };
  if (state.last === null && state.jumped === null && position && position.n > JUMP_FROM) {
    return { state: { ...state, jumped: where(position) }, action: 'jump:start' };
  }
  const opened = state.jumped && state.jumped !== where(position) ? `${state.jumped}, jumped to ${where(position)}` : where(position);
  if (state.last === null) return save({ ...state, opened, openedAt: position ? position.n : null, page: position ? position.n : null, pages: position ? position.m : null, known: position || frontMatter(footer), limit: limitFor(position) }, sha);
  if (state.pending) return turn({ ...state, pending: false });

  const turns = state.turns + 1;
  const limit = state.limit;
  if (turns > limit) return fail(state, `${turns} turns ${state.phase === 'back' ? 'back' : 'forward'}, more than the ${limit} a book of ${position?.m || 'unknown'} pages needs`);
  const fresh = sha !== state.last && !(sha in state.saved);
  const n = position ? position.n : null;
  const same = n !== null && n === state.page && !fresh ? state.same + 1 : 0;
  if (same >= STUCK_TURNS) return fail(state, `the reader stayed on page ${n} for ${STUCK_TURNS} turns`);
  const next = { ...state, turns, same, page: n, pages: position ? position.m : null, known: position || frontMatter(footer) || state.known };
  const wrongWay = n !== null && state.page !== null && position.m === state.pages
    && (state.phase === 'back' ? n > state.page : n < state.page);
  if (wrongWay) {
    if (!nextWay(state) && state.rests < RESTS) return rest({ ...next, last: sha });
    if (!nextWay(state)) return fail(state, `paging ${state.phase === 'back' ? 'back went forward' : 'forward went back'} from page ${state.page} to ${n} by button and both keys`);
    return turn({ ...next, keyed: nextWay(state), tries: 0, last: sha });
  }

  if (sha === state.last) {
    const tries = state.tries + 1;
    return tries < NEXT_ATTEMPTS ? turn({ ...next, tries }) : edge({ ...next, tries }, position);
  }
  if (sha in state.saved) return turn({ ...next, tries: 0, last: sha });
  // Forward from page 1, the screens before the opening one were all saved on the way back, though a canvas can draw
  // one again with other pixels: 英語教育幻想 (2026-10-12) saved 13 of them twice under new hashes.
  if (state.phase === 'fwd' && n !== null && state.openedAt !== null && n < state.openedAt) return turn({ ...next, tries: 0, last: sha });
  // A turn the wrong way can carry the reader past screens never saved, and coming back through them they look fresh.
  // A screen beyond the last saved page is not saved out of order: back past it, forward short of it.
  if (n !== null && state.savedPage !== null && (state.phase === 'back' ? n > state.savedPage : n < state.savedPage)) {
    return turn({ ...next, tries: 0, last: sha });
  }
  return save({ ...next, tries: 0 }, sha);
}

if (typeof module !== 'undefined') {
  module.exports = { initialState, nextAction, RESTS, NEXT_ATTEMPTS, TURNS_PER_PAGE, EXTRA_TURNS, UNKNOWN_LENGTH, STUCK_TURNS, IMAGE_WAITS, APP_ONLY_REASON };
}
