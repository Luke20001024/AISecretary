#!/usr/bin/env node

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sourceFixture = readFileSync('chrome-newtab/cognitive-demo-fixture.js', 'utf8');
const publishedFixture = readFileSync('docs/demo/cognitive-demo-fixture.js', 'utf8');
assert.equal(publishedFixture, sourceFixture, '线上 Demo fixture 必须与本地完整 Demo 同源');

const context = { window: {}, TextEncoder, TextDecoder };
vm.runInNewContext(publishedFixture, context, { filename: 'cognitive-demo-fixture.js' });

const library = context.window.MementoCognitiveDemoFixture;
assert.equal(typeof library?.createFixture, 'function');
const fixture = library.createFixture();
assert.equal(fixture.mode, 'synthetic');
assert.equal(fixture.records.length, 261);
assert.equal(fixture.themes.length, 6);
assert.equal(fixture.portrait.length, 4);
assert.equal(fixture.window.start, '2026-07-30');
assert.equal(fixture.window.end, '2026-08-18');
assert.equal(fixture.stats.todayRecords, 15);

console.log('Memento shared Preview fixture contract passed.');
