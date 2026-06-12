/**
 * 统一API服务层 - 所有后端API调用的集中管理
 * 
 * 【优势】
 * 1. 集中管理所有API端点（避免散落在各个页面）
 * 2. 统一错误处理和加载状态
 * 3. 提供类型提示（通过JSDoc）
 * 4. 便于API版本管理和迁移
 * 
 * 【使用方式】
 * import api from '../../services/api.js';
 * 
 * // 获取Dashboard数据
 * const data = await api.dashboard.get(forceRefresh);
 * 
 * // 接受分享
 * const result = await api.share.accept(token);
 */

const cloudRequest = require('../utils/cloud_request.js');

// ============================================================================
// 基础配置
// ============================================================================

const API_VERSION = 'v1';
const DEFAULT_TIMEOUT = 20000;

// ============================================================================
// 通用请求封装（增强版）
// ============================================================================

/**
 * 通用GET请求
 * @param {string} path - API路径
 * @param {Object} params - 查询参数
 * @param {Object} options - 额外选项（timeout, retries等）
 * @returns {Promise<any>}
 */
function get(path, params = {}, options = {}) {
  const queryString = Object.keys(params).length > 0
    ? '?' + Object.entries(params).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
    : '';
  
  return cloudRequest.callContainer({
    path: `${path}${queryString}`,
    method: 'GET',
    data: {},
    timeout: options.timeout || DEFAULT_TIMEOUT,
    retries: options.retries,
  });
}

/**
 * 通用POST请求
 * @param {string} path - API路径
 * @param {Object} data - 请求体
 * @param {Object} options - 额外选项
 * @returns {Promise<any>}
 */
function post(path, data = {}, options = {}) {
  return cloudRequest.callContainer({
    path: path,
    method: 'POST',
    data: data,
    timeout: options.timeout || DEFAULT_TIMEOUT,
    retries: options.retries,
  });
}

/**
 * 通用PUT请求
 * @param {string} path - API路径
 * @param {Object} data - 请求体
 * @param {Object} options - 额外选项
 * @returns {Promise<any>}
 */
function put(path, data = {}, options = {}) {
  return cloudRequest.callContainer({
    path: path,
    method: 'PUT',
    data: data,
    timeout: options.timeout || DEFAULT_TIMEOUT,
    retries: options.retries,
  });
}

/**
 * 通用DELETE请求
 * @param {string} path - API路径
 * @param {Object} options - 额外选项
 * @returns {Promise<any>}
 */
function del(path, options = {}) {
  return cloudRequest.callContainer({
    path: path,
    method: 'DELETE',
    data: {},
    timeout: options.timeout || DEFAULT_TIMEOUT,
    retries: options.retries,
  });
}

// ============================================================================
// Dashboard API
// ============================================================================

const dashboardAPI = {
  /**
   * 获取Dashboard数据
   * @param {boolean} forceRefresh - 是否强制刷新缓存
   * @returns {Promise<Object>} Dashboard数据
   */
  async get(forceRefresh = false) {
    console.log('[API] Dashboard.get', { forceRefresh });
    const startTime = Date.now();
    
    try {
      const data = await get('/api/dashboard/data', { force_refresh: forceRefresh });
      const elapsed = Date.now() - startTime;
      console.log(`[API] Dashboard.get ✓ ${elapsed}ms`);
      return data;
    } catch (err) {
      const elapsed = Date.now() - startTime;
      console.error(`[API] Dashboard.get ✗ ${elapsed}ms`, err);
      throw err;
    }
  },

  /**
   * 获取缓存统计（管理员）
   * @returns {Promise<Object>} 缓存命中率统计
   */
  async getCacheStats() {
    console.log('[API] Dashboard.getCacheStats');
    return await get('/api/admin/cache/stats');
  },

  /**
   * 失效缓存（管理员）
   * @param {number|null} userId - 用户ID，null表示所有用户
   * @param {string[]|null} components - 组件列表，null表示所有
   * @returns {Promise<Object>}
   */
  async invalidateCache(userId = null, components = null) {
    console.log('[API] Dashboard.invalidateCache', { userId, components });
    return await post('/api/admin/cache/invalidate', {
      user_id: userId,
      components: components,
    });
  },
};

// ============================================================================
// 认证 API
// ============================================================================

