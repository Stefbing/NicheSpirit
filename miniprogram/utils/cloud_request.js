/**
 * 通用请求封装 - 支持云开发和本地调试
 * - 统一超时保护（默认 15s）
 * - 自动重试（网络失败时最多重试 1 次）
 * - 401 自动无感刷新 Token（静默 wx.login + /api/auth/refresh）后重放原请求
 * - 刷新失败时引导用户重新登录
 */

// 配置：切换运行环境
const CONFIG = {
  // 'cloud' - 云托管模式, 'local' - 本地调试模式, 'prod' - 生产模式
  mode: 'prod',

  // 云托管配置
  cloudEnv: 'prod-d5g0so0137afcfdd5',
  cloudService: 'auto-home',

  // 本地调试配置（替换为你的本地后端地址）
  localBaseUrl: 'http://192.168.1.3:8000',
  prodBaseUrl: 'https://api.stefbing.xyz',

  // 请求超时（ms）- 真机局域网环境可能较慢
  timeout: 20000,
  // 最大重试次数（真机网络不稳定，增加一次重试机会）
  maxRetries: 2
};

let isCloudInitialized = false;

// 初始化云开发环境 (建议在 app.js 的 onLaunch 中也调一次)
function initCloud() {
  if (isCloudInitialized) return true;
  if (!wx.cloud) {
    console.error('请使用 2.2.3 或以上的基础库以使用云能力');
    return false;
  }

  wx.cloud.init({
    env: CONFIG.cloudEnv,
    traceUser: true,
  });

  isCloudInitialized = true;
  return true;
}

// ========================================================================
// 【Token 无感刷新】单飞控制，避免多个并发请求同时触发刷新
// ========================================================================
let _refreshingPromise = null;

/**
 * 静默刷新 Token：调用 /api/auth/refresh（带 wx.login code + 旧 token）
 * 多个并发请求只会触发一次刷新（单飞）
 * @returns {Promise<{token: string, refreshed: boolean}>}
 */
function silentRefreshToken() {
  if (_refreshingPromise) {
    console.log('[Auth] 已有 token 刷新在进行中，复用同一 Promise');
    return _refreshingPromise;
  }

  let settled = false;
  const settle = (fn, val) => {
    if (settled) return;
    settled = true;
    _refreshingPromise = null;
    fn(val);
  };

  _refreshingPromise = new Promise((resolve, reject) => {
    const oldToken = wx.getStorageSync('token') || '';

    // 安全超时：18s 兜底拒绝（避免极端情况下 Promise 永远挂起）
    const safetyTimer = setTimeout(() => {
      settle(reject, new Error('silentRefreshToken 超时（18s）'));
    }, 18000);

    // 1. 获取微信 code
    wx.login({
      success: (loginRes) => {
        if (!loginRes || !loginRes.code) {
          clearTimeout(safetyTimer);
          settle(reject, new Error('wx.login 返回 code 失败'));
          return;
        }

        // 2. 调用后端 /api/auth/refresh
        const refreshHeader = { 'Content-Type': 'application/json' };
        const innerFn = CONFIG.mode === 'cloud' ? cloudRequestRaw : httpRequestRaw;
        innerFn('/api/auth/refresh', 'POST', {
          code: loginRes.code,
          old_token: oldToken,
        }, refreshHeader, 15000)
          .then((res) => {
            clearTimeout(safetyTimer);
            if (res && res.token) {
              wx.setStorageSync('token', res.token);
              console.log('[Auth] ✅ Token 静默刷新成功, refreshed=', !!res.refreshed);
              settle(resolve, { token: res.token, refreshed: !!res.refreshed });
            } else {
              settle(reject, new Error('refresh 响应缺少 token'));
            }
          })
          .catch((err) => {
            clearTimeout(safetyTimer);
            console.error('[Auth] ❌ Token 静默刷新失败:', err);
            settle(reject, err);
          });
      },
      fail: (err) => {
        clearTimeout(safetyTimer);
        console.error('[Auth] ❌ wx.login 失败:', err);
        settle(reject, err);
      },
      complete: () => {
        // complete 不区分 success/fail，安全超时已能覆盖卡死场景
      },
    });
  });

  return _refreshingPromise;
}

