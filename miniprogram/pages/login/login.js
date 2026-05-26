const cloudRequest = require('../../utils/cloud_request.js');

/** 隐私协议存储键名 */
const PRIVACY_CONSENT_KEY = 'privacy_consent';

function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || '';
  return phone.slice(0, 3) + '****' + phone.slice(-4);
}

/**
 * 模式枚举
 * loading       - 静默登录中
 * phone_only    - 首次登录：仅展示「手机号注册/登录」
 * select        - 退出后重入：展示「本机登录」+「其他方式登录」
 * own_password  - 本账号密码输入
 * bind          - 其他手机号绑定输入
 */
const MODE = {
  LOADING: 'loading',
  PHONE_ONLY: 'phone_only',
  SELECT: 'select',
  OWN_PASSWORD: 'own_password',
  BIND: 'bind',
};

Page({
  data: {
    loginMode: MODE.LOADING,

    // 首次手机号注册/登录
    phoneOnly: '',
    phoneOnlyPassword: '',

    // 本账号密码登录（退出后重入）
    ownAccount: '',
    ownAccountDisplay: '',
    isPhoneAutoFilled: false,
    ownPassword: '',

    // 其他手机号绑定登录
    account: '',
    password: '',

    loading: false,
    errorMsg: '',

    lastLogoutPhone: '',
    lastLogoutPhoneDisplay: '',

    // 隐私协议弹窗
    showPrivacyDialog: false,
    agreed: false,

    // 底部行内协议校验
    inlineAgreed: false,
    privacyError: false,
    privacyShaking: false,
  },

  onLoad(query) {
    const isFromLogout = query.fromLogout === '1';
    const forcedMode = query.mode;
    const lastPhone = wx.getStorageSync('lastLogoutPhone') || '';

    // 1. 检查隐私协议是否已授权
    if (!wx.getStorageSync(PRIVACY_CONSENT_KEY)) {
      this.setData({
        loginMode: MODE.LOADING,
        showPrivacyDialog: true,
        agreed: false,
      });
      return;
    }

    // 2. 已授权 → 区分首次登录 vs 退出后重入
    if (isFromLogout) {
      this.handlePostLogout(lastPhone);
    } else if (forcedMode === 'own_password') {
      this.startOwnPasswordMode(lastPhone);
    } else if (forcedMode === 'phone_only') {
      this.startPhoneOnlyMode();
    } else if (lastPhone) {
      // 有退出记录 → 展示双选项
      this.showLoginSelect('', lastPhone);
    } else {
      // 无退出记录 → 首次登录 → 尝试静默登录，失败后进入 phone_only
      this.attemptSilentLogin();
    }
  },

  // ==================== 隐私协议弹窗 ====================

  showPrivacyDialog() {
    this.setData({ showPrivacyDialog: true, agreed: false });
  },

  toggleAgree() {
    this.setData({ agreed: !this.data.agreed });
  },

  viewAgreement() {
    wx.navigateTo({ url: '/pages/privacy/privacy?tab=service' });
  },

  viewPolicy() {
    wx.navigateTo({ url: '/pages/privacy/privacy?tab=privacy' });
  },

  confirmPrivacy() {
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意协议', icon: 'none' });
      return;
    }
    wx.setStorageSync(PRIVACY_CONSENT_KEY, Date.now());
    this.setData({ showPrivacyDialog: false });

    // 同意后恢复正常流程
    const lastPhone = wx.getStorageSync('lastLogoutPhone') || '';
    if (lastPhone) {
      this.showLoginSelect('', lastPhone);
    } else {
      this.startPhoneOnlyMode();
    }
  },

  rejectPrivacy() {
    wx.showModal({
      title: '提示',
      content: '您需要同意《冷门器灵服务协议》和《隐私政策》后才能使用本小程序。',
      confirmText: '我知道了',
      showCancel: false,
      success: () => { wx.navigateBack(); },
    });
  },

  // ==================== 底部行内协议校验 ====================

  toggleInlineAgree() {
    this.setData({
      inlineAgreed: !this.data.inlineAgreed,
      privacyError: false,
      privacyShaking: false,
    });
  },

  /**
   * 统一的登录前协议校验
   * @returns {boolean} true=通过
   */
  checkPrivacyBeforeLogin() {
    if (!this.data.inlineAgreed) {
      this.setData({
        privacyError: true,
        privacyShaking: true,
      });
      // 抖动动画结束后移除 shake 类
      setTimeout(() => {
        this.setData({ privacyShaking: false });
      }, 600);
      return false;
    }
    return true;
  },

  // ==================== 模式切换 ====================

  handlePostLogout(lastPhone) {
    const preventSilent = wx.getStorageSync('preventSilentLogin');
    if (preventSilent) {
      this.startOwnPasswordMode(lastPhone);
    } else {
      this.showLoginSelect('', lastPhone);
    }
  },

  /** 首次登录：仅展示手机号注册/登录 */
  startPhoneOnlyMode() {
    this.setData({
      loginMode: MODE.PHONE_ONLY,
      phoneOnly: '',
      phoneOnlyPassword: '',
      loading: false,
      errorMsg: '',
      inlineAgreed: false,
      privacyError: false,
    });
  },

  async attemptSilentLogin() {
    this.setData({ loginMode: MODE.LOADING, errorMsg: '', loading: false });
    try {
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      if (!loginRes.code) throw new Error('获取微信凭证失败');

      const res = await cloudRequest.callContainer({
        path: '/api/auth/silent-login',
        method: 'POST',
        data: { code: loginRes.code },
      });
      this.onLoginSuccess(res);
    } catch (err) {
      const sc = err?.statusCode;
      const detail = err?.data?.detail || '';
      if (sc === 401 || (detail && detail.code === 'UNBOUND')) {
        // 未绑定 → 进入首次手机号注册/登录
        this.startPhoneOnlyMode();
      } else {
        this.setData({ errorMsg: '网络异常，请重试' });
        setTimeout(() => this.startPhoneOnlyMode(), 500);
      }
    }
  },

  showLoginSelect(msg, lastPhone, force) {
    if (!force) {
      const preventSilent = wx.getStorageSync('preventSilentLogin');
      if (preventSilent) {
        this.startOwnPasswordMode(lastPhone || wx.getStorageSync('lastLogoutPhone') || '');
        return;
      }
    }
    const phone = lastPhone || wx.getStorageSync('lastLogoutPhone') || '';
    const preventSilent = wx.getStorageSync('preventSilentLogin');
    this.setData({
      loginMode: MODE.SELECT,
      errorMsg: msg || '',
      lastLogoutPhone: phone,
      lastLogoutPhoneDisplay: maskPhone(phone),
      ownAccount: phone,
      canPullDownSilent: !preventSilent,
      inlineAgreed: false,
      privacyError: false,
    });
  },

  onSelectOwnPassword() {
    const phone = this.data.ownAccount || this.data.lastLogoutPhone;
    this.startOwnPasswordMode(phone);
  },

  startOwnPasswordMode(phone) {
    const isAuto = !!phone && phone.length >= 7;
    this.setData({
      loginMode: MODE.OWN_PASSWORD,
      ownAccount: phone,
      ownAccountDisplay: isAuto ? maskPhone(phone) : phone,
      isPhoneAutoFilled: isAuto,
      ownPassword: '',
      loading: false,
      errorMsg: '',
      canPullDownSilent: false,
      inlineAgreed: this.data.inlineAgreed,
      privacyError: false,
    });
  },

  onSelectOtherPhone() {
    wx.setStorageSync('preventSilentLogin', false);
    this.setData({
      loginMode: MODE.BIND,
      account: '',
      password: '',
      loading: false,
      errorMsg: '',
      canPullDownSilent: false,
      inlineAgreed: false,
      privacyError: false,
    });
  },

  onBackToSelect() {
    wx.setStorageSync('preventSilentLogin', false);
    this.showLoginSelect('', '', true);
  },

  // ==================== 提交登录 ====================

  /** 首次手机号注册/登录 */
  async handlePhoneOnlyLogin() {
    if (!this.checkPrivacyBeforeLogin()) return;
    const { phoneOnly, phoneOnlyPassword } = this.data;
    if (!phoneOnly || phoneOnly.length !== 11) {
      this.setData({ errorMsg: '请输入正确的11位手机号' });
      return;
    }
    if (!phoneOnlyPassword || phoneOnlyPassword.length < 4) {
      this.setData({ errorMsg: '密码至少4位' });
      return;
    }
    this.setData({ loading: true, errorMsg: '' });
    try {
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      if (!loginRes.code) throw new Error('获取微信凭证失败');

      const res = await cloudRequest.callContainer({
        path: '/api/auth/bind',
        method: 'POST',
        data: { account: phoneOnly, password: phoneOnlyPassword, code: loginRes.code },
      });
      wx.setStorageSync('preventSilentLogin', true);
      this.onLoginSuccess(res);
    } catch (err) {
      this._handleLoginError(err);
    }
  },

  async handleOwnPasswordLogin() {
    if (!this.checkPrivacyBeforeLogin()) return;
    const { ownAccount, ownPassword } = this.data;
    if (!ownAccount || ownAccount.length !== 11) {
      this.setData({ errorMsg: '手机号不正确' });
      return;
    }
    if (!ownPassword || ownPassword.length < 4) {
      this.setData({ errorMsg: '密码至少4位' });
      return;
    }
    this.setData({ loading: true, errorMsg: '' });
    try {
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      if (!loginRes.code) throw new Error('获取微信凭证失败');

      const res = await cloudRequest.callContainer({
        path: '/api/auth/bind',
        method: 'POST',
        data: { account: ownAccount, password: ownPassword, code: loginRes.code },
      });
      wx.setStorageSync('preventSilentLogin', true);
      this.onLoginSuccess(res);
    } catch (err) {
      this._handleLoginError(err);
    }
  },

  async handleBindLogin() {
    if (!this.checkPrivacyBeforeLogin()) return;
    const { account, password } = this.data;
    if (account.length !== 11) {
      this.setData({ errorMsg: '请输入正确的11位手机号' });
      return;
    }
    if (password.length < 4) {
      this.setData({ errorMsg: '密码至少4位' });
      return;
    }
    this.setData({ loading: true, errorMsg: '' });
    try {
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      if (!loginRes.code) throw new Error('获取微信凭证失败');

      const res = await cloudRequest.callContainer({
        path: '/api/auth/bind',
        method: 'POST',
        data: { account, password, code: loginRes.code },
      });
      wx.setStorageSync('preventSilentLogin', false);
      wx.setStorageSync('lastLogoutPhone', '');
      this.onLoginSuccess(res);
    } catch (err) {
      this._handleLoginError(err);
    }
  },

  _handleLoginError(err) {
    const sc = err?.statusCode;
    const detail = err?.data?.detail || '';
    const errMsg = err?.errMsg || err?.message || JSON.stringify(err);
    let msg = '';
    if (sc === 401) {
      msg = '手机号或密码错误';
    } else if (sc === 400) {
      msg = typeof detail === 'string' ? detail : '参数有误';
    } else if (errMsg.includes('CONNECTION_RESET') || errMsg.includes('network')) {
      msg = '无法连接服务器，请确保后端已启动';
    } else if (errMsg.includes('timeout')) {
      msg = '请求超时，请检查服务器是否正常';
    } else {
      msg = `请求失败: ${errMsg}`;
    }
    this.setData({ errorMsg: msg, loading: false });
  },

  onLoginSuccess(res) {
    const { token, user_id, phone_number, openid, nickname } = res;
    wx.setStorageSync('token', token);
    wx.setStorageSync('userInfo', {
      user_id,
      phone_number,
      openid,
      nickname: nickname || `用户${phone_number.slice(-4)}`,
    });
    console.log('[Login] ✅ 登录成功，user_id:', user_id);
    wx.showLoading({ title: '正在进入…', mask: true });
    wx.reLaunch({ url: '/pages/index/index' });
  },

  // ==================== 静默登录 ====================

  onSilentLogin() {
    this._performSilentLoginWithPullDown();
  },

  onPullDownRefresh() {
    if (this.data.loginMode === MODE.SELECT) {
      const preventSilent = wx.getStorageSync('preventSilentLogin');
      if (!preventSilent) {
        this._performSilentLoginWithPullDown();
        return;
      }
    }
    wx.stopPullDownRefresh();
  },

  async _performSilentLoginWithPullDown() {
    try {
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({ success: resolve, fail: reject });
      });
      if (!loginRes.code) throw new Error('获取微信凭证失败');

      const res = await cloudRequest.callContainer({
        path: '/api/auth/silent-login',
        method: 'POST',
        data: { code: loginRes.code },
      });
      wx.hideLoading();
      wx.stopPullDownRefresh();
      this.onLoginSuccess(res);
    } catch (err) {
      wx.hideLoading();
      wx.stopPullDownRefresh();
      const sc = err?.statusCode;
      const detail = err?.data?.detail || '';
      if (sc === 401 || (detail && detail.code === 'UNBOUND')) {
        this.setData({ errorMsg: '静默登录失败，请选择其他方式登录' });
      } else {
        this.setData({ errorMsg: '网络异常，请下拉重试' });
      }
    }
  },

  // ==================== 输入事件 ====================

  onPhoneOnlyInput(e) { this.setData({ phoneOnly: e.detail.value }); },
  onPhoneOnlyPasswordInput(e) { this.setData({ phoneOnlyPassword: e.detail.value }); },

  onOwnAccountFocus() {
    if (this.data.isPhoneAutoFilled) {
      this.setData({ ownAccount: '', ownAccountDisplay: '', isPhoneAutoFilled: false });
    }
  },
  onOwnAccountInput(e) {
    const val = e.detail.value;
    this.setData({ ownAccount: val, ownAccountDisplay: val });
  },
  onOwnPasswordInput(e) { this.setData({ ownPassword: e.detail.value }); },
  onAccountInput(e) { this.setData({ account: e.detail.value }); },
  onPasswordInput(e) { this.setData({ password: e.detail.value }); },

  stopPropagation() {},
});