const authAPI = {
  /**
   * 登录
   * @param {string} phone - 手机号
   * @param {string} password - 密码
   * @returns {Promise<Object>} {token, user_id}
   */
  async login(phone, password) {
    console.log('[API] Auth.login');
    return await post('/api/auth/login', {
      phone_number: phone,
      password: password,
    });
  },

  /**
   * 注册
   * @param {string} phone - 手机号
   * @param {string} password - 密码
   * @returns {Promise<Object>}
   */
  async register(phone, password) {
    console.log('[API] Auth.register');
    return await post('/api/auth/register', {
      phone_number: phone,
      password: password,
    });
  },

  /**
   * 静默刷新Token（内部已集成在cloud_request.js中）
   * 通常不需要手动调用
   */
  silentRefresh() {
    return cloudRequest.silentRefreshToken();
  },
};

// ============================================================================
// 分享 API
// ============================================================================

const shareAPI = {
  /**
   * 创建分享
   * @param {string[]} deviceKeys - 设备Keys列表
   * @returns {Promise<Object>} {share_id, share_token, share_link}
   */
  async create(deviceKeys) {
    console.log('[API] Share.create', { deviceKeys });
    return await post('/api/share/create', {
      device_keys: deviceKeys,
    });
  },

  /**
   * 接受分享
   * @param {string} shareToken - 分享Token
   * @returns {Promise<Object>} {success, message, configured}
   */
  async accept(shareToken) {
    console.log('[API] Share.accept', { shareToken });
    return await post('/api/share/accept', {
      share_token: shareToken,
    });
  },

  /**
   * 获取分享管理列表（我发出的分享）
   * @returns {Promise<Object>} {shares}
   */
  async getManageList() {
    console.log('[API] Share.getManageList');
    return await get('/api/share/manage-list');
  },

  /**
   * 查询分享记录
   * @param {string} role - "from"（我发出的）或 "to"（我收到的）
   * @returns {Promise<Object>} {shares}
   */
  async list(role = 'from') {
    console.log('[API] Share.list', { role });
    return await get('/api/share/list', { role });
  },

  /**
   * 撤销分享
   * @param {number} shareId - 分享ID
   * @returns {Promise<Object>} {success, message}
   */
  async revoke(shareId) {
    console.log('[API] Share.revoke', { shareId });
    return await post('/api/share/revoke', {
      share_id: shareId,
    });
  },

  /**
   * 更新分享有效期
   * @param {number} shareId - 分享ID
   * @param {number} expireHours - 有效期（小时）
   * @returns {Promise<Object>} {success, message, expires_at}
   */
  async updateExpiry(shareId, expireHours) {
    console.log('[API] Share.updateExpiry', { shareId, expireHours });
    return await post('/api/share/update-expiry', {
      share_id: shareId,
      expire_hours: expireHours,
    });
  },

  /**
   * 检查过期分享（后台任务）
   * @param {number} userId - 用户ID，0表示所有用户
   * @returns {Promise<Object>} {success, revoked_count}
   */
  async checkExpired(userId = 0) {
    console.log('[API] Share.checkExpired', { userId });
    return await post('/api/share/check-expired', {
      user_id: userId,
    });
  },

  /**
   * 通过分享者ID查询待接受分享
   * @param {number} fromUserId - 分享者用户ID
   * @returns {Promise<Object>} {found, share}
   */
  async getPendingFromUser(fromUserId) {
    console.log('[API] Share.getPendingFromUser', { fromUserId });
    return await get('/api/share/pending-from-user', {
      from_user_id: fromUserId,
    });
  },
};

// ============================================================================
// PetKit API
// ============================================================================

const petkitAPI = {
  /**
   * 获取设备列表
   * @param {boolean} forceRefresh - 是否强制刷新
   * @returns {Promise<Array>} 设备列表
   */
  async getDevices(forceRefresh = false) {
    console.log('[API] PetKit.getDevices', { forceRefresh });
    return await get('/api/petkit/devices', { force_refresh: forceRefresh });
  },

  /**
   * 获取设备统计
   * @param {string} deviceId - 设备ID
   * @returns {Promise<Object>} 统计数据
   */
  async getStats(deviceId) {
    console.log('[API] PetKit.getStats', { deviceId });
    return await get('/api/petkit/stats', { device_id: deviceId });
  },

  /**
   * 获取历史统计
   * @param {string|null} deviceId - 设备ID（可选）
   * @param {number} days - 天数
   * @returns {Promise<Object>} 历史统计
   */
  async getHistory(deviceId = null, days = 7) {
    console.log('[API] PetKit.getHistory', { deviceId, days });
    const params = { days };
    if (deviceId) params.device_id = deviceId;
    return await get('/api/petkit/history', params);
  },

  /**
   * 控制设备（喂食、冲水等）
   * @param {string} deviceId - 设备ID
   * @param {string} action - 动作（feed/water等）
   * @param {Object} params - 额外参数
   * @returns {Promise<Object>}
   */
  async control(deviceId, action, params = {}) {
    console.log('[API] PetKit.control', { deviceId, action, params });
    return await post('/api/petkit/control', {
      device_id: deviceId,
      action: action,
      ...params,
    });
  },
};