/**
 * 强制重置刷新 Promise（在已知失败的场景下提前清理）
 */
function resetRefreshState() {
  _refreshingPromise = null;
}

/**
 * 统一请求封装 - 自动根据模式选择请求方式，支持重试
 */
function callContainer(options) {
  const { path, method = 'GET', data = {}, header = {}, success, fail, timeout, retries } = options;

  const effectiveTimeout = timeout || CONFIG.timeout;
  const maxRetries = retries !== undefined ? retries : CONFIG.maxRetries;

  // 内部执行函数（支持重试 + 401 刷新后重放）
  function doRequest(attempt, retriedAfterRefresh = false) {
    return new Promise((resolve, reject) => {
      // 【修复】每次 doRequest 时重新读取 token，避免闭包捕获旧值
      const token = wx.getStorageSync('token');
      const authHeader = token ? { 'Authorization': `Bearer ${token}` } : {};
      const mergedHeader = { ...authHeader, ...header };

      const requestFn = CONFIG.mode === 'cloud' ? cloudRequest : httpRequest;
      requestFn(path, method, data, mergedHeader, effectiveTimeout)
        .then((result) => {
          if (success) success(result);
          resolve(result);
        })
        .catch((err) => {
          const sc = err && err.statusCode;
          // 【Token 无感刷新】401 + 未刷新过 + 有旧 token → 尝试刷新后重放
          if (sc === 401 && !retriedAfterRefresh && wx.getStorageSync('token')) {
            const detail = err && err.data && err.data.detail;
            // 仅对"Token失效类"错误做无感刷新；其他401（如 UNBOUND）走原始失败流程
            const isTokenError = typeof detail === 'object' && detail
              ? ['TOKEN_EXPIRED', 'TOKEN_INVALID'].includes(detail.code)
              : true; // 纯字符串 detail 也按 token 错误处理（向后兼容）

            if (isTokenError) {
              console.warn('[Auth] 🔄 检测到 Token 401，触发静默刷新');
              silentRefreshToken()
                .then(() => {
                  // 刷新成功 → 用新 token 重放原请求
                  const newToken = wx.getStorageSync('token');
                  const replayHeader = { ...header, 'Authorization': `Bearer ${newToken}` };
                  const replayFn = CONFIG.mode === 'cloud' ? cloudRequest : httpRequest;
                  replayFn(path, method, data, replayHeader, effectiveTimeout)
                    .then((replayRes) => {
                      console.log('[Auth] ✅ Token 刷新后请求重放成功');
                      if (success) success(replayRes);
                      resolve(replayRes);
                    })
                    .catch((replayErr) => {
                      console.error('[Auth] ❌ 重放请求仍失败:', replayErr);
                      if (fail) fail(replayErr);
                      reject(replayErr);
                    });
                })
                .catch((refreshErr) => {
                  // 刷新失败 → 清空 token + 引导用户重新登录
                  console.error('[Auth] ❌ 静默刷新失败，需要重新登录');
                  try { wx.removeStorageSync('token'); } catch (e) {}
                  // 【修复】刷新失败时主动重定向到登录页，避免用户卡在中间态
                  const pages = getCurrentPages && getCurrentPages();
                  const currentRoute = pages && pages.length > 0
                    ? pages[pages.length - 1].route : '';
                  if (currentRoute && currentRoute !== 'pages/login/login') {
                    console.warn('[Auth] 🚪 刷新失败，重定向到登录页');
                    wx.reLaunch({ url: '/pages/login/login' });
                  }
                  if (fail) fail(err);
                  reject(err);
                });
              return;
            }
          }

          // 非 401 或 401 已重试过 → 网络错误重试
          if (sc !== 401 && attempt < maxRetries) {
            console.warn(`[Request] 请求失败，重试 ${attempt + 1}/${maxRetries}:`, err);
            setTimeout(() => {
              doRequest(attempt + 1, retriedAfterRefresh).then(resolve).catch(reject);
            }, 500 * (attempt + 1)); // 退避延迟
          } else {
            // 【新增】401 且非"已重试过"且无旧 token 时，跳转登录页
            if (sc === 401 && !retriedAfterRefresh && !wx.getStorageSync('token')) {
              const pages = getCurrentPages && getCurrentPages();
              const currentRoute = pages && pages.length > 0
                ? pages[pages.length - 1].route : '';
              if (currentRoute && currentRoute !== 'pages/login/login') {
                console.warn('[Auth] 🚪 无 token 且 401，重定向到登录页');
                wx.reLaunch({ url: '/pages/login/login' });
              }
            }
            // 【修复】401 但 isTokenError=false（如 UNBOUND/DEVICE_BOUND），
            // 说明后端返回了明确的非 token 类 401 拒绝，不应继续使用当前 token
            if (sc === 401 && !retriedAfterRefresh && wx.getStorageSync('token')) {
              const detail = err && err.data && err.data.detail;
              const isStructuredNonTokenError = typeof detail === 'object' && detail
                && !['TOKEN_EXPIRED', 'TOKEN_INVALID'].includes(detail.code);
              if (isStructuredNonTokenError) {
                console.warn('[Auth] 🚪 后端返回非token类401，清除token并重定向');
                try { wx.removeStorageSync('token'); } catch (e) {}
                const pages = getCurrentPages && getCurrentPages();
                const currentRoute = pages && pages.length > 0
                  ? pages[pages.length - 1].route : '';
                if (currentRoute && currentRoute !== 'pages/login/login') {
                  wx.reLaunch({ url: '/pages/login/login' });
                }
              }
            }
            if (fail) fail(err);
            reject(err);
          }
        });
    });
  }

  return doRequest(0, false);
}

