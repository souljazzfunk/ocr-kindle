importScripts('autostart.js');

// active lives in session storage, so a browser restart (which ends any capture) clears it.
async function loadState() {
  const { active = null } = await chrome.storage.session.get('active');
  const { saved = [] } = await chrome.storage.local.get('saved');
  return { active, saved };
}

async function storeState({ active, saved }) {
  await chrome.storage.session.set({ active });
  await chrome.storage.local.set({ saved });
}

// Claims from two tabs can arrive together; handle them one at a time.
let queue = Promise.resolve();
function update(change) {
  const next = queue.then(async () => {
    const result = change(await loadState());
    await storeState(result.state ?? result);
    return result;
  });
  queue = next.catch(() => {});
  return next;
}

chrome.action.onClicked.addListener((tab) => chrome.tabs.sendMessage(tab.id, 'toggle'));

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = sender.tab.id;
  if (msg.badge) chrome.action.setBadgeText({ tabId, text: msg.badge });
  if (msg.claim) {
    update((state) => claimCapture(state, { tabId, ...msg.claim })).then(({ ok, reason }) => reply({ ok, reason }));
    return true;
  }
  if (msg.finish) update((state) => finishCapture(state, { tabId, ...msg.finish }));
});

// A closed or reloaded tab has lost its capture, so it no longer holds the lock.
const release = (tabId) => update((state) => finishCapture(state, { tabId, asin: '', pages: 0 }));
chrome.tabs.onRemoved.addListener(release);
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === 'loading') release(tabId); });
