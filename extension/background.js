chrome.action.onClicked.addListener((tab) => chrome.tabs.sendMessage(tab.id, 'toggle'));

chrome.runtime.onMessage.addListener((msg, sender) => {
  chrome.action.setBadgeText({ tabId: sender.tab.id, text: msg.badge });
});
