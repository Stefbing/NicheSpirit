/**
 * 通用请求封装 - 支持云开发和本地调试
 * - 统一超时保护（默认 15s）
 * - 自动重试（网络失败时最多重试 1 次）
 * - 401 自动跳转登录
 */

// 配置：切换运行环境
const CONFIG = {
  // 'cloud' - 云托管模式, 'local' - 本地调试模式, 'prod' - 生产模式
  mode: 'local',

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

/**
 * 统一请求封装 - 自动根据模式选择请求方式，支持重试
 */
function callContainer(options) {
  const { path, method = 'GET', data = {}, header = {}, success, fail, timeout, retries } = options;

  // 自动从本地缓存获取 Token
  const token = wx.getStorageSync('token');
  const authHeader = token ? { 'Authorization': `Bearer ${token}` } : {};
  const mergedHeader = { ...authHeader, ...header };
  const effectiveTimeout = timeout || CONFIG.timeout;
  const maxRetries = retries !== undefined ? retries : CONFIG.maxRetries;

  // 内部执行函数（支持重试）
  function doRequest(attempt) {
    return new Promise((resolve, reject) => {
      const requestFn = CONFIG.mode === 'cloud' ? cloudRequest : httpRequest;
      requestFn(path, method, data, mergedHeader, effectiveTimeout)
        .then((result) => {
          if (success) success(result);
          resolve(result);
        })
        .catch((err) => {
          // 401 不再重试，直接失败
          if (err && err.statusCode === 401) {
            console.error('[Request] 401 登录失效');
            if (fail) fail(err);
            reject(err);
            return;
          }
          // 网络错误且还有重试次数
          if (attempt < maxRetries) {
            console.warn(`[Request] 请求失败，重试 ${attempt + 1}/${maxRetries}:`, err);
            setTimeout(() => {
              doRequest(attempt + 1).then(resolve).catch(reject);
            }, 500 * (attempt + 1)); // 退避延迟
          } else {
            if (fail) fail(err);
            reject(err);
          }
        });
    });
  }

  return doRequest(0);
}

/**
 * HTTP 请求 - 支持 local 和 prod 两种模式
 * 根据当前 mode 自动选择对应的 baseUrl
 */
function httpRequest(path, method, data, header, timeout) {
  const baseUrl = CONFIG.mode === 'local' ? CONFIG.localBaseUrl : CONFIG.prodBaseUrl;
  const url = `${baseUrl}${path}`;
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
  getConfig: () => CONFIG
};
