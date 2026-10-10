// How the background worker saves a finished ZIP, shared by background.js and download.test.js.
// saveAs: false saves straight into the default Downloads folder with no dialog, even when the browser
// is set to ask where to save each file. uniquify adds " (1)" instead of replacing an earlier ZIP.
function downloadOptions(url, filename) {
  return { url, filename, saveAs: false, conflictAction: 'uniquify' };
}

if (typeof module !== 'undefined') module.exports = { downloadOptions };
