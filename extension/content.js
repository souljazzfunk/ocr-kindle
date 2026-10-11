// Runs in every reader tab, but acts only in a tab the worker opened for a job. It observes the page, asks policy.js
// what to do, does it, and sends every page and event to the worker, which posts them to the channel.
const PAGE_WAIT_MS = 3000;
const FIRST_PAGE_WAIT_MS = 60000;
const JOB_ASKS = 10; // the worker stores the tab's job right after the window opens; the page may ask before that
const LIBRARY_SETTLE_MS = 3000;
const LIBRARY_WAIT_MS = 30000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const toWorker = (message) => chrome.runtime.sendMessage(message).catch((e) => ({ ok: false, reason: String(e) }));

function pageImage() {
  return [...document.querySelectorAll('img[src^="blob:"]')]
    .find((img) => img.getBoundingClientRect().width > 200);
}

function bookTitle(asin) {
  // .fixed-book-title and .top-chrome__book-title are the full-book reader's (dom-probe.json, AI for Educators).
  const element = document.querySelector('.book-title, .fixed-book-title, .top-chrome__book-title, [data-testid*="book-title" i], [class*="BookTitle"]');
  return pickTitle([element?.textContent, document.title], asin);
}

// Cloud Reader overlays the page image with a screen-reader text layer of <p> and <h1>-<h6> elements.
function pageMarkdown() {
  const layer = document.querySelector('#kr-renderer .kg-view > .kg-a11y-abs');
  if (!layer) return '';
  return [...layer.children].map((el) => {
    const level = /^H([1-6])$/.exec(el.tagName)?.[1];
    return level ? `${'#'.repeat(level)} ${el.textContent.trim()}` : el.textContent;
  }).join('\n\n').replace(/^\n+|\s+$/g, ''); // trim() would also drop a leading 　 indent
}

// The chevron ignores element.click(); it reacts to the full pointer sequence.
function press(label) {
  const button = document.querySelector(`[aria-label="${label}"]`);
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const Event = type.startsWith('pointer') ? PointerEvent : MouseEvent;
    button?.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, view: window }));
  }
}

async function pngOf(img) {
  await img.decode();
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas.toDataURL('image/png');
}

async function sha256(dataUrl) {
  const bytes = Uint8Array.from(atob(dataUrl.split(',')[1]), (c) => c.charCodeAt(0));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function observe() {
  const img = pageImage();
  const text = document.body.innerText;
  const png = img ? await pngOf(img) : null;
  return { position: parsePosition(text), sha: png ? await sha256(png) : null, appOnly: appOnly(text),
    signedOut: signedOut(document), src: img?.src ?? null, png };
}

async function waitForTurn(prevSrc) {
  for (let t = 0; t < PAGE_WAIT_MS; t += 100) {
    if (pageImage()?.src !== prevSrc) return;
    await sleep(100);
  }
}

const positionText = (position) => (position ? `${position.n}/${position.m}` : '');

async function capture(job) {
  for (let t = 0; t < FIRST_PAGE_WAIT_MS && !pageImage(); t += 500) {
    if (appOnly(document.body.innerText) || signedOut(document)) break;
    await sleep(500);
  }
  const event = (fields) => toWorker({ kc: 'event', event: { job: job.id, ...fields } });
  const positions = {};
  let state = initialState();
  let seen = await observe();
  for (;;) {
    const decided = nextAction(state, seen);
    state = decided.state;
    const { action } = decided;
    const { position, sha, appOnly: app, signedOut: out } = seen;
    await event({ type: 'trace', observation: { position, sha, appOnly: app, signedOut: out }, action });
    if (action === 'save') {
      if (!Object.keys(positions).length) {
        let probe;
        try { probe = probePage(document); } catch (e) { probe = { error: String(e) }; }
        await event({ type: 'title', title: bookTitle(job.asin), probe });
      }
      positions[state.n] = positionText(position);
      const sent = await toWorker({ kc: 'page', job: job.id, n: state.n, png: seen.png, position: positions[state.n],
        text: pageMarkdown(), sha });
      if (!sent?.ok) return; // the channel dropped the job; it reports the stall itself
    } else if (action.startsWith('turn:')) {
      press(action === 'turn:prev' ? 'Previous page' : 'Next page');
      await waitForTurn(seen.src);
      seen = await observe();
    } else {
      const reason = action.slice(action.indexOf(':') + 1);
      const failed = action.startsWith('fail:');
      // A capture that saved pages always hands them over; why it ended early goes to capture.json's stopped.
      if (failed && !Object.keys(positions).length) return event({ type: 'refused', reason });
      return event({ type: 'done', start: state.start, stopped: failed ? reason : '', positions,
        extension_version: chrome.runtime.getManifest().version });
    }
  }
}

async function scanLibrary(job) {
  let count = -1;
  for (let t = 0, still = 0; t < LIBRARY_WAIT_MS && still < LIBRARY_SETTLE_MS; t += 500) {
    await sleep(500);
    const now = document.querySelectorAll('*').length;
    still = now === count ? still + 500 : 0;
    count = now;
  }
  let probe;
  try { probe = probePage(document); } catch (e) { probe = { error: String(e) }; }
  return toWorker({ kc: 'event', event: { job: job.id, type: 'library', domain: location.hostname,
    books: parseLibraryGuess(document), probe } });
}

(async () => {
  for (let i = 0; i < JOB_ASKS; i++) {
    const job = await toWorker({ kc: 'job' });
    if (job?.kind === 'capture') return capture(job);
    if (job?.kind === 'library') return scanLibrary(job);
    if (job?.none) return; // the worker runs no job for this tab
    await sleep(1000);
  }
})();