// ========================================================================
// 内部 Raw 请求函数（不走 callContainer 防止递归调用）
// ========================================================================

/**
 * HTTP Raw 请求（不注入 Authorization，不触发自动刷新）
 * 仅供 silentRefreshToken 内部使用，避免循环调用
 */
function httpRequestRaw(path, method, data, header, timeout) {
  const baseUrl = CONFIG.mode === 'local' ? CONFIG.localBaseUrl : CONFIG.prodBaseUrl;
  const url = `${baseUrl}${path}`;

  return new Promise((resolve, reject) => {
    wx.request({
      url: url,
      method: method,
      timeout: timeout,
      header: {
        'Content-Type': 'application/json',
        ...header
      },
      data: data,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(res);
        }
      },
      fail: (err) => {
        reject(err);
      }
    });
  });
}

/**
 * URL 路径安全编码：将路径段中除分隔符外的非 ASCII 字符做 percent-encoding
 * 避免中文/特殊字符 device_key（如 "xiaomi_智能喂食机"）触发 wx.request 失败
 */
function _encodePath(path) {
  // 仅对路径段做编码，保留 ? 之后的查询串不处理（由调用方自管）
  const [pathPart, queryPart] = path.split('?');
  const encoded = pathPart
    .split('/')
    .map(seg => {
      try {
        // encodeURIComponent 编码除 -_.~!*'() 之外的字符
        return encodeURIComponent(decodeURIComponent(seg))
          .replace(/[!'()*]/g, c => '%' + c.charCodeAt(0).toString(16).toUpperCase());
      } catch (e) {
        return seg;
      }
    })
    .join('/');
  return queryPart ? `${encoded}?${queryPart}` : encoded;
}

/**
 * 云托管 Raw 请求（不注入 Authorization，不触发自动刷新）
 * 仅供 silentRefreshToken 内部使用
 */
function cloudRequestRaw(path, method, data, header, timeout) {
  if (!initCloud()) {
    return Promise.reject(new Error('云开发初始化失败'));
  }

  return new Promise((resolve, reject) => {
    wx.cloud.callContainer({
      config: {
        env: CONFIG.cloudEnv
      },
      path: path,
      method: method,
      timeout: timeout,
      header: {
        'X-WX-SERVICE': CONFIG.cloudService,
        'Content-Type': 'application/json',
        ...header
      },
      data: data,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(res);
        }
      },
      fail: (err) => {
        reject(err);
      }
    });
  });
}