// ============================================================================
// CloudPets API
// ============================================================================

const cloudpetsAPI = {
  /**
   * 获取今日投喂记录
   * @returns {Promise<Object>} {result: number, detail: Array}
   */
  async getServingsToday() {
    console.log('[API] CloudPets.getServingsToday');
    return await get('/api/cloudpets/servings/today');
  },

  /**
   * 获取喂食计划
   * @returns {Promise<Array>} 喂食计划列表
   */
  async getFeedingPlans() {
    console.log('[API] CloudPets.getFeedingPlans');
    return await get('/api/cloudpets/plans');
  },

  /**
   * 手动投喂
   * @param {string} deviceKey - 设备Key
   * @returns {Promise<Object>}
   */
  async feedNow(deviceKey) {
    console.log('[API] CloudPets.feedNow', { deviceKey });
    return await post('/api/cloudpets/feed', {
      device_key: deviceKey,
    });
  },
};

// ============================================================================
// 体脂秤 API
// ============================================================================

const scaleAPI = {
  /**
   * 保存体重记录
   * @param {number} memberId - 家庭成员ID
   * @param {number} weight - 体重（kg）
   * @param {number|null} impedance - 阻抗（可选）
   * @param {Object} additionalData - 额外数据（bmi, body_fat等）
   * @returns {Promise<Object>}
   */
  async saveRecord(memberId, weight, impedance = null, additionalData = {}) {
    console.log('[API] Scale.saveRecord', { memberId, weight });
    return await post('/api/scale/record', {
      member_id: memberId,
      weight: weight,
      impedance: impedance,
      ...additionalData,
    });
  },

  /**
   * 获取体重记录
   * @param {number} memberId - 家庭成员ID
   * @param {number} startTime - 开始时间戳（毫秒）
   * @param {number} endTime - 结束时间戳（毫秒）
   * @returns {Promise<Array>} 体重记录列表
   */
  async getRecords(memberId, startTime = null, endTime = null) {
    console.log('[API] Scale.getRecords', { memberId, startTime, endTime });
    const params = { member_id: memberId };
    if (startTime) params.start_time = startTime;
    if (endTime) params.end_time = endTime;
    return await get('/api/scale/records', params);
  },

  /**
   * 获取今日统计
   * @returns {Promise<Object>} {today_count, latest_body_fat}
   */
  async getTodayStats() {
    console.log('[API] Scale.getTodayStats');
    return await get('/api/scale/today-stats');
  },
};

// ============================================================================
// 系统配置 API
// ============================================================================

const configAPI = {
  /**
   * 保存平台配置
   * @param {string} platform - 平台名称（petkit/cloudpets/xiaomi）
   * @param {string} account - 账号
   * @param {string} password - 密码
   * @returns {Promise<Object>}
   */
  async savePlatformConfig(platform, account, password) {
    console.log('[API] Config.savePlatformConfig', { platform });
    return await post('/api/config/platform', {
      platform: platform,
      account: account,
      password: password,
    });
  },

  /**
   * 获取平台配置状态
   * @returns {Promise<Object>} 各平台配置状态
   */
  async getPlatformStatus() {
    console.log('[API] Config.getPlatformStatus');
    return await get('/api/config/platform-status');
  },

  /**
   * 删除平台配置
   * @param {string} platform - 平台名称
   * @returns {Promise<Object>}
   */
  async deletePlatformConfig(platform) {
    console.log('[API] Config.deletePlatformConfig', { platform });
    return await del(`/api/config/platform/${platform}`);
  },
};

// ============================================================================
// 导出统一API对象
// ============================================================================

module.exports = {
  // 各模块API
  dashboard: dashboardAPI,
  auth: authAPI,
  share: shareAPI,
  petkit: petkitAPI,
  cloudpets: cloudpetsAPI,
  scale: scaleAPI,
  config: configAPI,

  // 底层请求方法（高级用法）
  request: {
    get,
    post,
    put,
    delete: del,
  },

  // 配置
  API_VERSION,
  DEFAULT_TIMEOUT,
};
