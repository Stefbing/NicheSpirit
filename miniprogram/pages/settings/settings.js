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
    // 【修复】账号编辑弹窗
    showEditAccountModal: false,
    editAccountPlatform: '',
    editAccountLabel: '',
    editAccountValue: '',
    editPasswordValue: '',
    editIsSaving: false,
    // 分享管理
    shareList: [],
    shareListLoading: false,
    // 修改时效弹窗
    showExpiryModal: false,
    editShareId: null,
    editShareName: '',
    editExpiryHours: 24,
    editExpiryInput: '24',
  },

  onShow() {
    this.loadUserInfo();
    this.loadDeviceConfigStatus();
    this.loadShareManagement();
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
   * 【修复】从 dashboardData.device_platforms 提取 is_shared 标识
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
      const app = getApp();
      const dashboardData = await app.fetchDashboardData(userId);

      // 【修复】从 device_platforms 构建平台归属映射
      const platformInfoMap = {};
      (dashboardData.device_platforms || []).forEach(p => {
        platformInfoMap[p.platform] = {
          is_shared: p.is_shared || false,
          is_complete: p.is_complete || false,
        };
      });

      const deviceList = platforms.map(p => {
        const info = platformInfoMap[p.platform] || {};
        let configured = false;

        if (p.platform === 'xiaomi') {
          configured = info.is_complete || dashboardData.xiaomi_config === true;
        } else if (p.platform === 'cloudpets') {
          const servings = dashboardData.cloudpets_servings;
          configured = info.is_complete || (servings && Object.keys(servings).length > 0);
        } else if (p.platform === 'petkit') {
          const devices = dashboardData.petkit_devices;
          configured = info.is_complete || (devices && devices.length > 0);
        }

        return {
          ...p,
          configured,
          maskedAccount: '',
          is_shared: info.is_shared || false,
        };
      });

      this.setData({ deviceList });
    } catch (err) {
      console.error('[Settings] 加载设备配置状态失败:', err);
      this.setData({
        deviceList: platforms.map(p => ({ ...p, configured: false, maskedAccount: '', is_shared: false })),
      });
    }
  },

  // ====== 分享时效管理 ======

  /**
   * 加载分享管理列表
   */
  async loadShareManagement() {
    const userId = this.data.userId;
    if (!userId) return;

    this.setData({ shareListLoading: true });
    try {
      const res = await cloudRequest.callContainer({
        path: `/api/share/manage-list?user_id=${userId}`,
        method: 'GET',
      });

      const shares = (res && res.shares) || [];
      // 格式化显示
      const shareList = shares.map(s => {
        let statusText = '';
        let statusClass = '';
        let remainingText = '';

        if (s.status === 'pending') {
          statusText = '待接受';
          statusClass = 'status-pending';
          remainingText = `剩余 ${s.remaining_hours}h`;
        } else if (s.status === 'accepted') {
          statusText = '使用中';
          statusClass = 'status-active';
          if (s.remaining_hours > 0) {
            remainingText = `剩余 ${s.remaining_hours}h`;
          } else {
            remainingText = '即将过期';
          }
        } else {
          statusText = '已撤销';
          statusClass = 'status-revoked';
          remainingText = '已失效';
        }

        const deviceNames = (s.device_keys || []).map(k => {
          const parts = k.split('_');
          const plat = parts[0];
          const platMap = { petkit: '小佩猫厕所', cloudpets: '喂食机', xiaomi: '体脂秤' };
          return platMap[plat] || plat;
        }).join('、');

        return {
          ...s,
          statusText,
          statusClass,
          remainingText,
          deviceNames,
          createdTime: this._formatTime(s.created_at),
        };
      });

      this.setData({ shareList, shareListLoading: false });
    } catch (err) {
      console.error('[Settings] 加载分享列表失败:', err);
      this.setData({ shareListLoading: false });
    }
  },

  _formatTime(ms) {
    if (!ms) return '';
    const d = new Date(ms);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  },

  /**
   * 打开修改时效弹窗
   */
  onEditExpiry(e) {
    const shareId = e.currentTarget.dataset.shareId;
    const share = this.data.shareList.find(s => s.id === shareId);
    if (!share) return;

    this.setData({
      showExpiryModal: true,
      editShareId: shareId,
      editShareName: share.to_user_nickname || '好友',
      editExpiryHours: Math.max(1, Math.ceil(share.remaining_hours || 24)),
      editExpiryInput: String(Math.max(1, Math.ceil(share.remaining_hours || 24))),
    });
  },

  closeExpiryModal() {
    this.setData({ showExpiryModal: false });
  },

  onExpiryInput(e) {
    this.setData({ editExpiryInput: e.detail.value });
  },

  /**
   * 确认修改时效
   */
  async confirmUpdateExpiry() {
    const hours = parseInt(this.data.editExpiryInput, 10);
    if (isNaN(hours) || hours < 1) {
      wx.showToast({ title: '有效时长至少 1 小时', icon: 'none' });
      return;
    }
    if (hours > 720) {
      wx.showToast({ title: '有效时长最多 720 小时（30天）', icon: 'none' });
      return;
    }

    wx.showLoading({ title: '更新中…', mask: true });
    try {
      const userInfo = wx.getStorageSync('userInfo');
      const token = wx.getStorageSync('token');
      const res = await cloudRequest.callContainer({
        path: '/api/share/update-expiry',
        method: 'POST',
        data: {
          share_id: this.data.editShareId,
          user_id: userInfo.user_id,
          expire_hours: hours,
        },
        header: token ? { Authorization: `Bearer ${token}` } : {},
      });

      wx.hideLoading();
      this.closeExpiryModal();

      if (res && res.success) {
        wx.showToast({ title: res.message || '时效已更新', icon: 'success', duration: 2000 });
        this.loadShareManagement();
      } else {
        wx.showToast({ title: res?.message || '更新失败', icon: 'none' });
      }
    } catch (err) {
      wx.hideLoading();
      this.closeExpiryModal();
      const msg = err?.data?.detail || err?.errMsg || '更新失败';
      wx.showToast({ title: msg, icon: 'none' });
    }
  },

  /**
   * 撤销分享
   */
  onRevokeShare(e) {
    const shareId = e.currentTarget.dataset.shareId;
    const share = this.data.shareList.find(s => s.id === shareId);
    const name = share ? (share.to_user_nickname || '好友') : '该好友';

    wx.showModal({
      title: '撤销分享',
      content: `确定撤销对"${name}"的设备分享吗？撤销后对方将无法继续使用共享设备。`,
      confirmText: '确认撤销',
      confirmColor: '#ff4d4f',
      cancelText: '取消',
      success: async (res) => {
        if (res.confirm) {
          await this.doRevokeShare(shareId);
        }
      },
    });
  },

  async doRevokeShare(shareId) {
    wx.showLoading({ title: '撤销中…', mask: true });
    try {
      const userInfo = wx.getStorageSync('userInfo');
      const token = wx.getStorageSync('token');
      await cloudRequest.callContainer({
        path: `/api/share/revoke?share_id=${shareId}&user_id=${userInfo.user_id}`,
        method: 'POST',
        header: token ? { Authorization: `Bearer ${token}` } : {},
      });
      wx.hideLoading();
      wx.showToast({ title: '已撤销', icon: 'success', duration: 1500 });
      this.loadShareManagement();
    } catch (err) {
      wx.hideLoading();
      wx.showToast({ title: '撤销失败', icon: 'none' });
    }
  },

  // ====== 功能区一事件 ======

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

  onViewPrivacy() {
    wx.navigateTo({ url: '/pages/privacy/privacy' });
  },

  onDeleteAccount() {
    wx.showModal({
      title: '⚠️ 危险操作',
      content: '注销账号将永久删除您的全部数据（账号信息、设备配置、体重记录、家庭成员等），此操作不可恢复！\n\n确定要继续吗？',
      confirmText: '确认注销',
      confirmColor: '#ff4d4f',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
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

  // ====== 设备账号密码维护（自购设备 vs 共享设备） ======

  /**
   * 点击设备配置卡片的处理入口
   * - 自购设备（is_shared=false）：弹出编辑弹窗
   * - 共享设备（is_shared=true）：提示不可编辑
   */
  onEditDeviceConfig(e) {
    const platform = e.currentTarget.dataset.platform;
    const device = this.data.deviceList.find(d => d.platform === platform);
    if (!device) return;

    if (device.is_shared) {
      wx.showToast({ title: '共享设备无需维护账号密码', icon: 'none' });
      return;
    }

    // 自购设备 → 弹出账号密码编辑弹窗
    this.setData({
      showEditAccountModal: true,
      editAccountPlatform: platform,
      editAccountLabel: device.label,
      editAccountValue: device.maskedAccount || '',
      editPasswordValue: '',
      editIsSaving: false,
    });
  },

  onEditAccountInput(e) {
    this.setData({ editAccountValue: e.detail.value });
  },

  onEditPasswordInput(e) {
    this.setData({ editPasswordValue: e.detail.value });
  },

  closeEditAccountModal() {
    this.setData({ showEditAccountModal: false });
  },

  /**
   * 保存设备账号密码（直接调用已认证的 POST /api/devices/add）
   * 不跳转首页，在设置页内完成维护
   */
  async confirmEditAccount() {
    const { editAccountPlatform, editAccountValue, editPasswordValue, userId } = this.data;

    if (!editAccountValue || !editPasswordValue) {
      wx.showToast({ title: '请填写账号和密码', icon: 'none' });
      return;
    }

    this.setData({ editIsSaving: true });
    wx.showLoading({ title: '保存中…', mask: true });

    try {
      const isScale = editAccountPlatform === 'xiaomi';
      const path = isScale
        ? `/api/devices/scale/bind?user_id=${userId}`
        : '/api/devices/add';

      await cloudRequest.callContainer({
        path,
        method: 'POST',
        data: isScale
          ? { device_id: editAccountValue, device_name: editPasswordValue || 'MIBFS' }
          : {
              device_type: editAccountPlatform,
              platform: editAccountPlatform,
              account: editAccountValue,
              password: editPasswordValue,
            },
      });

      wx.hideLoading();
      wx.showToast({ title: '账号更新成功', icon: 'success', duration: 1500 });
      this.closeEditAccountModal();

      // 刷新设备状态
      this.loadDeviceConfigStatus();
    } catch (err) {
      wx.hideLoading();
      const msg = err?.data?.detail || err?.errMsg || '保存失败，请检查账号密码后重试';
      wx.showToast({ title: msg, icon: 'none' });
    } finally {
      this.setData({ editIsSaving: false });
    }
  },
});
