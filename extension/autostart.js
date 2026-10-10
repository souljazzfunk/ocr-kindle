// Rules for starting a capture, shared by background.js, content.js and autostart.test.js.
// A capture opened with #autocapture must run once: a reload, a restored tab or a second tab
// on the same book must not page through it again and download another ZIP.

const AUTOSTART_MARKER = '#autocapture';

// Returns the URL to put back in the address bar (marker removed) and the book's ASIN,
// or null when the page was not opened for an automatic capture.
function readAutoStart(href) {
  const url = new URL(href);
  if (url.hash !== AUTOSTART_MARKER) return null;
  url.hash = '';
  return { cleanUrl: url.href, asin: url.searchParams.get('asin') || '' };
}

// state: { active: { tabId, asin } | null, saved: [asin, ...] }
// Returns { ok, reason, state } where state is what to store when ok.
function claimCapture(state, { tabId, asin, auto }) {
  const { active, saved } = state;
  if (active && active.tabId !== tabId) {
    return { ok: false, reason: `tab ${active.tabId} is already capturing ${active.asin || 'a book'}`, state };
  }
  if (auto && !asin) return { ok: false, reason: 'auto start needs ?asin= in the URL', state };
  if (auto && saved.includes(asin)) return { ok: false, reason: `${asin} was already saved; click the icon to capture it again`, state };
  return { ok: true, reason: '', state: { active: { tabId, asin }, saved } };
}

function finishCapture(state, { tabId, asin, pages }) {
  if (state.active?.tabId !== tabId) return state;
  const saved = pages && asin && !state.saved.includes(asin) ? [...state.saved, asin] : state.saved;
  return { active: null, saved };
}

if (typeof module !== 'undefined') module.exports = { readAutoStart, claimCapture, finishCapture };
