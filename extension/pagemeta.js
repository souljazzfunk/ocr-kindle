// Book title and a record of the reader's page structure, shared by content.js and pagemeta.test.js.
// The full-book reader on read.amazon.com has neither .book-title nor the text layer the sample reader has,
// and its structure can only be read inside Laiken's signed-in Brave, so a capture records it in the ZIP.

const GENERIC_TITLES = /^(kindle|amazon|kindle cloud reader|amazon kindle|read\.amazon\.\w+)$/i;

// candidates: texts from title elements and document.title, best first.
function pickTitle(candidates, asin) {
  for (const raw of candidates) {
    const title = (raw || '').replace(/\s*[|\-–—:]\s*(Kindle|Amazon)( Cloud Reader)?\s*$/i, '').trim();
    if (title && !GENERIC_TITLES.test(title)) return title.replace(/[\\/:*?"<>|~]/g, '_').slice(0, 120);
  }
  return asin || 'kindle-book';
}

const PROBE_DEPTH = 6;
const PROBE_TEXT = 120;

// Tag, id and classes of the elements a few levels down, with how many children each has.
// Text is cut to PROBE_TEXT characters so the record shows where the text lives without copying the book.
function describe(el, depth) {
  const node = {
    tag: el.tagName.toLowerCase(),
    id: el.id || undefined,
    class: typeof el.className === 'string' && el.className ? el.className : undefined,
    aria: el.getAttribute('aria-label') || undefined,
    children: el.children.length,
  };
  if (!el.children.length && el.textContent.trim()) node.text = el.textContent.trim().slice(0, PROBE_TEXT);
  if (depth < PROBE_DEPTH) node.kids = [...el.children].slice(0, 12).map((c) => describe(c, depth + 1));
  return node;
}

function probePage(doc) {
  const sample = (selector) => [...doc.querySelectorAll(selector)].slice(0, 8).map((el) => describe(el, PROBE_DEPTH - 2));
  return {
    title: doc.title,
    url: doc.location.pathname + doc.location.search,
    counts: {
      blobImages: doc.querySelectorAll('img[src^="blob:"]').length,
      canvases: doc.querySelectorAll('canvas').length,
      iframes: doc.querySelectorAll('iframe').length,
      textLayer: doc.querySelectorAll('#kr-renderer .kg-view > .kg-a11y-abs').length,
    },
    titleCandidates: sample('[class*="title" i], [data-testid*="title" i], h1'),
    // The reader's buttons and menu items by label, so a jump to the start can be built from what the reader shows
    // instead of paging back one turn at a time (Automate the Boring Stuff paged back from 517).
    controls: [...doc.querySelectorAll('button, [role="button"], [role="menuitem"], a[aria-label]')].slice(0, 80)
      .map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60)).filter(Boolean),
    a11y: sample('[class*="a11y" i], [aria-live], [role="document"], [role="main"]'),
    body: describe(doc.body, 0),
  };
}

// "Page 75 of 120" (or 位置/ページ/Location) from the reader's footer as { n, m }, or null when it shows none.
function parsePosition(text) {
  const m = (text || '').match(/(?:位置|ページ|Location|Page)\D{0,3}(\d+)\s*(?:\/|of)\s*(\d+)/);
  return m ? { n: Number(m[1]), m: Number(m[2]) } : null;
}

// Whether paging back has reached the start: the reader says page 1, or, when it shows no page number,
// the previous-page button stopped turning.
function atStart(pos, turned) {
  return pos ? pos.n === 1 : !turned;
}

// Caps on page turns. On 2026-10-10 Automate the Boring Stuff (552 pages, opened at 517) turned for over 15 minutes
// with nothing to stop it but the end of the book, and looked like an endless loop. A guard is called once per turn
// with the reader's position and returns why to stop, or '' to go on.
// One page can take more than one turn (figures, code listings): 1.5 paged back from 517 to 12 in 603 turns and was
// stopped by a cap of one turn per page. The stuck check catches a reader that does not move; the cap is only a backstop.
// A turn that brought a new screen is progress even when the page number stays: Facing the Mountain's notes run for
// more than 30 screens all numbered 540 of 540, and 1.6 stopped there as stuck. The caller says so with fresh.
const TURNS_PER_PAGE = 3;
const EXTRA_TURNS = 50;
const UNKNOWN_LENGTH = 3000;
const STUCK_TURNS = 30;

function turnGuard(total) {
  const limit = TURNS_PER_PAGE * (total || UNKNOWN_LENGTH) + EXTRA_TURNS;
  let turns = 0;
  let last = null;
  let same = 0;
  return (pos, fresh = false) => {
    turns++;
    if (turns > limit) return `${turns} turns, more than the ${limit} a book of ${total || 'unknown'} pages needs`;
    const n = pos ? pos.n : null;
    if (n !== null && n === last && !fresh) {
      if (++same >= STUCK_TURNS) return `the reader stayed on page ${n} for ${STUCK_TURNS} turns`;
    } else {
      same = 0;
      last = n;
    }
    return '';
  };
}

// Some books open in Cloud Reader only as a notice that they need the Kindle app (2026-10-11, For 91 Days In
// Tokyo), with no page to capture. The words start the reason, so capture_book.py can tell this book-level refusal
// from a browser problem.
const APP_ONLY = 'kindle app required';

function appOnly(text) {
  return /Kindle App Is Required|can only be opened using Kindle app/i.test(text || '');
}

// A heartbeat in the tab title, which capture_book.py reads through AppleScript, so a capture that stopped moving is
// told from a long book that is still turning. On 2026-10-11 For 91 Days in Oviedo (265 pages) and Python Crash
// Course (500+) gave no ZIP in an hour, while Oviedo's recapture took two minutes, and nothing showed which it was.
// The page's visibility is in it too: Chrome slows the timers of a hidden tab, and the capture turns pages on timers.
const BEAT = /^\[kc [^\]]*\] /;

function heartbeat(title, stage, pos, visibility) {
  return `[kc ${stage} ${pos || '?'} ${visibility}] ${withoutHeartbeat(title)}`;
}

function withoutHeartbeat(title) {
  return (title || '').replace(BEAT, '');
}

if (typeof module !== 'undefined') module.exports = { pickTitle, probePage, parsePosition, atStart, turnGuard, appOnly, APP_ONLY, TURNS_PER_PAGE, EXTRA_TURNS, STUCK_TURNS, heartbeat, withoutHeartbeat };
