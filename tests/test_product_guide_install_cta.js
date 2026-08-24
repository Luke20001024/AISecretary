const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'docs', 'index.html'), 'utf8');

const closing = html.match(/<section class="closing shell"[\s\S]*?<\/section>/);
assert.ok(closing, 'product guide must contain the closing installation section');

assert.match(
  closing[0],
  /https:\/\/github\.com\/Luke20001024\/Memento\/releases\/tag\/v0\.10\.1/,
  'closing section must link to the current GitHub preview release'
);
assert.match(
  closing[0],
  /https:\/\/github\.com\/Luke20001024\/Memento\/blob\/main\/docs\/MEMENTO_DEMO_INSTALL\.md/,
  'closing section must link to the GitHub installation guide'
);
assert.match(closing[0], />\u53bb GitHub \u4e0b\u8f7d<\//, 'download action must be explicit');
assert.match(closing[0], />\u67e5\u770b\u5b89\u88c5\u6b65\u9aa4<\//, 'installation action must be explicit');
assert.match(closing[0], /href="demo\/dashboard\.html"/, 'closing section must preserve online trial access');

const externalLinks = [...closing[0].matchAll(/<a[^>]+href="https:\/\/github\.com\/[^>]+>/g)];
assert.equal(externalLinks.length, 2, 'closing section must expose exactly two GitHub actions');
for (const [link] of externalLinks) {
  assert.match(link, /target="_blank"/, 'GitHub actions must open outside the guide');
  assert.match(link, /rel="noreferrer"/, 'GitHub actions must isolate the opener context');
}

console.log('Product guide installation CTA contract passed.');
