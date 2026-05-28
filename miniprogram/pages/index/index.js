const app = getApp()
const cloudRequest = require('../../utils/cloud_request.js')

Page({
  data: {
    userInfo: null,
    userDevices: [],
    petDevices: [],
    healthDevices: [],
    showAddDeviceDialog: false,
    showDeviceConfigDialog: false,
    selectedDeviceType: '',
    selectedPlatform: '',
    selectedDeviceTypeText: '',
    deviceAccount: '',
    devicePassword: '',
    greeting: '',
    devicesLoaded: false,
    // 体脂秤绑定流程
    showScalePermissionDialog: false,
    showScaleScanningDialog: false,
    scaleScanning: false,
    scaleDiscoveredDevices: [],
    scaleScanHelp: '',
    // 分享相关
    showShareConfirm: false,
    showAcceptShare: false,
    shareToken: '',
    shareFromName: '',
  },
  
  onLoad: function (query) {
    wx.hideHomeButton();

    // 【修复】在检查登录状态前，优先捕获分享token存到全局，避免跳转登录后丢失
    if (query && query.share_token) {
      const app = getApp()
      app.globalData._pendingShareToken = query.share_token
      console.log('[首页] 📥 从分享卡片捕获 share_token:', query.share_token)
    }

    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this.setData({ userInfo });

    this.updateGreeting();
    this.loadUserDevices();
  },
  
  onShow: function() {
    wx.hideShareMenu({ menus: ['share', 'shareTimeline'] });
    wx.hideLoading();

    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    if (!this.data.userInfo) {
      this.setData({ userInfo });
    }

    this.updateGreeting();

    const pendingToken = app.globalData._pendingShareToken;
    if (pendingToken && this.data.userInfo && this.data.userInfo.user_id) {
      app.globalData._pendingShareToken = '';
      this.setData({ shareToken: pendingToken });
      this.handleIncomingShare(pendingToken);
    }

    if (this.data.userInfo) {
      this.registerDeviceStatusListener();

      // 每次页面显示都重新加载设备列表（防缓存/并发导致数据未及时渲染）
      console.log('[首页] 🔄 页面显示，刷新设备列表');
      this.loadUserDevices();
    }
  },
  
  // 注册时间问候语
  updateGreeting() {
    const hour = new Date().getHours()
    let greeting = ''
    
    if (hour >= 5 && hour < 9) {
      greeting = '早上好'
    } else if (hour >= 9 && hour < 12) {
      greeting = '上午好'
    } else if (hour >= 12 && hour < 14) {
      greeting = '中午好'
    } else if (hour >= 14 && hour < 18) {
      greeting = '下午好'
    } else if (hour >= 18 && hour < 22) {
      greeting = '晚上好'
    } else {
      greeting = '晚上好'
    }
    
    this.setData({ greeting })
  },
  
  /**
   * 注册设备状态更新监听
   */
  registerDeviceStatusListener() {
    // 在页面显示时检查设备状态
    this.checkCurrentDeviceStatus();
    
    // 设置定时器定期检查设备状态（每5秒）
    // 先清除旧定时器，避免重复创建
    if (this.deviceStatusTimer) {
      clearInterval(this.deviceStatusTimer);
    }
    this.deviceStatusTimer = setInterval(() => {
      this.checkCurrentDeviceStatus();
    }, 5000);
  },
  
  /**
   * 检查当前设备在线状态
   */
  checkCurrentDeviceStatus() {
    const app = getApp();
    const status = app.globalData.scaleConnectionStatus || 'offline';
    console.log('[首页] 检查设备状态:', status);
    
    const healthDevices = this.data.healthDevices.map(device => {
      if (device.device_type === 'scale') {
        // 对于体脂秤，使用全局扫描状态
        return {
          ...device,
          connectionStatus: status
        };
      }
      return device;
    });
    
    this.setData({ healthDevices });
  },
  
  /**
   * 更新设备在线状态
   */
  updateDeviceOnlineStatus(onlineStatusMap) {
    const app = getApp();
    const healthDevices = this.data.healthDevices.map(device => {
      if (device.device_type === 'scale') {
        // 使用全局扫描状态
        return {
          ...device,
          connectionStatus: app.globalData.scaleConnectionStatus || 'offline'
        };
      }
      return device;
    });
    
    this.setData({ healthDevices });
    console.log('[首页] 设备在线状态已更新:', healthDevices);
  },
  
  // 检查登录状态
  checkLoginStatus() {
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) {
      wx.reLaunch({ url: '/pages/login/login' });
      return;
    }
    this.setData({ userInfo });
    if (!this.data.devicesLoaded) {
      this.loadUserDevices();
    }
  },
  
  // 加载用户设备列表
  async loadUserDevices() {
    if (!this.data.userInfo || !this.data.userInfo.user_id) return
    
    // 防止重复请求
    if (this.isLoadingDevices) {
      console.log('[首页] 设备数据正在加载中，跳过重复请求')
      return
    }
    
    this.isLoadingDevices = true
    
    try {
      // 使用app.js中统一的fetchDashboardData方法（防重复请求）
      const app = getApp()
      const dashboardData = await app.fetchDashboardData(this.data.userInfo.user_id)
      
      console.log('[首页] 📦 获取到dashboard数据')
      console.log('[首页] 🔍 xiaomi_config:', dashboardData.xiaomi_config)
      console.log('[首页] 🔍 scale_stats:', dashboardData.scale_stats)
      const petDevices = []
      const healthDevices = []
      const userDevices = []

      // 从 device_platforms（缓存分组）渲染设备卡片
      // 显示依据：设备配置完整性 is_complete，而非云 API 返回数据
      const platforms = dashboardData.device_platforms || []

      for (const plat of platforms) {
        // 跳过未完整配置的设备
        if (!plat.is_complete) continue

        if (plat.is_ble) {
          // 体脂秤 — 本地蓝牙设备
          const scaleDevice = {
            device_key: plat.device_key,
            device_type: 'scale',
            device_name: plat.device_name,
            display_name: plat.device_name || 'MIBFS',
            platform: plat.platform,
            status: 'active',
            online: false,
            today_measurements: 0,
            latest_body_fat: null,
            is_shared: plat.is_shared || false,
          }

          const scaleStats = dashboardData.scale_stats
          if (scaleStats && typeof scaleStats === 'object' && 'today_count' in scaleStats) {
            scaleDevice.today_measurements = scaleStats.today_count || 0
            scaleDevice.latest_body_fat = scaleStats.latest_body_fat !== undefined ? scaleStats.latest_body_fat : null
          }

          healthDevices.push(scaleDevice)
          userDevices.push(scaleDevice)

        } else if (plat.platform === 'cloudpets') {
          // CloudPets 喂食机 — 卡片始终显示（is_complete=true），详情数据可选
          const feederDevice = {
            device_key: plat.device_key,
            device_type: 'feeder',
            device_name: plat.device_name,
            display_name: plat.device_name || '喂食机',
            platform: plat.platform,
            status: 'active',
            today_servings: 0,
            remaining_plans: 0,
            is_shared: plat.is_shared || false,
          }

          const servingsData = dashboardData.cloudpets_servings
          if (servingsData && typeof servingsData === 'object') {
            feederDevice.today_servings = servingsData.result || 0
          } else if (typeof servingsData === 'number') {
            feederDevice.today_servings = servingsData
          }

          const plans = dashboardData.cloudpets_plans || []
          if (Array.isArray(plans)) {
            const now = new Date()
            const currentMinutes = now.getHours() * 60 + now.getMinutes()
            let remaining = 0
            plans.forEach(p => {
              if (p.time && p.enabled !== false && p.enabled !== 0 && p.enabled !== '0') {
                const [h, m] = p.time.split(':').map(Number)
                if ((h * 60 + m) > currentMinutes) remaining++
              }
            })
            feederDevice.remaining_plans = remaining
          }

          petDevices.push(feederDevice)
          userDevices.push(feederDevice)

        } else if (plat.platform === 'petkit') {
          // PetKit 猫厕所 — 卡片始终显示（is_complete=true），详情数据可选
          const petkitDevices = dashboardData.petkit_devices || []

          const litterboxDevice = {
            device_key: plat.device_key,
            device_type: 'litterbox',
            device_name: plat.device_name,
            display_name: plat.device_name || '猫厕所',
            platform: plat.platform,
            status: 'active',
            today_visits: 0,
            sand_level: 0,
            is_shared: plat.is_shared || false,
          }

          const litterboxStats = dashboardData.litterbox_stats || {}
          const found = petkitDevices.find(d => {
            if (!d || !d.type) return false
            const name = d.name || ''
            return ['T3', 'T4', 'T4 Pura MAX', 'T5'].includes(d.type) || name.includes('MAX')
          })

          if (found) {
            let stats = {}
            if (litterboxStats[found.id]) stats = litterboxStats[found.id]
            else if (found.state_summary) stats = found.state_summary
            litterboxDevice.today_visits = stats.today_visits !== undefined ? stats.today_visits : 0
            litterboxDevice.sand_level = stats.sand_percent || 0
          }

          petDevices.push(litterboxDevice)
          userDevices.push(litterboxDevice)
        }
      }
      
      this.setData({
        userDevices,
        petDevices,
        healthDevices,
        devicesLoaded: true
      })

      // BLE 条件启动：仅当用户配置了体脂秤时才初始化蓝牙
      if (dashboardData.xiaomi_config === true) {
        const app = getApp()
        if (!app.globalData.bleAdapterInitialized && !app.globalData.bluetoothInitializing) {
          app.checkAndInitBluetooth(this.data.userInfo.user_id)
        }
      }
    } catch (err) {
      console.error('加载设备列表失败:', err)
      wx.showToast({ 
        title: '加载失败，请重试', 
        icon: 'none' 
      })
    } finally {
      this.isLoadingDevices = false
    }
  },
  
  // 显示添加设备弹窗
  showAddDeviceModal() {
    this.setData({ showAddDeviceDialog: true })
  },

  // 关闭添加设备弹窗
  closeAddDeviceModal() {
    this.setData({ showAddDeviceDialog: false })
  },

  // 选择设备类型
  selectDeviceType(e) {
    const type = e.currentTarget.dataset.type
    const platform = e.currentTarget.dataset.platform
    const typeMap = {
      'feeder': '喂食机',
      'litterbox': '猫厕所',
      'scale': '体脂秤'
    }

    if (type === 'scale') {
      // 体脂秤：启动本地蓝牙绑定流程
      this.setData({
        showAddDeviceDialog: false,
        showScalePermissionDialog: true,
      })
      return
    }

    this.setData({
      selectedDeviceType: type,
      selectedPlatform: platform,
      selectedDeviceTypeText: typeMap[type],
      showAddDeviceDialog: false,
      showDeviceConfigDialog: true,
      deviceAccount: '',
      devicePassword: ''
    })
  },

  // 关闭设备配置弹窗
  closeDeviceConfigModal() {
    this.setData({
      showDeviceConfigDialog: false,
      deviceAccount: '',
      devicePassword: ''
    })
  },

  // 账号输入
  onAccountInput(e) {
    this.setData({ deviceAccount: e.detail.value })
  },
  
  // 密码输入
  onPasswordInput(e) {
    this.setData({ devicePassword: e.detail.value })
  },

  // 提交设备配置
  async onSubmitDeviceConfig() {
    const { selectedDeviceType, selectedPlatform, deviceAccount, devicePassword } = this.data
    
    // 体脂秤为本地设备，无需云账号密码
    if (selectedDeviceType !== 'scale' && (!deviceAccount || !devicePassword)) {
      wx.showToast({ title: '请填写完整信息', icon: 'none' })
      return
    }
    
    wx.showLoading({ title: '添加中...' })
    
    try {
      await cloudRequest.callContainer({
        path: `/api/devices/add?user_id=${this.data.userInfo.user_id}`,
        method: 'POST',
        data: {
          device_type: selectedDeviceType,
          platform: selectedPlatform,
          account: selectedDeviceType === 'scale' ? 'local' : deviceAccount,
          password: selectedDeviceType === 'scale' ? 'local' : devicePassword
        }
      })
      
      // 如果是体脂秤，自动初始化"自己"成员（BLE 由后续 loadUserDevices 根据 xiaomi_config 条件启动）
      if (selectedDeviceType === 'scale') {
        await this.initScaleSelfMember()
        console.log('[首页] 体脂秤添加成功')
      }
      
      wx.hideLoading()
      wx.showToast({ title: '添加成功', icon: 'success' })
      
      this.closeDeviceConfigModal()
      // 清除缓存及在途请求，强制刷新
      const app = getApp()
      app.clearDashboardCache()
      await this.loadUserDevices()
    } catch (err) {
      wx.hideLoading()
      console.error('添加设备失败:', err)

      const errData = err && err.data
      wx.showModal({
        title: '添加失败',
        content: (errData && errData.detail) || '请检查账号密码后重试',
        showCancel: false,
      })
    }
  },



  // 初始化体脂秤的"自己"成员
  async initScaleSelfMember() {
    try {
      // 先检查是否已有"自己"成员
      const res = await cloudRequest.callContainer({
        path: `/api/family-members?user_id=${this.data.userInfo.user_id}`,
        method: 'GET'
      })
      
      const members = Array.isArray(res.data) ? res.data : (Array.isArray(res) ? res : [])
      const hasSelf = members.some(m => m.relationship === 'self')
      
      // 如果没有"自己"成员，创建默认成员
      if (!hasSelf) {
        await cloudRequest.callContainer({
          path: `/api/family-members?user_id=${this.data.userInfo.user_id}`,
          method: 'POST',
          data: {
            name: this.data.userInfo.nickname || this.data.userInfo.phone_number || '我',
            gender: '',
            age: 0,
            height: 0,
            avatar_color: '',
            relationship: 'self'
          }
        })
        console.log('✓ 已自动初始化体脂秤"自己"成员')
      }
    } catch (err) {
      console.error('初始化体脂秤成员失败:', err)
      // 不阻断流程，仅记录错误
    }
  },

  // ==================== 体脂秤蓝牙绑定流程 ====================

  // 关闭权限说明弹窗
  closeScalePermissionDialog() {
    this.setData({ showScalePermissionDialog: false })
    this.cleanupScaleScan()
  },

  // 授权蓝牙并开始扫描
  async authorizeAndScan() {
    this.setData({ showScalePermissionDialog: false })
    wx.showLoading({ title: '启动蓝牙...', mask: true })

    try {
      const app = getApp()
      await app.checkAndInitBluetooth(this.data.userInfo.user_id)
      wx.hideLoading()
      this.startScaleScan()
    } catch (err) {
      wx.hideLoading()
      const errMsg = (err && err.errMsg) || ''
      // 开发者工具不支持蓝牙
      if (errMsg.indexOf('not available') !== -1) {
        wx.showModal({
          title: '当前环境不支持蓝牙',
          content: '微信开发者工具不支持蓝牙功能，请使用真机（手机）扫码预览测试。\n\n已为您进入扫码模式，请用手机扫码后重新添加体脂秤。',
          showCancel: false,
        })
      } else {
        wx.showModal({
          title: '蓝牙启动失败',
          content: '请确保手机蓝牙已开启，并在系统设置中授予蓝牙权限后重试',
          showCancel: false,
        })
      }
    }
  },

  // 开始扫描体脂秤设备
  startScaleScan() {
    const app = getApp()
    app.clearDiscoveredDevices()
    // 绑定阶段抑制蓝牙数据自动跳转称重页
    app.globalData.suppressScaleAutoNavigate = true

    this.setData({
      showScaleScanningDialog: true,
      scaleScanning: true,
      scaleDiscoveredDevices: [],
      scaleScanHelp: '正在扫描体脂秤设备...',
    })

    // 订阅设备发现事件
    this._discoveryUnsub = app.subscribeDeviceDiscovery((devices) => {
      this._filterDiscoveredDevices(devices)
    })

    // 10秒后自动结束扫描
    this._scanTimeout = setTimeout(() => {
      if (!this.data.scaleScanning) return
      const count = this.data.scaleDiscoveredDevices.length
      this.setData({
        scaleScanning: false,
        scaleScanHelp: count > 0
          ? '请选择上方设备完成绑定'
          : '未发现体脂秤设备，请确保设备已开机并靠近手机',
      })
    }, 10000)
  },

  // 过滤已发现的设备（剔除已绑定的）—— 已绑定ID缓存5秒避免高频请求
  _boundDeviceCache: { ids: [], time: 0 },

  async _getBoundDeviceIds(userId) {
    const now = Date.now()
    if (this._boundDeviceCache && (now - this._boundDeviceCache.time < 5000)) {
      return this._boundDeviceCache.ids
    }
    let boundIds = []
    try {
      const boundRes = await cloudRequest.callContainer({
        path: `/api/devices/scale/bound?user_id=${userId}`,
        method: 'GET',
      })
      if (boundRes && boundRes.bound && boundRes.device_id) {
        boundIds = [boundRes.device_id.toLowerCase()]
      }
    } catch (e) {
      console.warn('[绑定] 获取已绑定设备ID失败:', e)
    }
    this._boundDeviceCache = { ids: boundIds, time: now }
    return boundIds
  },

  async _filterDiscoveredDevices(devices) {
    const boundDeviceIds = await this._getBoundDeviceIds(this.data.userInfo.user_id)

    const filtered = devices
      .filter(d => d.deviceId && d.name)
      .map(d => ({
        deviceId: d.deviceId,
        name: d.name,
        RSSI: d.RSSI || -100,
        lastSeen: d.lastSeen || Date.now(),
        isDuplicate: boundDeviceIds.includes(d.deviceId.toLowerCase()),
        signalBars: d.RSSI > -60 ? 4 : d.RSSI > -75 ? 3 : d.RSSI > -85 ? 2 : 1,
      }))
      .sort((a, b) => b.RSSI - a.RSSI)

    this.setData({ scaleDiscoveredDevices: filtered })
  },

  // 选择并绑定体脂秤设备
  async confirmBindScale(e) {
    const deviceId = e.currentTarget.dataset.deviceId
    const deviceName = e.currentTarget.dataset.deviceName
    const isDuplicate = e.currentTarget.dataset.duplicate === 'true'

    // 防重复校验（前端快速拦截）
    if (isDuplicate) {
      wx.showModal({
        title: '无法重复添加',
        content: '该蓝牙设备已在您的账户中绑定，无法重复添加同一蓝牙设备',
        showCancel: false,
      })
      return
    }

    wx.showLoading({ title: '绑定中...', mask: true })

    try {
      await cloudRequest.callContainer({
        path: `/api/devices/scale/bind?user_id=${this.data.userInfo.user_id}`,
        method: 'POST',
        data: {
          device_id: deviceId,
          device_name: deviceName,
        },
      })

      // 初始化"自己"成员
      await this.initScaleSelfMember()

      wx.hideLoading()
      wx.showToast({ title: '绑定成功', icon: 'success', duration: 1500 })

      // 关闭弹窗（保留 suppress 标记 2 秒，避免 BLE 立即跳转称重页覆盖提示）
      this.setData({ showScaleScanningDialog: false })

      // 刷新设备列表
      const app = getApp()
      app.clearDashboardCache()
      await this.loadUserDevices()

      // 2 秒后释放 BLE 自动跳转锁
      setTimeout(() => {
        app.globalData.suppressScaleAutoNavigate = false
      }, 2000)
    } catch (err) {
      wx.hideLoading()
      const errData = err && err.data
      const errMsg = (errData && errData.detail) || '绑定失败，请重试'

      if (errData && err.status === 409) {
        wx.showModal({
          title: '无法重复添加',
          content: errMsg,
          showCancel: false,
        })
      } else {
        wx.showToast({ title: errMsg, icon: 'none' })
      }
    }
  },

  // 关闭扫描弹窗（恢复自动跳转）
  closeScaleScanDialog() {
    this.cleanupScaleScan()
    const app = getApp()
    app.globalData.suppressScaleAutoNavigate = false
    this.setData({ showScaleScanningDialog: false })
  },

  // 清理扫描资源（不恢复自动跳转，由 confirmBindScale 延迟控制）
  cleanupScaleScan() {
    if (this._discoveryUnsub) {
      this._discoveryUnsub()
      this._discoveryUnsub = null
    }
    if (this._scanTimeout) {
      clearTimeout(this._scanTimeout)
      this._scanTimeout = null
    }
  },

  // 阻止事件冒泡
  stopPropagation() {},

  // 跳转到设置页面
  goToConfig() {
    wx.navigateTo({
      url: '/pages/settings/settings'
    })
  },

  // 长按设备卡片 - 删除确认
  onLongPressDevice(e) {
    const deviceKey = e.currentTarget.dataset.deviceKey
    const deviceName = e.currentTarget.dataset.deviceName
    
    // 检查是否共享设备（被分享者无权删除）
    const sharedDevices = this.data.userDevices.filter(d => d.is_shared)
    const isShared = sharedDevices.some(d => d.device_key === deviceKey)
    if (isShared) {
      wx.showToast({ title: '共享设备不支持删除', icon: 'none' })
      return
    }
    
    wx.showModal({
      title: '删除设备',
      content: `确定要删除"${deviceName}"吗？\n删除后需要重新配置账号密码。`,
      confirmText: '删除',
      confirmColor: '#ff4d4f',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          this.deleteDevice(deviceKey, deviceName)
        }
      }
    })
  },

  // 删除设备
  async deleteDevice(deviceKey, deviceName) {
    wx.showLoading({ title: '删除中...' })
    
    try {
      // 解析deviceKey获取platform和device_type
      const parts = deviceKey.split('_')
      const platform = parts[0]
      const deviceType = parts[1]
      
      // 如果是体脂秤，先假删成员（软删除）并停止蓝牙扫描
      if (deviceType === 'scale') {
        await this.softDeleteScaleMembers()
        
        // 停止全局蓝牙定时扫描
        const app = getApp();
        if (app && app.stopPeriodicScan) {
          console.log('[首页] 删除体脂秤，停止蓝牙定时扫描');
          app.stopPeriodicScan();
          app.globalData.bleAdapterInitialized = false;
        }
      }
      
      // 删除设备配置
      const result = await cloudRequest.callContainer({
        path: `/api/devices/${deviceKey}?user_id=${this.data.userInfo.user_id}`,
        method: 'DELETE'
      })
      
      console.log('[首页] 删除结果:', result)

      // 更新本地userInfo的has_configured状态
      const userInfo = wx.getStorageSync('userInfo');
      if (userInfo) {
        userInfo.has_configured = false;
        wx.setStorageSync('userInfo', userInfo);
      }
      
      wx.hideLoading()
      wx.showToast({ title: '删除成功', icon: 'success' })

      // 清除 dashboard 缓存及在途请求，确保删除后立即刷新
      const app = getApp();
      app.clearDashboardCache()
      
      // 刷新设备列表
      await this.loadUserDevices()
    } catch (err) {
      wx.hideLoading()
      console.error('删除设备失败:', err)
      wx.showToast({ 
        title: (err && err.data && err.data.detail) || err.errMsg || '删除失败', 
        icon: 'error',
        duration: 2000
      })
    }
  },

  // 假删体脂秤成员（软删除）
  async softDeleteScaleMembers() {
    try {
      // 获取所有成员
      const membersRes = await cloudRequest.callContainer({
        path: `/api/family-members?user_id=${this.data.userInfo.user_id}`,
        method: 'GET'
      })
      
      const members = Array.isArray(membersRes.data) ? membersRes.data : (Array.isArray(membersRes) ? membersRes : [])
      
      // 逐个软删除（设置is_active=false）
      for (const member of members) {
        await cloudRequest.callContainer({
          path: `/api/family-members/${member.id}?user_id=${this.data.userInfo.user_id}`,
          method: 'PUT',
          data: {
            ...member,
            is_active: false
          }
        }).catch(err => {
          console.warn(`[假删成员] 成员 ${member.name} 删除失败:`, err)
        })
      }
      
      console.log('[假删成员] 已软删除', members.length, '个成员')
    } catch (err) {
      console.error('[假删成员] 失败:', err)
    }
  },

  // 退出登录
  logout() {
    wx.showModal({
      title: '确认退出',
      content: '确定要退出登录吗？',
      success: (res) => {
        if (res.confirm) {
          // 清除设备状态检查定时器
          if (this.deviceStatusTimer) {
            clearInterval(this.deviceStatusTimer);
            this.deviceStatusTimer = null;
          }
          
          // 停止蓝牙定时扫描
          const app = getApp();
          if (app) {
            // 清除 Dashboard 缓存（防止下次登录显示旧数据）
            if (app.clearDashboardCache) {
              app.clearDashboardCache();
            }
            // 停止蓝牙扫描
            if (app.stopPeriodicScan) {
              console.log('[首页] 退出登录，停止蓝牙定时扫描');
              app.stopPeriodicScan();
            }
            // 重置蓝牙状态
            app.globalData.bleAdapterInitialized = false;
            app.globalData.bluetoothInitializing = false;
            app.globalData.latestScaleData = null;
            app.globalData.scaleListeners = [];
          }
          
          // 保存退出的手机号（供登录页本账号密码模式使用）
          const userInfo = this.data.userInfo || wx.getStorageSync('userInfo');
          if (userInfo && userInfo.phone_number) {
            wx.setStorageSync('lastLogoutPhone', userInfo.phone_number);
          }
          
          wx.removeStorageSync('userInfo')
          wx.removeStorageSync('token')
          wx.removeStorageSync('preventSilentLogin')
          this.setData({ 
            userInfo: null,
            userDevices: [],
            petDevices: [],
            healthDevices: []
          })
          
          // 导航到登录页（携带 fromLogout 参数）
          wx.reLaunch({ url: '/pages/login/login?fromLogout=1' })
        }
      }
    })
  },
  
  onHide() {
    // 页面隐藏时清除定时器
    if (this.deviceStatusTimer) {
      clearInterval(this.deviceStatusTimer);
      this.deviceStatusTimer = null;
    }
    
    // 注意：不在这里停止蓝牙扫描
    // 因为用户可能跳转到体脂秤页面，需要持续的蓝牙数据
    // 只在退出登录或删除设备时才停止扫描
  },
  
  onUnload() {
    if (this.deviceStatusTimer) {
      clearInterval(this.deviceStatusTimer);
      this.deviceStatusTimer = null;
    }

    // 清理体脂秤绑定扫描 + 恢复自动跳转
    this.cleanupScaleScan()
    const app = getApp()
    app.globalData.suppressScaleAutoNavigate = false

    // 注意：不在这里停止蓝牙扫描
  },
  
  // 下拉刷新
  async onPullDownRefresh() {
    console.log('[首页] 下拉刷新')
    
    if (!this.data.userInfo) {
      wx.stopPullDownRefresh()
      return
    }
    
    try {
      // 重新加载设备数据（用户主动刷新，BLE 条件启动已集成在 loadUserDevices 内）
      await this.loadUserDevices()
      
      wx.showToast({
        title: '刷新成功',
        icon: 'success',
        duration: 1000
      })
    } catch (err) {
      console.error('刷新失败:', err)
      wx.showToast({
        title: '刷新失败',
        icon: 'none'
      })
    } finally {
      wx.stopPullDownRefresh()
    }
  },
  
  // ==================== 分享功能 ====================

  /**
   * 点击分享按钮 → 显示确认弹窗
   */
  onShareConfirm() {
    if (!this.data.userDevices || this.data.userDevices.length === 0) {
      wx.showToast({ title: '暂无设备可分享', icon: 'none' })
      return
    }
    this.setData({ showShareConfirm: true })
  },

  closeShareConfirm() {
    this.setData({ showShareConfirm: false })
  },

  closeAcceptShare() {
    this.setData({ showAcceptShare: false })
  },

  // 跳转到设置中心 → 分享时效管理
  goToExpirySettings() {
    this.setData({ showShareConfirm: false })
    wx.navigateTo({
      url: '/pages/settings/settings#share-expiry-section',
    })
  },

  /**
   * 确认分享后，调用后端创建分享记录，然后触发微信原生分享
   */
  async doShare() {
    const userInfo = this.data.userInfo || wx.getStorageSync('userInfo')
    if (!userInfo || !userInfo.user_id) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }

    // 收集所有设备的 device_key
    const allKeys = []
    this.data.userDevices.forEach(d => {
      if (d.device_key) allKeys.push(d.device_key)
    })
    if (allKeys.length === 0) {
      wx.showToast({ title: '暂无设备可分享', icon: 'none' })
      return
    }

    try {
      // 调用后端创建分享
      const res = await cloudRequest.callContainer({
        path: '/api/share/create',
        method: 'POST',
        data: {
          from_user_id: userInfo.user_id,
          device_keys: allKeys,
        },
      })

      if (res && res.share_token) {
        // 保存 share_token 供 onShareAppMessage 使用
        this._pendingShareToken = res.share_token
        this.setData({ showShareConfirm: false })
        // 后续通过 open-type="share" 触发 onShareAppMessage
      }
    } catch (err) {
      console.error('[Share] 创建分享失败:', err)
      wx.showToast({ title: '分享创建失败', icon: 'none' })
      this.setData({ showShareConfirm: false })
    }
  },

  /**
   * 转发给朋友 - 生成分享卡片
   */
  onShareAppMessage(res) {
    const userInfo = this.data.userInfo || wx.getStorageSync('userInfo')
    const userName = userInfo ? (userInfo.nickname || '我的智能家') : '好友'
    const token = this._pendingShareToken || ''

    // 分享后清除暂存 token
    this._pendingShareToken = ''

    // 如果是从右上角菜单转发（无 token），尝试创建分享
    if (!token && userInfo) {
      // 静默创建分享：异步发起，但 WeChat 分享卡片参数需同步返回
      // 采用懒加载策略：传参带特殊标记，B 打开时后端实时查询
      return {
        title: `${userName} 邀请您使用智能设备`,
        path: `/pages/index/index?from_uid=${userInfo.user_id || ''}`,
        imageUrl: '',
      }
    }

    return {
      title: `${userName} 邀请您使用智能设备`,
      path: `/pages/index/index?share_token=${token}`,
      imageUrl: '',
    }
  },

  /**
   * 分享到朋友圈
   */
  onShareTimeline() {
    const userInfo = this.data.userInfo || wx.getStorageSync('userInfo')
    const userName = userInfo ? (userInfo.nickname || '我的智能家') : '我的智能家'

    return {
      title: `${userName}的智能设备中心`,
      query: '',
      imageUrl: '',
    }
  },

  /**
   * 处理收到的分享（B 打开分享卡片时调用）
   */
  async handleIncomingShare(shareToken) {
    if (!shareToken) return

    // 等待登录就绪
    const userInfo = this.data.userInfo || wx.getStorageSync('userInfo')
    if (!userInfo || !userInfo.user_id) {
      // 用户未登录，保存 token 到全局，登录后再处理
      app.globalData._pendingShareToken = shareToken
      return
    }

    // 已登录，显示接受分享确认
    try {
      // 查询分享者信息（可选，用于显示名称）
      this.setData({
        showAcceptShare: true,
        shareToken: shareToken,
        shareFromName: '好友',
      })
    } catch (err) {
      console.error('[Share] 加载分享信息失败:', err)
    }
  },

  /**
   * 接受分享 → 调用后端自动配置
   */
  async acceptShare() {
    const userInfo = this.data.userInfo || wx.getStorageSync('userInfo')
    if (!userInfo || !userInfo.user_id) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }

    this.setData({ showAcceptShare: false })
    wx.showLoading({ title: '配置中…', mask: true })

    try {
      const res = await cloudRequest.callContainer({
        path: '/api/share/accept',
        method: 'POST',
        data: {
          share_token: this.data.shareToken,
          to_user_id: userInfo.user_id,
        },
      })

      wx.hideLoading()
      if (res && res.success) {
        wx.showToast({ title: '设备已添加', icon: 'success', duration: 2000 })
        // 重新加载设备列表
        setTimeout(() => this.loadUserDevices(), 1500)
      } else {
        wx.showToast({ title: res?.message || '配置失败', icon: 'none' })
      }
    } catch (err) {
      wx.hideLoading()
      const msg = err?.data?.detail || err?.errMsg || '接受分享失败'
      wx.showToast({ title: msg, icon: 'none' })
    }
  },
})
