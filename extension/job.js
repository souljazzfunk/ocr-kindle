// What the worker does with an answer from the channel (GET /job), shared by background.js and job.test.js.
// active: the job this worker runs ({ job, tabId, windowId, handed, started }) or null. reply: the channel's answer, or null
// when it could not be reached. Returns 'reload', 'open', 'end' or 'none'.
// The worker is loaded unpacked from the checkout, so chrome.runtime.reload() picks up a version Python pulled.
// A reload is not repeated within RELOAD_GAP_MS, so a channel run from another checkout cannot loop it.
const RELOAD_GAP_MS = 60000;

function pollStep(active, reply, ownVersion, now, lastReload) {
  if (!reply) return 'none';
  if (active) return reply.job && reply.job.id === active.job.id ? 'none' : 'end';
  if (reply.version !== ownVersion) return now - (lastReload || 0) >= RELOAD_GAP_MS ? 'reload' : 'none';
  return reply.job ? 'open' : 'none';
}

// The job for a content script that asks. A tab that reloads before it sent any page (read.amazon.co.jp redirects a
// purchased book to its sample, 2026-10-12) gets the job again; once pages went to the channel a reload gets none, so
// the same book is never paged twice in one job.
function handOut(active, tabId) {
  if (!active || active.tabId !== tabId) return null;
  return active.handed && active.started ? null : active.job;
}

// Cloud Reader moved the book's tab to its sample: the purchased book is not on this domain.
const isSample = (url) => /\/sample\//.test(new URL(url).pathname);

function readerUrl(job) {
  return job.kind === 'library' ? `https://${job.domain}/kindle-library` : `https://${job.domain}/?asin=${job.asin}`;
}

if (typeof module !== 'undefined') module.exports = { pollStep, handOut, isSample, readerUrl, RELOAD_GAP_MS };
