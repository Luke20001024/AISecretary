// Installed Backend V2 replaces this owner-only file with a local bearer token.
// The published demo deliberately keeps the token empty and remains on fixture data.
(function exposeMementoRuntimeConfig(root) {
  'use strict';

  root.MementoRuntimeConfig = Object.freeze({
    publicPreview: true,
    mode: 'fixture',
    baseUrl: '',
    token: '',
  });
})(typeof window !== 'undefined' ? window : globalThis);
