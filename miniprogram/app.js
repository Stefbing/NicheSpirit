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

    // 小米配置检查缓存
    xiaomiConfigChecked: false,
    hasXiaomiConfig: false,

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
    scalePageNavigationInFlight: false  // 防止重复跳转到称重页
  },

  onLaunch() {
    try {
      // 初始化云开发
      const config = cloudRequest.getConfig ? cloudRequest.getConfig() : null;
      if (config && config.mode === 'cloud') {
        cloudRequest.initCloud();
      }

      // 检查登录状态，初始化蓝牙
      const userInfo = wx.getStorageSync('userInfo');
      if (userInfo && userInfo.user_id) {
        setTimeout(() => {
          this.checkAndInitBluetooth(userInfo.user_id);
        }, 500);
      }
    } catch (err) {
      console.error('[App] onLaunch 错误:', err);
    }
  },

  onShow() {
    // 小程序从后台切回前台时，不做处理
    // 数据清除已在 startContinuousScan 中执行
  },

  /**
   * 获取Dashboard数据（带防重复请求机制）
   * @param {number} userId - 用户ID
   * @returns {Promise} Dashboard数据
   */
  async fetchDashboardData(userId) {
    // 如果有缓存且未过期，直接返回
    const now = Date.now();
    if (this.globalData.cachedDashboardData && (now - this.globalData.dashboardCacheTime) < 30000) {
      console.log('[App] ✅ 使用缓存的dashboard数据');
      return this.globalData.cachedDashboardData;
    }

    // 如果正在请求中，等待同一个Promise
    if (this.globalData.dashboardFetching && this.globalData.dashboardFetchPromise) {
      console.log('[App] ⏳ 等待已有的dashboard请求完成');
      return this.globalData.dashboardFetchPromise;
    }

    // 设置请求锁
    this.globalData.dashboardFetching = true;

    // 创建新的请求Promise
    this.globalData.dashboardFetchPromise = new Promise((resolve, reject) => {
      cloudRequest.callContainer({
        path: `/api/dashboard/data?user_id=${userId}`,
        method: 'GET',
        success: (res) => {
          console.log('[App] 📦 Dashboard接口返回');

          // 缓存数据
          this.globalData.cachedDashboardData = res;
          this.globalData.dashboardCacheTime = Date.now();

          // 释放锁
          this.globalData.dashboardFetching = false;
          this.globalData.dashboardFetchPromise = null;

          resolve(res);
        },
        fail: (err) => {
          console.error('[App] ❌ Dashboard接口失败:', err);

          // 释放锁并清除缓存
          this.globalData.dashboardFetching = false;
          this.globalData.dashboardFetchPromise = null;
          this.globalData.cachedDashboardData = null;
          this.globalData.dashboardCacheTime = 0;

          reject(err);
        }
      });
    });

    return this.globalData.dashboardFetchPromise;
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

    // 使用缓存的配置检查结果
    if (this.globalData.xiaomiConfigChecked) {
      if (!this.globalData.hasXiaomiConfig) {
        console.log('[BLE] ⚠️ 已检查过，未配置小米账号');
        this.globalData.bluetoothInitializing = false;
        return;
      }
      // 有配置但未初始化，继续执行
      console.log('[BLE] ⚡ 使用缓存配置，直接初始化');
    } else {
      // 首次检查配置
      try {
        console.log('[BLE] 🔍 检查小米配置...');

        // 使用统一的fetchDashboardData方法
        const res = await this.fetchDashboardData(userId);

        console.log('[BLE] 📦 接口返回:', res);


        // 注意：res 没有 data 字段，直接访问 xiaomi_config
        const hasXiaomiConfig = res.xiaomi_config === true;

        // 缓存结果
        this.globalData.xiaomiConfigChecked = true;
        this.globalData.hasXiaomiConfig = hasXiaomiConfig;

        if (!hasXiaomiConfig) {
          console.log('[BLE] ❌ 未配置小米账号，跳过蓝牙初始化');
          return;
        }
      } catch (err) {
        console.error('[BLE] ❌ 配置检查失败:', err);
        // 重置缓存标志，允许下次重试
        this.globalData.xiaomiConfigChecked = false;
        this.globalData.hasXiaomiConfig = false;
        return;
      }
    }

    // 初始化蓝牙
    try {
      console.log('[BLE] ✅ 配置检查通过，开始初始化蓝牙');
      this.initBluetoothManager();

      // 预加载成员数据
      this.loadScaleMembers(userId);
    } catch (err) {
      console.error('[BLE] ❌ 初始化蓝牙失败:', err);
    } finally {
      // 释放锁
      this.globalData.bluetoothInitializing = false;
    }
  },

  initBluetoothManager() {
    // 清除旧的扫描数据，防止误触发跳转
    this.globalData.latestScaleData = null;

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

        // 开始持续扫描（不再周期性停止）
        this.startContinuousScan();
      },
      fail: (err) => {
        console.error('[BLE] ❌ 蓝牙初始化失败:', err);
        this.globalData.bleAdapterInitialized = false;
      }
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
    for (let device of devices) {
      if (!device.name) continue;

      // 设备识别：小米体脂秤 2
      const deviceName = device.name.toLowerCase();
      if (!deviceName.includes('mibfs') && !deviceName.includes('mi scale')) continue;

      // 解析数据
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
        RSSI: device.RSSI,
        timestamp: parseTime
      };

      // 记录本次处理的数据
      this.globalData.lastProcessedData = finalData;
      this.globalData.lastProcessedTimestamp = Date.now();

      this.notifyScaleListeners(this.globalData.latestScaleData);

      // 更新设备在线状态
      const isOnline = device.RSSI >= -85 && device.RSSI <= -35;
      this.globalData.scaleConnectionStatus = isOnline ? 'online' : 'offline';

      // 已在称重页则跳过跳转
      if (this.isCurrentPage('pages/scale/scale')) {
        console.log('[BLE] ⏸️ 已在称重页，跳过');
        return;
      }

      // 【修改】只要数据新鲜且未处于跳转中，就跳转
      // 不再判断体重是否变化，让称重页自己处理数据更新
      if (!this.globalData.scalePageNavigationInFlight) {
        this.checkAndNavigateToScalePage(finalData.weight);
      } else {
        console.log('[BLE] ⏸️ 跳转进行中，跳过');
      }
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
  // BLE 时间同步相关方法
  // =====================

  /**
   * 创建 BLE 连接（Promise 封装）
   */
  createBLEConnection(deviceId) {
    return new Promise((resolve, reject) => {
      wx.createBLEConnection({
        deviceId,
        timeout: 10000,
        success: resolve,
        fail: reject
      });
    });
  },

  /**
   * 获取 BLE 设备服务（Promise 封装）
   */
  getBLEDeviceServices(deviceId) {
    return new Promise((resolve, reject) => {
      wx.getBLEDeviceServices({
        deviceId,
        success: (res) => resolve(res.services),
        fail: reject
      });
    });
  },

  /**
   * 获取 BLE 特征值（Promise 封装）
   */
  getBLEDeviceCharacteristics(deviceId, serviceId) {
    return new Promise((resolve, reject) => {
      wx.getBLEDeviceCharacteristics({
        deviceId,
        serviceId,
        success: (res) => resolve(res.characteristics),
        fail: reject
      });
    });
  },

  /**
   * 写入 BLE 特征值（Promise 封装）
   */
  writeBLECharacteristicValue(deviceId, serviceId, characteristicId, value) {
    return new Promise((resolve, reject) => {
      wx.writeBLECharacteristicValue({
        deviceId,
        serviceId,
        characteristicId,
        value,
        success: resolve,
        fail: reject
      });
    });
  },

  /**
   * 关闭 BLE 连接（Promise 封装）
   */
  closeBLEConnection(deviceId) {
    return new Promise((resolve, reject) => {
      wx.closeBLEConnection({
        deviceId,
        success: resolve,
        fail: reject
      });
    });
  },

  /**
   * 构建时间同步数据（10字节）
   * 格式：
   * Byte 0-1: Year (Little Endian)
   * Byte 2: Month (1-12)
   * Byte 3: Day (1-31)
   * Byte 4: Hour (0-23)
   * Byte 5: Minute (0-59)
   * Byte 6: Second (0-59)
   * Byte 7: 0x03 (固定值)
   * Byte 8: 0x00 (固定值)
   * Byte 9: 0x00 (固定值)
   *
   * 注意：设备期望接收 UTC 时间，不是本地时间
   */
  buildTimeSyncData(date) {
    // 使用 UTC 时间，因为设备广播和解析都使用 UTC
    const year = date.getUTCFullYear();
    const month = date.getUTCMonth() + 1;
    const day = date.getUTCDate();
    const hour = date.getUTCHours();
    const minute = date.getUTCMinutes();
    const second = date.getUTCSeconds();

    const buffer = new ArrayBuffer(10);
    const view = new DataView(buffer);

    // Year (Little Endian)
    view.setUint16(0, year, true);
    // Month
    view.setUint8(2, month);
    // Day
    view.setUint8(3, day);
    // Hour
    view.setUint8(4, hour);
    // Minute
    view.setUint8(5, minute);
    // Second
    view.setUint8(6, second);
    // Fixed bytes
    view.setUint8(7, 0x03);
    view.setUint8(8, 0x00);
    view.setUint8(9, 0x00);

    return buffer;
  },

  /**
   * 执行设备时间同步（核心逻辑）
   * @param {string} deviceId - 设备ID
   */
  async executeTimeSync(deviceId) {
    console.log('[BLE] 🕐 开始时间同步流程...');

    try {
      // 1. 创建 BLE 连接
      await this.createBLEConnection(deviceId);
      console.log('[BLE] ✅ BLE 连接成功');

      // 2. 获取服务列表
      const services = await this.getBLEDeviceServices(deviceId);
      console.log('[BLE] 📦 发现服务数量:', services.length);

      // 3. 查找 Body Composition Service (0x181b)
      const bodyCompService = services.find(s => {
        const uuid = s.uuid.replace(/-/g, '').toLowerCase();
        return uuid.includes('181b');
      });

      if (!bodyCompService) {
        throw new Error('未找到体成分服务 (0x181b)');
      }

      // 4. 获取特征值列表
      const characteristics = await this.getBLEDeviceCharacteristics(
        deviceId,
        bodyCompService.uuid
      );

      // 5. 查找 Current Time 特征 (0x2a2b)
      const currentTimeChar = characteristics.find(c => {
        const uuid = c.uuid.replace(/-/g, '').toLowerCase();
        return uuid.includes('2a2b');
      });

      if (!currentTimeChar) {
        throw new Error('未找到 Current Time 特征 (0x2a2b)');
      }

      // 6. 构建并写入时间数据（10字节）
      const timeData = this.buildTimeSyncData(new Date());
      await this.writeBLECharacteristicValue(
        deviceId,
        bodyCompService.uuid,
        currentTimeChar.uuid,
        timeData
      );
      console.log('[BLE] ✅ 时间写入成功');

      // 7. 断开连接（让设备重启以激活新时间）
      await this.closeBLEConnection(deviceId);
      console.log('[BLE] 🔌 已断开连接，等待设备重启...');

      // 8. 等待设备重启完成（2秒）
      await new Promise(resolve => setTimeout(resolve, 2000));

      console.log('[BLE] ✅ 时间同步完成！');

    } catch (err) {
      console.error('[BLE] ❌ 时间同步失败:', err.message);

      // 确保关闭连接
      try {
        await this.closeBLEConnection(deviceId);
      } catch (closeErr) {
        console.warn('[BLE] ⚠️ 关闭连接失败:', closeErr.message);
      }

      wx.showToast({
        title: '时间同步失败，请重试',
        icon: 'none',
        duration: 3000
      });

      throw err; // 重新抛出错误，让调用者处理
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
      setTimeout(() => {
        this.globalData.scalePageNavigationInFlight = false;
        wx.hideLoading();
      }, 1000);
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
