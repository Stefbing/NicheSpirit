const cloudRequest = require('../../utils/cloud_request.js');

/**
 * 【修复】记录每个外部接口调用的请求参数与返回数据
 */
function logApiCall(apiName, path, method, data, response, isError) {
  const prefix = isError ? '❌' : '✅';
  const truncated = response !== undefined && response !== null
    ? (typeof response === 'object' ? JSON.stringify(response).slice(0, 500) : String(response).slice(0, 500))
    : '(empty)';
  console.log(`[API] ${prefix} ${apiName} | ${method} ${path} | req=${JSON.stringify(data || {})} | res=${truncated}`);
}

Page({
  data: {
    plans: [],
    loading: true,
    actionLoading: false,
    feedAmount: 1, // 投喂份量
    todayServings: 0, // 今日已喂次数
    deviceName: '云宠喂食器', // 设备名称
    // 【新增】设备状态
    feederStatus: null,
    showAddPlanDialog: false, // 显示添加计划弹窗
    planTime: '', // 计划时间
    planAmount: 1, // 计划份量
    planAmountIndex: 0, // 计划份量索引
    _onLoadCalled: false // 【修复】防重标记，阻止 onLoad + onShow 重复调用
  },

  onLoad() {
    this.setData({ _onLoadCalled: true });
    this.fetchPlans();
    this.fetchTodayServings();
    this.fetchStatus();
  },

  onShow() {
    // 【修复】首次打开页面时，onLoad 和 onShow 连续触发，
    // 如果 onLoad 已调用过 fetchTodayServings，onShow 不再重复请求
    if (!this.data._onLoadCalled) {
      this.fetchTodayServings();
    }
    // 隐藏右上角"..."菜单的分享功能，统一使用页内分享入口
    wx.hideShareMenu({ menus: ['share', 'shareTimeline'] });
  },

  // 设置投喂份量
  setFeedAmount(e) {
    const amount = parseInt(e.currentTarget.dataset.amount);
    this.setData({ feedAmount: amount });
  },

  // 获取今日喂食次数
  fetchTodayServings() {
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) return;
    
    const path = `/api/cloudpets/servings_today?user_id=${userInfo.user_id}`;
    console.log('[API] 📤 GET', path);
    cloudRequest.callContainer({
      path,
      method: 'GET',
      success: res => {
        logApiCall('fetchTodayServings', path, 'GET', null, res);
        if (res && typeof res === 'object' && res.result !== undefined) {
          this.setData({ todayServings: res.result });
        } else if (typeof res === 'number') {
          this.setData({ todayServings: res });
        }
      },
      fail: err => {
        logApiCall('fetchTodayServings', path, 'GET', null, err, true);
        console.error('获取今日喂食次数失败:', err);
      }
    });
  },

  feedOne() {
    const { feedAmount } = this.data;
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    
    this.setData({ actionLoading: true });
    wx.showLoading({
      title: '正在投喂...'
    });

    const path = `/api/cloudpets/feed?user_id=${userInfo.user_id}`;
    const data = { amount: feedAmount };
    console.log('[API] 📤 POST', path, data);
    cloudRequest.callContainer({
      path,
      method: 'POST',
      data,
      success: res => {
        logApiCall('feedOne', path, 'POST', data, res);
        wx.hideLoading();
        this.setData({ actionLoading: false });
        wx.showToast({
          title: `已投喂 ${feedAmount} 份`,
          icon: 'success'
        });
        // 刷新今日喂食次数
        this.fetchTodayServings();
        // 【修复】使用统一的数据更新通知接口，确保返回首页时能显示最新数据
        const app = getApp();
        app.notifyDataUpdate('feeder', ['cloudpets_servings']);
      },
      fail: err => {
        wx.hideLoading();
        this.setData({ actionLoading: false });
        console.error('投喂请求失败:', err);
        
        // 检查是否是 503 服务未初始化
        if (err.statusCode === 503) {
          wx.showModal({
            title: '服务未配置',
            content: 'CloudPets 喂食器功能需要配置账号密码\n\n请在首页完成初始配置',
            showCancel: true,
            cancelText: '取消',
            confirmText: '去配置',
            success: (res) => {
              if (res.confirm) {
                // 跳转到首页进行配置
                wx.reLaunch({
                  url: '/pages/index/index'
                });
              }
            }
          });
        } else {
          wx.showToast({
            title: '网络错误',
            icon: 'error'
          });
        }
      }
    });
  },

  fetchPlans() {
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) return;
    
    this.setData({ loading: true });
    const path = `/api/cloudpets/plans?user_id=${userInfo.user_id}`;
    console.log('[API] 📤 GET', path);
    cloudRequest.callContainer({
      path,
      success: res => {
        logApiCall('fetchPlans', path, 'GET', null, res);
        // callContainer 已返回业务数据
        this.setData({
          plans: res || [],
          loading: false
        });
      },
      fail: err => {
        console.error('获取喂食计划失败:', err);
        this.setData({ loading: false });
        
        // 检查是否是 503 服务未初始化
        if (err.statusCode === 503) {
          wx.showModal({
            title: '服务未配置',
            content: 'CloudPets 喂食器功能需要配置账号密码\n\n请在首页完成初始配置',
            showCancel: true,
            cancelText: '取消',
            confirmText: '去配置',
            success: (res) => {
              if (res.confirm) {
                // 跳转到首页进行配置
                wx.reLaunch({
                  url: '/pages/index/index'
                });
              }
            }
          });
        } else {
          wx.showToast({
            title: '加载失败',
            icon: 'error'
          });
        }
      }
    });
  },

  togglePlan(e) {
    const index = e.currentTarget.dataset.index;
    const plans = this.data.plans.slice();
    const plan = {
      ...plans[index],
      enabled: e.detail.value
    };

    plans[index] = plan;
    this.setData({ plans });

    const path = `/api/cloudpets/plans/${plan.id}?user_id=${wx.getStorageSync('userInfo').user_id}`;
    console.log('[API] 📤 PUT', path, plan);
    cloudRequest.callContainer({
      path,
      method: 'PUT',
      data: plan,
      success: () => {
        logApiCall('togglePlan', path, 'PUT', plan, 'ok');
        wx.showToast({
          title: e.detail.value ? '已启用' : '已禁用',
          icon: 'success'
        });
        // 【优化】使用统一的数据更新通知接口
        const app = getApp();
        app.notifyDataUpdate('feeder', ['cloudpets_plans', 'cloudpets_servings']);
      },
      fail: err => {
        console.error('更新喂食计划失败:', err);
        plans[index] = {
          ...plan,
          enabled: !e.detail.value
        };
        this.setData({ plans });
        
        // 检查是否是 503 服务未初始化
        if (err.statusCode === 503) {
          wx.showModal({
            title: '服务未配置',
            content: 'CloudPets 喂食器功能需要配置账号密码\n\n请在首页完成初始配置',
            showCancel: true,
            cancelText: '取消',
            confirmText: '去配置',
            success: (res) => {
              if (res.confirm) {
                // 跳转到首页进行配置
                wx.reLaunch({
                  url: '/pages/index/index'
                });
              }
            }
          });
        } else {
          wx.showToast({
            title: '操作失败',
            icon: 'error'
          });
        }
      }
    });
  },

  savePlans() {
    wx.showToast({
      title: '计划已实时保存',
      icon: 'success'
    });
  },

  // 显示添加计划弹窗
  showAddPlanModal() {
    this.setData({
      showAddPlanDialog: true,
      planTime: '',
      planAmount: 1,
      planAmountIndex: 0
    });
  },

  // 关闭添加计划弹窗
  closeAddPlanModal() {
    this.setData({ showAddPlanDialog: false });
  },

  // 阻止事件冒泡
  stopPropagation() {},

  // 选择计划时间
  onPlanTimeChange(e) {
    this.setData({ planTime: e.detail.value });
  },

  // 选择计划份量
  onPlanAmountChange(e) {
    const index = parseInt(e.detail.value);
    const amounts = [1, 2, 3, 4, 5];
    this.setData({
      planAmountIndex: index,
      planAmount: amounts[index]
    });
  },

  // 提交新计划
  submitPlan() {
    const { planTime, planAmount } = this.data;

    if (!planTime) {
      wx.showToast({ title: '请选择时间', icon: 'none' });
      return;
    }

    wx.showLoading({ title: '添加中...' });

    const newPlan = {
      time: planTime,
      amount: planAmount,
      enabled: true,
      weekdays: [1, 2, 3, 4, 5, 6, 7] // 默认每天
    };

    const path = `/api/cloudpets/plans?user_id=${wx.getStorageSync('userInfo').user_id}`;
    console.log('[API] 📤 POST', path, newPlan);
    cloudRequest.callContainer({
      path,
      method: 'POST',
      data: newPlan,
      success: () => {
        logApiCall('submitPlan', path, 'POST', newPlan, 'ok');
        wx.hideLoading();
        wx.showToast({ title: '添加成功', icon: 'success' });
        this.closeAddPlanModal();
        this.fetchPlans(); // 刷新计划列表
        // 【优化】使用统一的数据更新通知接口
        const app = getApp();
        app.notifyDataUpdate('feeder', ['cloudpets_plans']);
      },
      fail: err => {
        wx.hideLoading();
        console.error('添加计划失败:', err);
        
        if (err.statusCode === 503) {
          wx.showModal({
            title: '服务未配置',
            content: 'CloudPets 喂食器功能需要配置账号密码\n\n请在首页完成初始配置',
            showCancel: true,
            cancelText: '取消',
            confirmText: '去配置',
            success: (res) => {
              if (res.confirm) {
                wx.reLaunch({ url: '/pages/index/index' });
              }
            }
          });
        } else {
          wx.showToast({ title: '添加失败', icon: 'error' });
        }
      }
    });
  },

  // 删除计划
  deletePlan(e) {
    const planId = e.currentTarget.dataset.id;

    wx.showModal({
      title: '确认删除',
      content: '确定要删除这个喂食计划吗？',
      success: (res) => {
        if (res.confirm) {
          wx.showLoading({ title: '删除中...' });

          const path = `/api/cloudpets/plans/${planId}?user_id=${wx.getStorageSync('userInfo').user_id}`;
          console.log('[API] 📤 DELETE', path);
          cloudRequest.callContainer({
            path,
            method: 'DELETE',
            success: () => {
              logApiCall('deletePlan', path, 'DELETE', null, 'ok');
              wx.hideLoading();
              wx.showToast({ title: '删除成功', icon: 'success' });
              this.fetchPlans(); // 刷新计划列表
              // 【优化】使用统一的数据更新通知接口
              const app = getApp();
              app.notifyDataUpdate('feeder', ['cloudpets_plans']);
            },
            fail: err => {
              wx.hideLoading();
              console.error('删除计划失败:', err);
              
              if (err.statusCode === 503) {
                wx.showModal({
                  title: '服务未配置',
                  content: 'CloudPets 喂食器功能需要配置账号密码\n\n请在首页完成初始配置',
                  showCancel: true,
                  cancelText: '取消',
                  confirmText: '去配置',
                  success: (res) => {
                    if (res.confirm) {
                      wx.reLaunch({ url: '/pages/index/index' });
                    }
                  }
                });
              } else {
                wx.showToast({ title: '删除失败', icon: 'error' });
              }
            }
          });
        }
      }
    });
  },
  
  // 下拉刷新
  async onPullDownRefresh() {
    console.log('[喂食器] 下拉刷新')
    
    try {
      await this.fetchPlans()
      await this.fetchStatus()
      
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
  
  /**
   * 【新增】获取喂食器联网状态
   * 下拉刷新时一并调用，补充设备实时状态信息
   */
  fetchStatus() {
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) return;

    const path = `/api/cloudpets/feeder/status?user_id=${userInfo.user_id}`;
    console.log('[API] 📤 GET', path);
    cloudRequest.callContainer({
      path,
      method: 'GET',
      success: res => {
        logApiCall('fetchStatus', path, 'GET', null, res);
        this.setData({ feederStatus: res });
      },
      fail: err => {
        logApiCall('fetchStatus', path, 'GET', null, err, true);
        console.warn('[Feeder] 获取设备状态失败（非致命）:', err);
      }
    });
  },

  /**
   * 转发给朋友 - 通过页内分享按钮触发，仅分享当前设备
   */
  onShareAppMessage(res) {
    const token = this._pendingShareToken || '';
    this._pendingShareToken = '';
    return {
      title: this._shareTitle || '我的智能喂食器',
      path: token ? `/pages/index/index?share_token=${token}` : '/pages/feeder/feeder',
      imageUrl: ''
    }
  },

  /**
   * 单设备分享：点击设备卡片上的分享按钮
   */
  onDeviceShare() {
    const userInfo = wx.getStorageSync('userInfo');
    if (!userInfo || !userInfo.user_id) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      return;
    }

    wx.showLoading({ title: '准备分享...', mask: true });

    // 调用后端创建单设备分享记录
    cloudRequest.callContainer({
      path: '/api/share/create',
      method: 'POST',
      data: {
        device_keys: ['cloudpets_cloudpets'],
      },
      success: (res) => {
        wx.hideLoading();
        if (res && res.share_token) {
          this._pendingShareToken = res.share_token;
          this._shareTitle = '分享智能喂食器';
          // open-type="share" 将自动触发 onShareAppMessage
        } else {
          wx.showToast({ title: '分享创建失败', icon: 'none' });
        }
      },
      fail: (err) => {
        wx.hideLoading();
        console.error('[Feeder] 创建分享失败:', err);
        wx.showToast({ title: '分享创建失败', icon: 'none' });
      }
    });
  }
});
