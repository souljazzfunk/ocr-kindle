const test = require('node:test');
const assert = require('node:assert');
const manifest = require('./manifest.json');
const { downloadOptions } = require('./download.js');

test('content scripts load the shared rules before content.js', () => {
  const js = manifest.content_scripts[0].js;
  assert.deepStrictEqual(js, ['autostart.js', 'pagemeta.js', 'content.js']);
});

test('the background worker can keep the one-tab lock', () => {
  assert.ok(manifest.permissions.includes('storage'));
});

test('the worker saves a finished ZIP with no dialog, under its own name, never over an earlier one', () => {
  assert.ok(manifest.permissions.includes('downloads'));
  assert.deepStrictEqual(downloadOptions('blob:https://read.amazon.com/1', 'Drive.zip'),
    { url: 'blob:https://read.amazon.com/1', filename: 'Drive.zip', saveAs: false, conflictAction: 'uniquify' });
});
