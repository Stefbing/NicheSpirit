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

      const app = getApp();
      if (!app.globalData.cachedDashboardData && this.data.devicesLoaded) {
        console.log('[首页] 🔄 检测到缓存已清除，重新加载设备数据');
        this.loadUserDevices();
      } else if (this.data.devicesLoaded && app.globalData.cachedDashboardData) {
        const cachedData = app.globalData.cachedDashboardData;
        const hasXiaomiConfig = cachedData.xiaomi_config || false;
        const hasScaleStats = cachedData.scale_stats && typeof cachedData.scale_stats === 'object' && 'today_count' in cachedData.scale_stats;

        if (hasXiaomiConfig && !hasScaleStats) {
          console.warn('[首页] ⚠️ 缓存数据不完整，强制刷新');
          app.globalData.cachedDashboardData = null;
          app.globalData.dashboardCacheTime = 0;
          this.loadUserDevices();
        }
      }
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
      
      // 处理 CloudPets 喂食机（仅当有实际配置数据时显示）
      const hasCloudPetsConfig = dashboardData.cloudpets_servings && 
                                 Object.keys(dashboardData.cloudpets_servings).length > 0
      if (hasCloudPetsConfig) {
        const feederDevice = {
          device_key: 'cloudpets_cloudpets',
          device_type: 'feeder',
          device_name: 'cloudpets',
          display_name: '喂食机',
          platform: 'cloudpets',
          status: 'active'
        }
        // 解析今日投喂次数 - 使用 result 字段
        const servingsData = dashboardData.cloudpets_servings
        if (servingsData && typeof servingsData === 'object') {
          feederDevice.today_servings = servingsData.result || 0
        } else if (typeof servingsData === 'number') {
          feederDevice.today_servings = servingsData
        } else {
          feederDevice.today_servings = 0
        }
        
        // 计算计划剩余数量 - 只计算当前时间之后的启用计划
        const plans = dashboardData.cloudpets_plans || []
        if (Array.isArray(plans)) {
          // 获取当前时间 HH:mm
          const now = new Date()
          const currentMinutes = now.getHours() * 60 + now.getMinutes()
          
          let remaining = 0
          plans.forEach(p => {
            // 确保 plan 结构中有 time 且 enabled
            if (p.time && p.enabled !== false && p.enabled !== 0 && p.enabled !== '0') {
              const [h, m] = p.time.split(':').map(Number)
              const planMinutes = h * 60 + m
              if (planMinutes > currentMinutes) {
                remaining++
              }
            }
          })
          
          feederDevice.remaining_plans = remaining
        } else {
          feederDevice.remaining_plans = 0
        }
        
        petDevices.push(feederDevice)
        userDevices.push(feederDevice)
      }
      
      // 处理 PetKit 猫厕所
      const petkitDevices = dashboardData.petkit_devices || []
      if (petkitDevices.length > 0) {
        const litterboxDevice = {
          device_key: 'petkit_petkit',
          device_type: 'litterbox',
          device_name: 'petkit',
          display_name: '猫厕所',
          platform: 'petkit',
          status: 'active'
        }
        const litterboxStats = dashboardData.litterbox_stats || {}
        
        // 查找第一个猫厕所设备（与Web端一致）
        const litterboxPetkitDevice = petkitDevices.find(d => {
          if (!d || !d.type) return false
          const name = d.name || ''
          return ['T3', 'T4', 'T4 Pura MAX', 'T5'].includes(d.type) || name.includes('MAX')
        })
        
        if (litterboxPetkitDevice) {
          // 优先使用缓存的统计数据（与Web端一致）
          let stats = {}
          if (litterboxStats[litterboxPetkitDevice.id]) {
            stats = litterboxStats[litterboxPetkitDevice.id]
          } else if (litterboxPetkitDevice.state_summary) {
            stats = litterboxPetkitDevice.state_summary
          }
          
          // 今日如厕次数 - 只使用 today_visits，不使用 used_times（累计值）
          litterboxDevice.today_visits = stats.today_visits !== undefined ? stats.today_visits : 0
          
          // 猫砂余量百分比
          litterboxDevice.sand_level = stats.sand_percent || 0
        } else {
          litterboxDevice.today_visits = 0
          litterboxDevice.sand_level = 0
        }
        
        petDevices.push(litterboxDevice)
        userDevices.push(litterboxDevice)
      }
      
      // 处理小米体脂秤（检查是否有配置）
      const hasXiaomiConfig = dashboardData.xiaomi_config || false
      if (hasXiaomiConfig) {
        const scaleDevice = {
          device_key: 'xiaomi_xiaomi',
          device_type: 'scale',
          device_name: 'xiaomi',
          display_name: 'MIBFS', // 首页显示短名称
          platform: 'xiaomi',
          status: 'active',
          online: false, // 默认离线，后续通过蓝牙状态更新
          today_measurements: 0, // 今日测量次数
          latest_body_fat: null // 最新体脂率
        }
        
        // 从 dashboardData 中获取体脂秤统计数据
        const scaleStats = dashboardData.scale_stats
        console.log('[首页] 📊 体脂秤统计数据:', scaleStats)
        
        // 【修复】只有当 scale_stats 存在且包含有效字段时才使用
        if (scaleStats && typeof scaleStats === 'object' && 'today_count' in scaleStats) {
          scaleDevice.today_measurements = scaleStats.today_count || 0
          scaleDevice.latest_body_fat = scaleStats.latest_body_fat !== undefined ? scaleStats.latest_body_fat : null
          console.log('[首页] ✅ 体脂秤数据 - 今日测量:', scaleDevice.today_measurements, '体脂率:', scaleDevice.latest_body_fat)
        } else {
          console.warn('[首页] ⚠️ scale_stats 数据缺失或格式错误，使用默认值')
        }
        
        healthDevices.push(scaleDevice)
        userDevices.push(scaleDevice)
      }
      
      this.setData({
        userDevices,
        petDevices,
        healthDevices,
        devicesLoaded: true  // 标记已加载
      })
    } catch (err) {
      console.error('加载设备列表失败:', err)
      wx.showToast({ 
        title: '加载失败，请重试', 
        icon: 'none' 
      })
    } finally {
      // 无论成功或失败，都重置加载状态
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
    
    if (!deviceAccount || !devicePassword) {
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
          account: deviceAccount,
          password: devicePassword
        }
      })
      
      // 如果是体脂秤，自动初始化“自己”成员
      if (selectedDeviceType === 'scale') {
        await this.initScaleSelfMember()
        
        // 体脂秤添加成功后，立即初始化蓝牙
        console.log('[首页] 体脂秤添加成功，立即初始化蓝牙')
        app.checkAndInitBluetooth()
      }
      
      wx.hideLoading()
      wx.showToast({ title: '添加成功', icon: 'success' })
      
      this.closeDeviceConfigModal()
      // 添加设备后刷新设备列表
      await this.loadUserDevices()
    } catch (err) {
      wx.hideLoading()
      console.error('添加设备失败:', err)
      wx.showToast({ title: '添加失败，请重试', icon: 'none' })
    }
  },

  // 初始化体脂秤的“自己”成员
  async initScaleSelfMember() {
    try {
      // 先检查是否已有“自己”成员
      const res = await cloudRequest.callContainer({
        path: `/api/family-members?user_id=${this.data.userInfo.user_id}`,
        method: 'GET'
      })
      
      const members = Array.isArray(res.data) ? res.data : (Array.isArray(res) ? res : [])
      const hasSelf = members.some(m => m.relationship === 'self')
      
      // 如果没有“自己”成员，创建默认成员
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
        console.log('✓ 已自动初始化体脂秤“自己”成员')
      }
    } catch (err) {
      console.error('初始化体脂秤成员失败:', err)
      // 不阻断流程，仅记录错误
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
    
    wx.showModal({
      title: '删除设备',
      content: `确定要删除“${deviceName}”吗？\n删除后需要重新配置账号密码。`,
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
      await cloudRequest.callContainer({
        path: `/api/devices/${deviceKey}?user_id=${this.data.userInfo.user_id}`,
        method: 'DELETE'
      })
      
      // 更新本地userInfo的has_configured状态
      const userInfo = wx.getStorageSync('userInfo');
      if (userInfo) {
        userInfo.has_configured = false;
        wx.setStorageSync('userInfo', userInfo);
      }
      
      wx.hideLoading()
      wx.showToast({ title: '删除成功', icon: 'success' })
      
      // 刷新设备列表
      await this.loadUserDevices()
    } catch (err) {
      wx.hideLoading()
      console.error('删除设备失败:', err)
      wx.showToast({ 
        title: err.errMsg || '删除失败', 
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
    // 页面卸载时清除定时器
    if (this.deviceStatusTimer) {
      clearInterval(this.deviceStatusTimer);
      this.deviceStatusTimer = null;
    }
    
    // 注意：不在这里停止蓝牙扫描
    // 保持扫描运行，供其他页面使用
  },
  
  // 下拉刷新
  async onPullDownRefresh() {
    console.log('[首页] 下拉刷新')
    
    if (!this.data.userInfo) {
      wx.stopPullDownRefresh()
      return
    }
    
    try {
      // 重新加载设备数据（用户主动刷新）
      await this.loadUserDevices()
      
      // 如果网络恢复，尝试初始化蓝牙
      const app = getApp()
      if (app && !app.globalData.bleAdapterInitialized && !app.globalData.bluetoothInitializing) {
        console.log('[首页] 🔄 下拉刷新成功，尝试初始化蓝牙')
        app.checkAndInitBluetooth(this.data.userInfo.user_id)
      }
      
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