/**
 * HTTP 请求 - 支持 local 和 prod 两种模式
 * 根据当前 mode 自动选择对应的 baseUrl
 */
function httpRequest(path, method, data, header, timeout) {
  const baseUrl = CONFIG.mode === 'local' ? CONFIG.localBaseUrl : CONFIG.prodBaseUrl;
  const url = `${baseUrl}${_encodePath(path)}`;
  const modeLabel = CONFIG.mode === 'local' ? '本地' : '生产';
  console.log(`[Request] [${modeLabel}] → ${method} ${url}`);

  return new Promise((resolve, reject) => {
    const startTime = Date.now();
    wx.request({
      url: url,
      method: method,
      timeout: timeout,
      header: {
        'Content-Type': 'application/json',
        ...header
      },
      data: data,
      success: (res) => {
        const elapsed = Date.now() - startTime;
        console.log(`[Request] ← ${res.statusCode} ${method} ${path} (${elapsed}ms)`);
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(res);
        }
      },
      fail: (err) => {
        const elapsed = Date.now() - startTime;
        const errMsg = (err && err.errMsg) || '';
        // 识别超时 vs 连接拒绝，帮助定位真机网络问题
        if (errMsg.indexOf('timeout') !== -1 || errMsg.indexOf('ETIMEDOUT') !== -1) {
          console.error(`[Request] ✗ 请求超时 [${url}] (${elapsed}ms/${timeout}ms): 请检查：` +
            '1) 手机与电脑是否在同一局域网；2) Windows防火墙是否放行8000端口；3) 后端服务是否正常运行');
        } else if (errMsg.indexOf('refuse') !== -1 || errMsg.indexOf('ECONNREFUSED') !== -1) {
          console.error(`[Request] ✗ 连接被拒 [${url}] (${elapsed}ms): 后端服务未启动或端口错误`);
        } else if (errMsg.indexOf('fail') !== -1 && elapsed >= timeout) {
          console.error(`[Request] ✗ 请求超时（wx.request fail）[${url}] (${elapsed}ms/${timeout}ms)`);
        } else {
          console.error(`[Request] ✗ HTTP 请求失败 [${url}] (${elapsed}ms):`, err);
        }
        reject(err);
      }
    });
  });
}

/**
 * 云托管请求
 */
function cloudRequest(path, method, data, header, timeout) {
  if (!initCloud()) {
    return Promise.reject(new Error('云开发初始化失败'));
  }

  return new Promise((resolve, reject) => {
    wx.cloud.callContainer({
      config: {
        env: CONFIG.cloudEnv
      },
      path: path,
      method: method,
      timeout: timeout,
      header: {
        'X-WX-SERVICE': CONFIG.cloudService,
        'Content-Type': 'application/json',
        ...header
      },
      data: data,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(res);
        }
      },
      fail: (err) => {
        console.error('[Request] 云托管请求异常:', err);
        reject(err);
      }
    });
  });
}

module.exports = {
  callContainer,
  initCloud,
  getConfig: () => CONFIG,
  // 【新增】对外暴露的 token 刷新能力
  silentRefreshToken,
  resetRefreshState,
};
