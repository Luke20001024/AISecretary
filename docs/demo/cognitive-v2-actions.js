// Frontend action boundary for Backend V2. Shadow modes are always read-only.

(function exposeCognitiveV2Actions(root, factory) {
  const contract = typeof module !== 'undefined' && module.exports
    ? require('./cognitive-v2-contract.js')
    : root.MementoCognitiveV2Contract;
  const api = factory(contract);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MementoCognitiveV2Actions = api;
})(typeof window !== 'undefined' ? window : globalThis, function createActions(contract) {
  'use strict';

  const TERMINAL_ACTION = new Set(['applied', 'rejected', 'conflict']);
  const TERMINAL_RUN = new Set(['completed', 'completed_with_warnings', 'no_change', 'rejected', 'conflict', 'failed']);

  class CognitiveV2ActionError extends Error {
    constructor(message, kind = 'invalid') {
      super(message);
      this.name = 'CognitiveV2ActionError';
      this.kind = kind;
    }
  }

  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function assertWritable(mode) {
    contract.validateFeatureMode(mode);
    if (mode !== 'v2_live') throw new CognitiveV2ActionError('当前数据源处于只读模式', 'read_only');
  }
  function positiveInteger(value, fallback) {
    return Number.isSafeInteger(value) && value > 0 ? value : fallback;
  }

  function createActionClient(options) {
    const mode = contract.validateFeatureMode(options.mode);
    const transport = options.transport || {};
    const pollIntervalMs = positiveInteger(options.pollIntervalMs, 400);
    const maximumPolls = positiveInteger(options.maximumPolls, 50);

    async function submitAction(action) {
      assertWritable(mode);
      if (typeof transport.submitAction !== 'function') throw new CognitiveV2ActionError('Action transport 未配置', 'unavailable');
      return clone(await transport.submitAction(clone(action)));
    }

    async function pollActionResult(actionId) {
      if (typeof transport.pollActionResult !== 'function') throw new CognitiveV2ActionError('Action result transport 未配置', 'unavailable');
      const value = await transport.pollActionResult(actionId);
      return value === null ? null : clone(value);
    }

    async function requestRun(kind, scope) {
      assertWritable(mode);
      if (typeof transport.requestRun !== 'function') throw new CognitiveV2ActionError('Run transport 未配置', 'unavailable');
      return clone(await transport.requestRun(kind, clone(scope)));
    }

    async function readRunStatus(runId) {
      if (typeof transport.readRunStatus !== 'function') throw new CognitiveV2ActionError('Run status transport 未配置', 'unavailable');
      return clone(await transport.readRunStatus(runId));
    }

    async function waitForActionResult(actionId) {
      for (let attempt = 0; attempt < maximumPolls; attempt += 1) {
        const result = await pollActionResult(actionId);
        if (result !== null) {
          if (!TERMINAL_ACTION.has(result.status)) throw new CognitiveV2ActionError('Action result 不是终态', 'evidence');
          return result;
        }
        await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
      }
      throw new CognitiveV2ActionError('等待 Action 终态超时', 'timeout');
    }

    async function waitForRunResult(runId) {
      for (let attempt = 0; attempt < maximumPolls; attempt += 1) {
        const status = await readRunStatus(runId);
        if (TERMINAL_RUN.has(status.status)) return status;
        await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
      }
      throw new CognitiveV2ActionError('等待 Run 终态超时', 'timeout');
    }

    return Object.freeze({ mode, submitAction, pollActionResult, requestRun, readRunStatus, waitForActionResult, waitForRunResult });
  }

  function createHttpTransport(options) {
    const baseUrl = new URL(options.baseUrl || 'http://127.0.0.1:4318');
    if (baseUrl.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(baseUrl.hostname)) {
      throw new CognitiveV2ActionError('Runtime transport 只允许本机回环地址', 'authorization');
    }
    const token = String(options.token || '');
    if (token.length < 32) throw new CognitiveV2ActionError('Runtime token 无效', 'authorization');
    const fetchImpl = options.fetchImpl || globalThis.fetch;
    if (typeof fetchImpl !== 'function') throw new CognitiveV2ActionError('fetch transport 不可用', 'unavailable');
    const request = async (path, init = {}) => {
      const response = await fetchImpl(new URL(path, baseUrl), {
        ...init,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new CognitiveV2ActionError(
          payload?.error?.message || `Runtime 请求失败 ${response.status}`,
          payload?.error?.kind || 'transport'
        );
      }
      return payload.value;
    };
    const query = value => encodeURIComponent(String(value));
    return Object.freeze({
      bootstrap: () => request('/v2/bootstrap'),
      readRuntimeSettings: () => request('/v2/runtime-settings'),
      updateRuntimeSettings: settings => request('/v2/runtime-settings', {
        method: 'POST', body: JSON.stringify(settings),
      }),
      submitAction: action => request('/v2/action', { method: 'POST', body: JSON.stringify(action) }),
      pollActionResult: actionId => request(`/v2/action-result?id=${query(actionId)}`),
      requestRun: (runKind, scope) => request('/v2/run-request', {
        method: 'POST',
        body: JSON.stringify({ run_kind: runKind, scope, requested_at: new Date().toISOString() }),
      }),
      readRunStatus: runId => request(`/v2/run-status?id=${query(runId)}`),
      listRunStatuses: (limit = 20) => request(`/v2/run-statuses?limit=${query(limit)}`),
      readExternalSession: sessionId => request(`/v2/external-session?id=${query(sessionId)}`),
      health: async () => {
        const response = await fetchImpl(new URL('/health', baseUrl), {
          headers: { Authorization: `Bearer ${token}` },
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new CognitiveV2ActionError('Runtime 健康检查失败', 'transport');
        return clone(payload);
      },
    });
  }

  return Object.freeze({ CognitiveV2ActionError, createActionClient, createHttpTransport });
});
