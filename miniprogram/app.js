const cloudRequest = require('./utils/cloud_request.js');
const BLEUtils = require('./utils/ble_scale.js');

// 配置常量
const CONFIG = {
  MIN_VALID_WEIGHT: 30,  // 最小有效体重 30kg
  MAX_WEIGHT: 200,       // 最大体重 200kg
  FRESHNESS_THRESHOLD: 10000  // 数据新鲜度阈值 10秒
};

App({
  globalData: {
    // 环境标识
    environment: "development",
    // 待处理的分享 token（登录前收到，登录后处理）
    _pendingShareToken: '',
    /** 入口场景值（1007=分享卡片，其他见微信文档） */
    _pendingShareScene: 0,
    /** 启动路径（用于判断是否需要 reLaunch） */
    _launchPath: '',

    // 蓝牙状态（简化）
    bleAdapterInitialized: false,
    bluetoothInitializing: false,  // 防止并发初始化
    latestScaleData: null,
    scaleListeners: [],  // 订阅者列表
    scaleMembers: [],    // 预加载的成员数据
    lastJumpWeight: 0,   // 上次跳转时的体重
    scaleConnectionStatus: 'offline',  // 设备在线状态（基于RSSI）

    // 数据去重相关
    lastProcessedData: null,  // 上次处理的数据
    lastProcessedTimestamp: 0, // 上次处理的时间戳

    // Dashboard数据缓存（避免重复请求）
    cachedDashboardData: null,
    dashboardCacheTime: 0,
    dashboardFetching: false,  // 防止并发请求
    dashboardFetchPromise: null,  // 共享同一个Promise

    // 时间同步相关
    timeSyncInProgress: false,  // 时间同步进行中标志
    lastTimeSyncDeviceId: null,  // 上次执行时间同步的设备ID
    lastTimeSyncTimestamp: 0,  // 上次执行时间同步的时间戳

    // 页面跳转锁
    scalePageNavigationInFlight: false,  // 防止重复跳转到称重页
    suppressScaleAutoNavigate: false,  // 绑定阶段抑制自动跳转

    // 【防雪崩】缓存项定向刷新标记集合（刷新时不丢弃整个缓存）
    _staleKeys: new Set(),

    // 【新增】最后数据更新时间戳（用于智能判断是否需要强制刷新）
    _lastDataUpdateTime: 0,

    // 【修复】启动时 token 刷新 Promise，供 fetchDashboardData 等待
    _startupRefreshPromise: null,
  },

  // 已发现的体脂秤设备列表（供绑定界面使用）
  _discoveredScaleDevices: {},  // { deviceId: { deviceId, name, RSSI, lastSeen } }
  _deviceDiscoveryListeners: [],  // [{ callback, filter }]

  /**
   * 订阅蓝牙设备发现事件
   * @param {Function} callback - 每次发现新设备时调用 (devices: Array) => void
   * @returns {Function} 取消订阅函数
   */
  subscribeDeviceDiscovery(callback) {
    if (typeof callback !== 'function') {
      return function() {};
    }
    this._deviceDiscoveryListeners.push({ callback, filter: null });
    // 立即通知已有设备列表
    const devices = Object.values(this._discoveredScaleDevices);
    if (devices.length > 0) {
      try { callback(devices); } catch (e) { console.error('[Discovery] 回调失败:', e); }
    }
    return () => {
      this._deviceDiscoveryListeners =
        this._deviceDiscoveryListeners.filter(l => l.callback !== callback);
    };
  },

  /**
   * 取消订阅设备发现事件（兼容旧接口）
   */
  unsubscribeDeviceDiscovery(callback) {
    this._deviceDiscoveryListeners =
      this._deviceDiscoveryListeners.filter(l => l.callback !== callback);
  },

  /**
   * 清除已发现的设备列表
   */
  clearDiscoveredDevices() {
    this._discoveredScaleDevices = {};
  },

  onLaunch(options) {
    // 【修复】解析入口参数：启动路径、场景值、分享令牌
    if (options) {
      console.log('[App] 🚀 onLaunch:', JSON.stringify({
        path: options.path,
        scene: options.scene,
        query: options.query,
      }));
      this.globalData._launchPath = options.path || '';
      this.globalData._pendingShareScene = options.scene || 0;
      if (options.query && options.query.share_token) {
        this.globalData._pendingShareToken = options.query.share_token;
        console.log('[App] 📥 从 onLaunch 捕获 share_token:', options.query.share_token,
          '| 场景值:', options.scene, '(1007=分享卡片)');
      }
    }

    try {
      // 初始化云开发
      const config = cloudRequest.getConfig ? cloudRequest.getConfig() : null;
      if (config && config.mode === 'cloud') {
        cloudRequest.initCloud();
      }

      // 检查登录状态：优先尝试静默免密登录
      this.checkAndAutoLogin();
    } catch (err) {
      console.error('[App] onLaunch 错误:', err);
    }
  },

  onShow(options) {
    // 【修复】小程序从后台切回前台时（如点击聊天中的分享卡片），捕获 share_token
    if (options) {
      if (options.query && options.query.share_token) {
        this.globalData._pendingShareToken = options.query.share_token;
        this.globalData._pendingShareScene = options.scene || 0;
        console.log('[App] 📥 从 onShow 捕获 share_token:', options.query.share_token,
          '| 场景值:', options.scene, '(1007=分享卡片)');
      }
    }
  },

  /**
   * 检查登录状态 + 自动静默免密登录
   * 登录成功后初始化蓝牙等后续流程
   *
   * 逻辑：
   * 1. 已有 token → 异步触发静默刷新延长有效期（不阻塞），同时直接初始化后续流程
   * 2. 无 token 但 preventSilentLogin=true → 跳登录页并进入"本账号密码登录"模式
   * 3. 无 token 且未禁止静默 → 尝试 openid 静默登录
   */
  async checkAndAutoLogin() {
    const userInfo = wx.getStorageSync('userInfo');
    const token = wx.getStorageSync('token');
    const preventSilentLogin = wx.getStorageSync('preventSilentLogin');

    if (userInfo && userInfo.user_id && token) {
      console.log('[App] 已有登录态，user_id:', userInfo.user_id);
      console.log('[App] 启动路径:', this.globalData._launchPath,
        '| pendingToken:', this.globalData._pendingShareToken ? '有' : '无');

      // 【Token 无感刷新】后台异步尝试延长 token 有效期（不阻塞启动）
      // 若 token 实际已过期，cloud_request.js 的 401 兜底机制会处理
      // 【修复】将 refresh 的 Promise 存储到 globalData，供 fetchDashboardData 协调
      const cloudRequest = require('./utils/cloud_request.js');
      const refreshPromise = cloudRequest.silentRefreshToken();
      this.globalData._startupRefreshPromise = refreshPromise;
      refreshPromise
        .then(({ refreshed }) => {
          if (refreshed) {
            console.log('[App] ✅ 启动时已静默刷新 token');
          } else {
            console.log('[App] ✅ token 仍有效，无需刷新');
          }
        })
        .catch((err) => {
          // 静默失败不影响主流程，下一次请求失败时由 cloud_request.js 兜底
          console.warn('[App] ⚠️ 启动时静默刷新失败（不影响主流程）:', err);
        })
        .finally(() => {
          this.globalData._startupRefreshPromise = null;
        });

      // 【修复】关键：如果入口路径已经是 pages/index/index（分享卡片/正常启动），
      // 不需要 reLaunch。让微信系统自然创建页面，避免 reLaunch 清空 URL 参数。
      // 如果入口是其他页面，再 reLaunch 到首页并携带 share_token。
      const launchPath = this.globalData._launchPath || '';
      if (launchPath === 'pages/index/index' || !launchPath) {
        console.log('[App] ✅ 已在首页路径，跳过 reLaunch，等待页面自然创建');
        return;
      }

      // 其他路径 → reLaunch 到首页，携带 share_token
      let url = '/pages/index/index';
      if (this.globalData._pendingShareToken) {
        url += `?share_token=${this.globalData._pendingShareToken}`;
      }
      wx.reLaunch({ url });
      return;
    }

    // 检测 preventSilentLogin 标志 → 跳过静默，直接进入本账号密码登录模式
    if (preventSilentLogin) {
      console.log('[App] 已禁止静默登录，跳转至本账号密码登录');
      wx.reLaunch({ url: '/pages/login/login?mode=own_password' });
      return;
    }

    // 无登录态 → 由登录页自行处理静默登录检测与分流
    console.log('[App] 无登录态，等待登录页进行静默登录检测');
  },

  /**
   * 清除 Dashboard 缓存（登出时使用，硬清除，无降级）
   * 数据变更时请使用 refreshDashboardCache(keys) 替代此方法
   */
  clearDashboardCache() {
    this.globalData.cachedDashboardData = null;
    this.globalData.dashboardCacheTime = 0;
    this.globalData.dashboardFetching = false;
    this.globalData.dashboardFetchPromise = null;
    this.globalData._staleKeys = new Set();
    this.globalData._startupRefreshPromise = null;  // 【修复】清理启动刷新 Promise
    console.log('[App] 🧹 Dashboard 缓存已硬清除（仅登出场景使用）');
  },

  /**
   * 优雅刷新 Dashboard 缓存（替代 clearDashboardCache 暴力清除）
   * 不清空现有缓存，仅标记指定 key 为"待刷新"。
   * 下次 fetchDashboardData 会发起请求，成功后将新数据与旧缓存合并返回，
   * 即使请求失败，旧数据依然可供首页正常渲染（平滑过渡）。
   *
   * @param {string[]} staleKeys - 需要刷新的缓存项 key 列表
   *   可用 key: 'cloudpets_plans' | 'cloudpets_servings' | 'petkit_devices'
   *            | 'scale_stats' | 'device_platforms' | 'xiaomi_config'
   */
  refreshDashboardCache(staleKeys = []) {
    const g = this.globalData;
    // 【核心】不置空 cachedDashboardData，保留旧数据供降级使用
    g.dashboardCacheTime = 0;                // 使缓存过期，下次 fetch 会发请求
    g.dashboardFetching = false;             // 重置请求锁
    g.dashboardFetchPromise = null;          // 重置在途请求
    // 记录需要定向刷新的 key
    if (staleKeys.length > 0) {
      staleKeys.forEach(k => g._staleKeys.add(k));
      console.log('[App] 🔄 缓存标记为需刷新，待合并项:', staleKeys);
    } else {
      // 未指定 key 时，标记全部 key 为需刷新（兼容旧调用方）
      const allKeys = ['cloudpets_plans', 'cloudpets_servings', 'petkit_devices',
                       'scale_stats', 'device_platforms', 'xiaomi_config'];
      allKeys.forEach(k => g._staleKeys.add(k));
      console.log('[App] 🔄 缓存标记为需刷新（全部项）');
    }
  },

  /**
   * 【新增】统一的数据更新通知接口（供所有二级页面调用）
   *
   * 使用场景：
   * - 二级页面从第三方API获取新数据成功后
   * - 二级页面执行写操作（投喂、清洗、保存测量等）成功后
   * - 任何需要首页显示最新数据的场景
   *
   * 调用方式：
   *   const app = getApp();
   *   app.notifyDataUpdate('feeder', ['cloudpets_servings', 'cloudpets_plans']);
   *
   * @param {string} source - 数据来源标识（用于日志，如 'feeder', 'scale', 'petkit'）
   * @param {string[]} updatedKeys - 已更新的缓存项列表
   */
  notifyDataUpdate(source, updatedKeys = []) {
    console.log(`[App] 📢 收到数据更新通知: source=${source}, keys=`, updatedKeys);

    // 记录最后更新时间
    this.globalData._lastDataUpdateTime = Date.now();

    // 如果指定了更新的key，标记为stale；否则标记全部
    if (updatedKeys && updatedKeys.length > 0) {
      this.refreshDashboardCache(updatedKeys);
    } else {
      // 未指定key时，根据source推断
      const keyMap = {
        'feeder': ['cloudpets_servings', 'cloudpets_plans'],
        'scale': ['scale_stats', 'xiaomi_config'],
        'petkit': ['petkit_devices'],
        'device_add': ['device_platforms', 'petkit_devices', 'cloudpets_plans', 'cloudpets_servings', 'xiaomi_config', 'scale_stats'],
      };
      const inferredKeys = keyMap[source] || [];
      this.refreshDashboardCache(inferredKeys.length > 0 ? inferredKeys : []);
    }
  },
   /* @param {number} userId - 用户ID
   * @param {number} timeout - 请求超时时间（默认 15s）
   * @param {boolean} forceRefresh - 是否强制刷新（跳过缓存）
   * @returns {Promise} Dashboard数据
   *
   * 【优化】缓存策略：
   * - 正常情况：15秒缓存（原30秒，缩短以提升实时性）
   * - 从二级页面返回：5秒缓存（通过页面栈深度检测）
   * - 有 staleKeys 标记：立即刷新
   * - forceRefresh=true：立即刷新
   */
  async fetchDashboardData(userId, timeout = 15000, forceRefresh = false) {
    const g = this.globalData;

    // 【修复】如果启动时 token 刷新仍在进行中，等待完成后再发起请求
    // 避免旧 token 发起的请求触发 401 需要二次刷新
    if (g._startupRefreshPromise) {
      console.log('[App] ⏳ 等待启动时 token 刷新完成后再请求 Dashboard...');
      try {
        await g._startupRefreshPromise;
        console.log('[App] ✅ token 刷新已完成，继续 Dashboard 请求');
      } catch (e) {
        console.warn('[App] ⚠️ 启动时 token 刷新已失败（Dashboard 仍将尝试）:', e);
      }
    }

    const hasStaleKeys = g._staleKeys && g._staleKeys.size > 0;

    // 【智能缓存过期时间计算】
    // 检测是否从二级页面返回（页面栈深度变化或包含多级页面）
    const pages = getCurrentPages();
    const isReturningFromSubPage = pages.length >= 2; // 首页+至少一个二级页面

    // 动态缓存时间：从二级页面返回时5秒，否则15秒
    const dynamicCacheTTL = isReturningFromSubPage ? 5000 : 15000;

    // 如果有缓存且未过期，且没有待刷新的key，直接返回（除非强制刷新）
    const now = Date.now();
    if (!forceRefresh && !hasStaleKeys && g.cachedDashboardData && (now - g.dashboardCacheTime) < dynamicCacheTTL) {
      console.log('[App] ✅ 使用缓存的dashboard数据', {
        cacheAge: `${Math.round((now - g.dashboardCacheTime) / 1000)}s`,
        ttl: `${dynamicCacheTTL / 1000}s`,
        isReturningFromSubPage
      });
      return g.cachedDashboardData;
    }

    if (g.cachedDashboardData && (now - g.dashboardCacheTime) >= dynamicCacheTTL) {
      console.log('[App] ⏱️ Dashboard缓存已过期，重新获取', {
        cacheAge: `${Math.round((now - g.dashboardCacheTime) / 1000)}s`,
        ttl: `${dynamicCacheTTL / 1000}s`
      });
    }

    // 如果正在请求中，等待同一个Promise
    if (g.dashboardFetching && g.dashboardFetchPromise) {
      console.log('[App] ⏳ 等待已有的dashboard请求完成');
      return g.dashboardFetchPromise;
    }

    // 【防雪崩】当有 stale key 需要刷新时，给 dashboardCacheTime 加随机偏移，
    // 让同一时间触发的大量刷新请求的后续自动刷新时间错开
    if (hasStaleKeys) {
      const jitter = Math.floor(Math.random() * 10000); // 0~10s 随机
      g.dashboardCacheTime = now - 30000 + jitter;       // 使缓存处于"即将过期"而非"已过期"状态
      console.log('[App] ⏳ 处理 %d 个待刷新项，添加 %dms 防雪崩偏移', g._staleKeys.size, jitter);
    }

    // 设置请求锁
    g.dashboardFetching = true;

    // 创建带超时保护的请求 Promise
    g.dashboardFetchPromise = new Promise((resolve, reject) => {
      // 超时定时器
      const timeoutId = setTimeout(() => {
        console.error('[App] ❌ Dashboard 请求超时');
        g.dashboardFetching = false;
        g.dashboardFetchPromise = null;
        // 超时时不丢弃旧缓存，首页仍可展示旧数据而非空
        const fallback = g.cachedDashboardData;
        if (fallback) {
          resolve(fallback);
        } else {
          reject(new Error('Dashboard 请求超时'));
        }
      }, timeout);

      cloudRequest.callContainer({
        path: `/api/dashboard/data?user_id=${userId}`,
        method: 'GET',
        success: (res) => {
          clearTimeout(timeoutId);
          console.log('[App] 📦 Dashboard接口返回');

          // ================================================================
          // 【核心】缓存更新策略：
          // - forceRefresh / 首次加载 → 直接使用新响应（完全替换）
          // - 普通刷新 → 定向合并（仅更新 staleKeys + 非空新数据）
          // ================================================================
          const prev = g.cachedDashboardData;

          // 【修复】forceRefresh 或首次加载时直接使用新响应，跳过合并逻辑
          // 解决"空数组被锁定"问题：当用户主动刷新时应该看到最新数据
          if (!prev) {
            // 首次加载：无旧缓存
            g._staleKeys.clear();
            g.cachedDashboardData = res;
            console.log('[App] ✅ 首次加载，直接缓存响应数据');
          } else {
            // 有旧缓存时的处理
            const stale = [...g._staleKeys];
            g._staleKeys.clear();

            if (forceRefresh) {
              // 【强制刷新】直接使用新响应（用户主动操作，信任后端数据）
              g.cachedDashboardData = res;
              console.log('[App] 🔄 强制刷新，已完全替换缓存');
            } else {
              // 【普通刷新】定向合并策略
              const merged = { ...prev };

              // 仅对标记的 key，用新响应覆盖旧缓存
              for (const key of stale) {
                if (res[key] !== undefined) {
                  merged[key] = res[key];
                  console.log('[App] 🔄 合并刷新:', key);
                }
              }

              // 对于未被标记的 key，但新响应中明确返回了非空值，也更新（防止全量跳转遗漏）
              for (const key of Object.keys(res)) {
                if (!stale.includes(key) && res[key] !== undefined && res[key] !== null) {
                  // 新响应中的非 stale 项如果是空数组/空对象，保留旧值（避免后端临时异常）
                  if (Array.isArray(res[key]) && res[key].length === 0 && Array.isArray(merged[key]) && merged[key].length > 0) {
                    continue; // 保留旧数组
                  }
                  if (typeof res[key] === 'object' && !Array.isArray(res[key]) && res[key] !== null
                      && Object.keys(res[key]).length === 0 && merged[key] && Object.keys(merged[key]).length > 0) {
                    continue; // 保留旧对象
                  }
                  merged[key] = res[key];
                }
              }

              g.cachedDashboardData = merged;
              console.log(`[App] 📋 合并完成: stale=${stale.length}, total=${Object.keys(res).length}`);
            }
          }

          g.dashboardCacheTime = Date.now();
          g.dashboardFetching = false;
          g.dashboardFetchPromise = null;

          resolve(g.cachedDashboardData);
        },
        fail: (err) => {
          clearTimeout(timeoutId);
          console.error('[App] ❌ Dashboard接口失败:', err);

          // 【修复】失败时不丢弃旧缓存，返回缓存数据兜底
          const fallback = g.cachedDashboardData;
          if (fallback) {
            console.log('[App] 📦 使用旧缓存数据兜底，保留 %d 个待刷新项', g._staleKeys.size);
            g.dashboardFetching = false;
            g.dashboardFetchPromise = null;
            resolve(fallback);
          } else {
            g.dashboardFetching = false;
            g.dashboardFetchPromise = null;
            g.cachedDashboardData = null;
            g.dashboardCacheTime = 0;
            reject(err);
          }
        }
      });
    });

    return g.dashboardFetchPromise;
  },

  async checkAndInitBluetooth(userId) {
    if (!userId) return;

    // 如果已初始化，直接返回
    if (this.globalData.bleAdapterInitialized) {
      console.log('[BLE] ✅ 蓝牙已初始化，跳过');
      return;
    }

    // 防止并发调用
    if (this.globalData.bluetoothInitializing) {
      console.log('[BLE] ⚠️ 蓝牙正在初始化中，跳过');
      return;
    }
    this.globalData.bluetoothInitializing = true;

    console.log('[BLE] ✅ 开始初始化蓝牙');

    try {
      // 等待蓝牙适配器初始化完成（Promise化）
      await this.initBluetoothManager();
      console.log('[BLE] ✅ 蓝牙适配器初始化成功');

      // 预加载成员数据
      this.loadScaleMembers(userId);
    } catch (err) {
      console.error('[BLE] ❌ 初始化蓝牙失败:', err);
      throw err;  // 向上传播错误，让调用方处理
    } finally {
      this.globalData.bluetoothInitializing = false;
    }
  },

  initBluetoothManager() {
    // 清除旧的扫描数据，防止误触发跳转
    this.globalData.latestScaleData = null;

    if (this.globalData.bleAdapterInitialized) {
      console.log('[BLE] ✅ 蓝牙适配器已初始化，跳过');
      return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
      wx.openBluetoothAdapter({
        success: () => {
          console.log('[BLE] ✅ 蓝牙适配器初始化成功');
          this.globalData.bleAdapterInitialized = true;

          // 监听设备发现
          wx.onBluetoothDeviceFound(this.handleDeviceFound.bind(this));

          // 监听适配器状态变化
          wx.onBluetoothAdapterStateChange((res) => {
            this.globalData.bleAdapterInitialized = res.available;
            if (!res.available) {
              console.warn('[BLE] ⚠️ 蓝牙适配器不可用');
            }
          });

          // 开始持续扫描
          this.startContinuousScan();
          resolve();
        },
        fail: (err) => {
          const errMsg = (err && err.errMsg) || '';
          // already opened 视为成功（竞态条件导致）
          if (errMsg.indexOf('already opened') !== -1) {
            console.log('[BLE] ✅ 蓝牙适配器已就绪');
            this.globalData.bleAdapterInitialized = true;
            this.startContinuousScan();
            resolve();
            return;
          }
          console.error('[BLE] ❌ 蓝牙初始化失败:', err);
          this.globalData.bleAdapterInitialized = false;
          reject(err);
        }
      });
    });
  },

  startContinuousScan() {
    if (!this.globalData.bleAdapterInitialized) return;

    wx.startBluetoothDevicesDiscovery({
      allowDuplicatesKey: true,
      interval: 300,
      success: function() {
        console.log('[BLE] 📡 开始持续扫描');
      },
      fail: function(err) {
        console.error('[BLE] ❌ 扫描启动失败:', err);
      }
    });
  },

  handleDeviceFound(res) {
    const devices = res.devices || [];
    const now = Date.now();
    let foundScaleForBinding = false;

    for (let device of devices) {
      if (!device.name) continue;

      // ====== 绑定模式：记录发现的所有 BLE 设备（不限制名称）======
      this._discoveredScaleDevices[device.deviceId] = {
        deviceId: device.deviceId,
        name: device.name,
        RSSI: device.RSSI || -100,
        lastSeen: now,
      };
      foundScaleForBinding = true;

      // 设备识别：小米体脂秤 2
      const deviceName = device.name.toLowerCase();
      if (!deviceName.includes('mibfs') && !deviceName.includes('mi scale')) continue;

      // 解析广播数据
      let finalData = null;
      if (device.serviceData) {
        for (let uuid in device.serviceData) {
          finalData = BLEUtils.parse(device.serviceData[uuid]);
          if (finalData) break;
        }
      }

      if (!finalData && device.advertisData) {
        finalData = BLEUtils.parse(device.advertisData);
      }

      if (!finalData) continue;

      // =====================
      // 数据新鲜度检测（基于设备时间 vs 当前时间）
      // =====================
      const deviceTimestamp = finalData.deviceTimestamp;
      if (!deviceTimestamp) {
        console.log('[BLE] ⚠️ 无设备时间戳，跳过');
        continue;
      }

      const currentTime = Date.now();
      const timeDiff = currentTime - deviceTimestamp; // 正数表示设备时间落后，负数表示超前
      const timeDiffHours = timeDiff / (1000 * 60 * 60);

      // =====================
      // 异常时间判定与同步触发条件
      // =====================
      let shouldSyncTime = false;
      let syncReason = '';

      // 条件1：严重滞后 - 设备时间比当前时间落后超过24小时
      if (timeDiffHours > 24) {
        shouldSyncTime = true;
        syncReason = `设备时间严重滞后 ${Math.round(timeDiffHours)} 小时`;
      }
      // 条件2：时间超前 - 设备时间超出当前时间（未来时间）
      else if (timeDiffHours < 0) {
        shouldSyncTime = true;
        syncReason = `设备时间超前 ${Math.round(Math.abs(timeDiffHours))} 小时`;
      }

      if (shouldSyncTime) {
        console.log(`[BLE] ⚠️ 检测到时间异常: ${syncReason}`);
        // 异步执行时间同步，避免阻塞主线程
        this.asyncTriggerTimeSync(device.deviceId);
        // 时间异常时仍继续处理数据，但记录日志
        console.log('[BLE] ⚠️ 注意：设备时间异常，数据可能不准确');
      }

      // =====================
      // 数据新鲜度过滤（正常流程）
      // =====================
      const parseTime = finalData.receivedAt; // 广播解析时的实时时间
      const freshnessDiff = Math.abs(parseTime - deviceTimestamp);

      // 如果时间差超过10秒，视为过期数据
      if (freshnessDiff >= CONFIG.FRESHNESS_THRESHOLD) {
        console.log('[BLE] ⚠️ 数据过期，丢弃');
        continue;
      }

      // 数据去重：检查是否与上次处理的数据相同
      const isDuplicate = this.isDuplicateData(finalData);
      if (isDuplicate) {
        console.log('[BLE] ⚠️ 重复数据，跳过处理');
        continue;
      }

      // 更新全局数据并发布
      this.globalData.latestScaleData = {
        ...finalData,
        deviceId: device.deviceId,
        RSSI: device.RSSI
      };

      // 记录本次处理的数据
      this.globalData.lastProcessedData = finalData;
      this.globalData.lastProcessedTimestamp = Date.now();

      this.notifyScaleListeners(this.globalData.latestScaleData);

      // 更新设备在线状态
      const isOnline = device.RSSI >= -85 && device.RSSI <= -35;
      this.globalData.scaleConnectionStatus = isOnline ? 'online' : 'offline';

      // 绑定阶段 / 已在称重页，均跳过自动跳转
      if (this.globalData.suppressScaleAutoNavigate) {
        console.log('[BLE] \u23F8\uFE0F 绑定阶段，跳过自动跳转');
        continue;
      }
      if (this.isCurrentPage('pages/scale/scale')) {
        console.log('[BLE] \u23F8\uFE0F 已在称重页，跳过');
        continue;
      }

      // 只要数据新鲜且未处于跳转中，就跳转
      if (!this.globalData.scalePageNavigationInFlight) {
        this.checkAndNavigateToScalePage(finalData.weight);
      } else {
        console.log('[BLE] \u23F8\uFE0F 跳转进行中，跳过');
      }
    }

    // 批量通知设备发现订阅者（每批次扫描结果统一通知一次）
    if (foundScaleForBinding) {
      const allDevices = Object.values(this._discoveredScaleDevices);
      this._deviceDiscoveryListeners.forEach(l => {
        try { l.callback(allDevices); } catch (e) { console.error('[Discovery] 通知失败:', e); }
      });
    }
  },

  // =====================
  // 发布-订阅模式
  // =====================

  /**
   * 订阅体脂秤数据
   * @param {Function} callback - 回调函数 (data) => void
   * @returns {Function} 取消订阅函数
   */
  subscribeScaleData(callback) {
    if (typeof callback !== 'function') {
      return function() {};
    }

    this.globalData.scaleListeners.push(callback);

    // 如果有最新数据，立即通知
    if (this.globalData.latestScaleData) {
      try {
        callback(this.globalData.latestScaleData);
      } catch (err) {
        console.error('[BLE] 订阅回调失败:', err);
      }
    }

    // 返回取消订阅函数
    return () => {
      this.unsubscribeScaleData(callback);
    };
  },

  /**
   * 取消订阅
   */
  unsubscribeScaleData(callback) {
    const index = this.globalData.scaleListeners.indexOf(callback);
    if (index > -1) {
      this.globalData.scaleListeners.splice(index, 1);
    }
  },

  /**
   * 通知所有订阅者
   */
  notifyScaleListeners(data) {
    this.globalData.scaleListeners.forEach((cb) => {
      try {
        cb(data);
      } catch (err) {
        console.error('[BLE] 订阅者回调失败:', err);
      }
    });
  },

  /**
   * 检查当前页面是否为指定路由
   * @param {string} route - 要检查的路由
   * @returns {boolean}
   */
  isCurrentPage(route) {
    const pages = getCurrentPages();
    const currentPage = pages[pages.length - 1];
    return currentPage && currentPage.route === route;
  },

  /**
   * 预加载体脂秤成员数据
   */
  async loadScaleMembers(userId) {
    try {
      console.log('[BLE] 🔍 开始加载成员数据...');

      const res = await new Promise((resolve, reject) => {
        cloudRequest.callContainer({
          path: `/api/scale/members?user_id=${userId}`,
          method: 'GET',
          success: resolve,
          fail: reject
        });
      });

      console.log('[BLE] 📦 成员接口返回:', res);

      // 注意：res 直接是 {code, data} 结构，或者就是数组
      const members = res.code === 200 && res.data ? res.data : (Array.isArray(res) ? res : []);

      if (members.length > 0) {
        // 存储到全局，供页面使用
        this.globalData.scaleMembers = members;
        console.log(`[BLE] ✅ 预加载 ${members.length} 个成员数据`);
      } else {
        console.warn('[BLE] ⚠️ 未找到成员数据');
      }
    } catch (err) {
      console.error('[BLE] ❌ 预加载成员数据失败:', err);
    }
  },

  // =====================
  // BLE 时间同步相关方法（使用 BLEBridge 统一封装）
  // =====================

  /**
   * 构建时间同步数据（10字节，UTC 时间）
   */
  buildTimeSyncData(date) {
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth() + 1;
    const day = date.getUTCDate();
    const hour = date.getUTCHours();
    const minute = date.getUTCMinutes();
    const second = date.getUTCSeconds();

    const buffer = new ArrayBuffer(10);
    const view = new DataView(buffer);
    view.setUint16(0, year, true);
    view.setUint8(2, month);
    view.setUint8(3, day);
    view.setUint8(4, hour);
    view.setUint8(5, minute);
    view.setUint8(6, second);
    view.setUint8(7, 0x03);
    view.setUint8(8, 0x00);
    view.setUint8(9, 0x00);
    return buffer;
  },

  /**
   * 执行设备时间同步（使用 BLEBridge）
   * @param {string} deviceId - 设备ID
   */
  async executeTimeSync(deviceId) {
    console.log('[BLE] 🕐 开始时间同步流程...');
    const ble = BLEUtils.BLEBridge;

    try {
      await ble.createConnection(deviceId);
      console.log('[BLE] ✅ BLE 连接成功');

      const services = await ble.getServices(deviceId);
      console.log('[BLE] 📦 发现服务数量:', services.length);

      const bodyCompService = services.find(s => {
        const uuid = s.uuid.replace(/-/g, '').toLowerCase();
        return uuid.includes('181b');
      });
      if (!bodyCompService) throw new Error('未找到体成分服务 (0x181b)');

      const characteristics = await ble.getCharacteristics(deviceId, bodyCompService.uuid);
      const currentTimeChar = characteristics.find(c => {
        const uuid = c.uuid.replace(/-/g, '').toLowerCase();
        return uuid.includes('2a2b');
      });
      if (!currentTimeChar) throw new Error('未找到 Current Time 特征 (0x2a2b)');

      const timeData = this.buildTimeSyncData(new Date());
      await ble.writeValue(deviceId, bodyCompService.uuid, currentTimeChar.uuid, timeData);
      console.log('[BLE] ✅ 时间写入成功');

      await ble.closeConnection(deviceId);
      console.log('[BLE] 🔌 已断开连接，等待设备重启...');
      await new Promise(resolve => setTimeout(resolve, 2000));
      console.log('[BLE] ✅ 时间同步完成！');

    } catch (err) {
      console.error('[BLE] ❌ 时间同步失败:', err.message);

      try {
        await ble.closeConnection(deviceId);
      } catch (closeErr) {
        console.warn('[BLE] ⚠️ 关闭连接失败:', closeErr.message);
      }

      wx.showToast({
        title: '时间同步失败，请重试',
        icon: 'none',
        duration: 3000
      });

      throw err;
    }
  },

  /**
   * 异步触发时间同步（带全局防重机制）
   * @param {string} deviceId - 设备ID
   */
  asyncTriggerTimeSync(deviceId) {
    const now = Date.now();
    const global = this.globalData;

    // 全局防重检查：同一设备在5分钟内不重复触发
    if (global.timeSyncInProgress) {
      console.log('[BLE] ⏳ 时间同步已在进行中，跳过');
      return;
    }

    if (global.lastTimeSyncDeviceId === deviceId &&
        (now - global.lastTimeSyncTimestamp) < 5 * 60 * 1000) {
      console.log('[BLE] ⏸️ 该设备最近已执行过时间同步，跳过');
      return;
    }

    // 设置全局锁
    global.timeSyncInProgress = true;
    global.lastTimeSyncDeviceId = deviceId;
    global.lastTimeSyncTimestamp = now;

    console.log('[BLE] 🕐 异步触发时间同步...');

    // 异步执行，不阻塞主线程
    setTimeout(() => {
      // 1. 先停止扫描
      wx.stopBluetoothDevicesDiscovery({
        success: () => console.log('[BLE] ⏸️ 已停止扫描，准备同步时间'),
        fail: (err) => console.warn('[BLE] ⚠️ 停止扫描失败:', err)
      });

      // 2. 执行时间同步
      this.executeTimeSync(deviceId)
        .then(() => {
          console.log('[BLE] ✅ 时间同步完成');
        })
        .catch((err) => {
          console.error('[BLE] ❌ 时间同步失败:', err);
        })
        .finally(() => {
          // 3. 无论成功失败，都恢复扫描并释放锁
          if (this.globalData.bleAdapterInitialized) {
            this.startContinuousScan();
            console.log('[BLE] 📡 已恢复扫描');
          }
          global.timeSyncInProgress = false;
        });
    }, 100); // 延迟100ms执行，确保不阻塞当前扫描流程
  },

  /**
   * 检查是否为重复数据
   * @param {Object} newData - 新接收的数据
   * @returns {boolean} 是否为重复数据
   */
  isDuplicateData(newData) {
    const lastData = this.globalData.lastProcessedData;
    if (!lastData) return false;

    // 时间间隔超过5秒，不视为重复（可能是新的测量）
    const timeDiff = Date.now() - this.globalData.lastProcessedTimestamp;
    if (timeDiff > 5000) return false;

    // 如果体重相同且阻抗也相同，则认为是重复数据
    const isSameWeight = Math.abs(lastData.weight - newData.weight) < 0.01;
    const isSameImpedance = (lastData.impedance || 0) === (newData.impedance || 0);
    const isSameStabilized = lastData.isStabilized === newData.isStabilized;

    // 关键：如果新数据有阻抗而旧数据没有，必须视为新数据（即使体重相同）
    if (isSameWeight && !lastData.impedanceValid && newData.impedanceValid) {
      console.log('[BLE] 🆕 新数据包含阻抗，视为非重复');
      return false;
    }

    // 关键：如果旧数据已有阻抗，新数据阻抗不同，也视为新数据
    if (isSameWeight && lastData.impedanceValid && newData.impedanceValid && !isSameImpedance) {
      console.log('[BLE] 🆕 阻抗数据变化，视为非重复');
      return false;
    }

    // 如果所有关键指标都相同，则认为是重复数据
    return isSameWeight && isSameImpedance && isSameStabilized;
  },

  checkAndNavigateToScalePage(currentWeight) {
    const global = this.globalData;

    // 跳转锁：防止页面栈溢出
    if (global.scalePageNavigationInFlight) {
      console.log('[BLE] ⏸️ 跳转进行中，跳过');
      return;
    }

    // 已在称重页则跳过
    if (this.isCurrentPage('pages/scale/scale')) {
      console.log('[BLE] ⏸️ 已在称重页，跳过');
      return;
    }

    // 记录本次跳转的体重
    global.lastJumpWeight = currentWeight;
    global.scalePageNavigationInFlight = true;

    console.log('[BLE] 🚀 开始跳转...');
    wx.vibrateShort({ type: 'medium' });
    wx.showLoading({ title: '连接秤...', mask: true });

    const releaseLock = () => {
      // 缩短延迟至 300ms，避免 BLE 300ms 扫描周期内的重复跳转
      setTimeout(() => {
        this.globalData.scalePageNavigationInFlight = false;
        wx.hideLoading();
      }, 300);
    };

    wx.navigateTo({
      url: '/pages/scale/scale',
      fail: () => {
        wx.redirectTo({ url: '/pages/scale/scale' });
      },
      complete: releaseLock
    });
  }

});
