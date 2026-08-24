(function initMementoDirectoryAccess(global) {
  const DEFAULT_TIMEOUT_MS = 8000;

  function timeoutError(stage, timeoutMs) {
    const error = new Error(`${stage}超时(${Math.round(timeoutMs / 1000)}秒)`);
    error.name = 'TimeoutError';
    error.stage = stage;
    return error;
  }

  function withTimeout(task, timeoutMs = DEFAULT_TIMEOUT_MS, stage = '操作') {
    if (!timeoutMs || timeoutMs < 0) return Promise.resolve().then(task);

    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(timeoutError(stage, timeoutMs));
      }, timeoutMs);

      Promise.resolve()
        .then(task)
        .then(value => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          resolve(value);
        }, error => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          reject(error);
        });
    });
  }

  function reportStage(options, stage) {
    try {
      if (options.onStage) options.onStage(stage);
    } catch (error) {
      console.warn('无法更新目录恢复阶段', error);
    }
  }

  function runStage(options, stage, label, task) {
    reportStage(options, stage);
    // Permission checks and directory loads are uncancellable File System
    // Access calls. Only the IndexedDB handle lookup uses the watchdog.
    const isFileSystemAccess = stage === 'query-permission' || stage === 'load-directory';
    const timeoutMs = isFileSystemAccess ? -1 : options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    return withTimeout(task, timeoutMs, label);
  }

  function isPermissionError(error) {
    return Boolean(error && (error.name === 'NotAllowedError' || error.name === 'SecurityError'));
  }

  function isStaleHandleError(error) {
    return Boolean(error && (error.name === 'NotFoundError' || error.name === 'InvalidStateError'));
  }

  function createGenerationGate() {
    let current = 0;
    return {
      begin() {
        current += 1;
        return current;
      },
      invalidate(generation) {
        if (generation === current) current += 1;
      },
      isCurrent(generation) {
        return generation === current;
      },
      commit(generation, task) {
        if (generation !== current) return false;
        task();
        return true;
      },
    };
  }

  async function restore(options) {
    let handle;
    try {
      handle = await runStage(options, 'load-handle', '读取浏览器授权记录', options.loadHandle);
    } catch (error) {
      return { kind: 'storage-error', error };
    }

    if (!handle) return { kind: 'missing' };

    let permission;
    try {
      permission = await runStage(
        options,
        'query-permission',
        '检查数据目录权限',
        () => options.queryPermission(handle)
      );
    } catch (error) {
      return { kind: 'permission-check-error', handle, error };
    }

    if (permission !== 'granted') {
      return { kind: 'permission-required', handle, permission };
    }

    try {
      await runStage(options, 'load-directory', '读取 Memento 数据文件', () => options.loadDirectory(handle));
      return { kind: 'ready', handle, permission };
    } catch (error) {
      if (isPermissionError(error)) {
        return { kind: 'permission-required', handle, permission: 'prompt', error };
      }
      if (isStaleHandleError(error)) {
        return { kind: 'directory-missing', handle, permission, error };
      }
      return { kind: 'read-error', handle, permission, error };
    }
  }

  global.MementoDirectoryAccess = {
    createGenerationGate,
    isPermissionError,
    isStaleHandleError,
    restore,
    withTimeout,
  };
})(typeof window !== 'undefined' ? window : globalThis);
