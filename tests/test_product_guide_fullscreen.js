const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'index.html'), 'utf8');

const fullscreenLayout = html.match(
  /\.live-demo:fullscreen,[\s\S]*?\.live-demo\.is-live-demo-fullscreen \{([\s\S]*?)\n\s*\}/
);
assert.ok(fullscreenLayout, 'product guide must define the fullscreen demo layout');
assert.match(
  fullscreenLayout[1],
  /grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)/,
  'fullscreen demo must reserve rows for its toolbar and flexible iframe'
);

const fullscreenFrame = html.match(
  /\.live-demo:fullscreen iframe,[\s\S]*?\.live-demo\.is-live-demo-fullscreen iframe \{([\s\S]*?)\n\s*\}/
);
assert.ok(fullscreenFrame, 'product guide must define fullscreen iframe sizing');
assert.match(fullscreenFrame[1], /grid-row:\s*2/, 'fullscreen iframe must occupy the flexible second row');
assert.match(fullscreenFrame[1], /height:\s*100%\s*!important/, 'fullscreen iframe must fill its row');
assert.match(fullscreenFrame[1], /min-height:\s*0/, 'fullscreen iframe must be allowed to shrink to the viewport');

console.log('Product guide fullscreen layout contract passed.');
