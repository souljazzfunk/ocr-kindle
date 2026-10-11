// What the worker does with an answer from the channel (GET /job), shared by background.js and job.test.js.
// active: the job this worker runs ({ job, tabId, windowId, handed }) or null. reply: the channel's answer, or null
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

function readerUrl(job) {
  return job.kind === 'library' ? `https://${job.domain}/kindle-library` : `https://${job.domain}/?asin=${job.asin}`;
}

if (typeof module !== 'undefined') module.exports = { pollStep, readerUrl, RELOAD_GAP_MS };
