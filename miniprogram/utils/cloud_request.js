/**
 * 通用请求封装 - 支持云开发和本地调试
 * - 统一超时保护（默认 15s）
 * - 自动重试（网络失败时最多重试 1 次）
 * - 401 自动跳转登录
 */

// 配置：切换运行环境
const CONFIG = {
  // 'cloud' - 云托管模式, 'local' - 本地调试模式
  mode: 'local',

  // 云托管配置
  cloudEnv: 'prod-d5g0so0137afcfdd5',
  cloudService: 'auto-home',

  // 本地调试配置（替换为你的本地后端地址）
  localBaseUrl: 'http://192.168.1.3:8000',

  // 请求超时（ms）
  timeout: 15000,
  // 最大重试次数
  maxRetries: 1
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
      const requestFn = CONFIG.mode === 'local' ? localRequest : cloudRequest;
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
 * 本地调试请求
 */
function localRequest(path, method, data, header, timeout) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${CONFIG.localBaseUrl}${path}`,
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
        console.error('[Request] 本地请求异常:', err);
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
