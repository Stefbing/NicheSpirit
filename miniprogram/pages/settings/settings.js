const cloudRequest = require('../../utils/cloud_request.js');

/**
 * 手机号脱敏
 */
function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || '';
  return phone.slice(0, 3) + '****' + phone.slice(-4);
}

Page({
  data: {
    nickName: '',
    phoneDisplay: '',
    avatarChar: '',
    userId: null,
    nickname: '',
    deviceList: [],
  },

  onShow() {
    this.loadUserInfo();
    this.loadDeviceConfigStatus();
  },

  /**
   * 加载用户基本信息
   */
  loadUserInfo() {
    const userInfo = wx.getStorageSync('userInfo') || {};
    const phone = userInfo.phone_number || '';
    const nickname = userInfo.nickname || '用户';

    this.setData({
      nickName: nickname,
      phoneDisplay: maskPhone(phone),
      avatarChar: nickname.charAt(0),
      userId: userInfo.user_id,
      nickname: nickname,
    });
  },

  /**
   * 加载各平台设备配置状态
   */
  async loadDeviceConfigStatus() {
    const userId = this.data.userId;
    if (!userId) return;

    const platforms = [
      { platform: 'cloudpets', label: '云宠喂食机', icon: '🍖', iconBg: 'rgba(251,191,36,0.15)' },
      { platform: 'petkit', label: '小佩猫厕所', icon: '🚽', iconBg: 'rgba(59,130,246,0.15)' },
      { platform: 'xiaomi', label: '小米体脂秤', icon: '⚖️', iconBg: 'rgba(16,185,129,0.15)' },
    ];

    try {
      // 获取 dashboard 数据判断各平台配置情况
      const app = getApp();
      const dashboardData = await app.fetchDashboardData(userId);

      const deviceList = platforms.map(p => {
        let configured = false;
        let maskedAccount = '';

        if (p.platform === 'xiaomi') {
          // 体脂秤为本地设备，通过后端xiaomi_config判断是否已配置
          configured = dashboardData.xiaomi_config === true;
        } else if (p.platform === 'cloudpets') {
          const servings = dashboardData.cloudpets_servings;
          configured = servings && Object.keys(servings).length > 0;
        } else if (p.platform === 'petkit') {
          const devices = dashboardData.petkit_devices;
          configured = devices && devices.length > 0;
        }

        return { ...p, configured, maskedAccount };
      });

      this.setData({ deviceList });
    } catch (err) {
      console.error('[Settings] 加载设备配置状态失败:', err);
      // 默认全为未配置
      this.setData({
        deviceList: platforms.map(p => ({ ...p, configured: false, maskedAccount: '' })),
      });
    }
  },

  // ====== 功能区一事件 ======

  /**
   * 修改密码
   */
  onChangePassword() {
    wx.showModal({
      title: '修改密码',
      editable: true,
      placeholderText: '请输入新密码（至少4位）',
      success: async (res) => {
        if (res.confirm && res.content) {
          const newPassword = res.content.trim();
          if (newPassword.length < 4) {
            wx.showToast({ title: '密码至少4位', icon: 'none' });
            return;
          }
          await this.doChangePassword(newPassword);
        }
      },
    });
  },

  async doChangePassword(newPassword) {
    wx.showLoading({ title: '修改中...' });
    try {
      const userInfo = wx.getStorageSync('userInfo');
      const token = wx.getStorageSync('token');
      await cloudRequest.callContainer({
        path: '/api/auth/change-password',
        method: 'POST',
        data: {
          user_id: userInfo.user_id,
          password: newPassword,
        },
        header: token ? { Authorization: `Bearer ${token}` } : {},
      });
      wx.hideLoading();
      wx.showToast({ title: '密码修改成功', icon: 'success' });
    } catch (err) {
      wx.hideLoading();
      wx.showToast({ title: '修改失败: ' + (err.errMsg || ''), icon: 'none' });
    }
  },

  /**
   * 查看隐私协议
   */
  onViewPrivacy() {
    wx.navigateTo({ url: '/pages/privacy/privacy' });
  },

  /**
   * 注销账号
   */
  onDeleteAccount() {
    wx.showModal({
      title: '⚠️ 危险操作',
      content: '注销账号将永久删除您的全部数据（账号信息、设备配置、体重记录、家庭成员等），此操作不可恢复！\n\n确定要继续吗？',
      confirmText: '确认注销',
      confirmColor: '#ff4d4f',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          // 二次确认
          wx.showModal({
            title: '再次确认',
            content: '请再次确认：您确定要注销账号并删除所有数据吗？',
            confirmText: '确定注销',
            confirmColor: '#ff4d4f',
            cancelText: '暂不注销',
            success: async (res2) => {
              if (res2.confirm) {
                await this.doDeleteAccount();
              }
            },
          });
        }
      },
    });
  },

  async doDeleteAccount() {
    wx.showLoading({ title: '正在注销...', mask: true });
    try {
      const userInfo = wx.getStorageSync('userInfo');
      const token = wx.getStorageSync('token');
      await cloudRequest.callContainer({
        path: '/api/auth/delete-account',
        method: 'POST',
        data: { user_id: userInfo.user_id },
        header: token ? { Authorization: `Bearer ${token}` } : {},
      });

      // 清除本地存储
      wx.removeStorageSync('userInfo');
      wx.removeStorageSync('token');
      wx.removeStorageSync('preventSilentLogin');
      wx.removeStorageSync('lastLogoutPhone');

      wx.hideLoading();
      wx.showToast({ title: '账号已注销', icon: 'success', duration: 2000 });

      setTimeout(() => {
        wx.reLaunch({ url: '/pages/login/login' });
      }, 2000);
    } catch (err) {
      wx.hideLoading();
      wx.showToast({ title: '注销失败: ' + (err.errMsg || ''), icon: 'none', duration: 3000 });
    }
  },

  // ====== 功能区二事件 ======

  /**
   * 点击编辑设备配置 → 跳转至原来 config 页
   */
  onEditDeviceConfig(e) {
    const platform = e.currentTarget.dataset.platform;
    wx.navigateTo({ url: `/pages/config/config?platform=${platform}` });
  },
});
