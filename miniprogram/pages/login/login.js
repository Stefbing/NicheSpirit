const cloudRequest = require('../../utils/cloud_request.js');

/**
 * 手机号脱敏：保留前3后4，中间4位变星号
 * @param {string} phone - 11位手机号
 * @returns {string} 脱敏后字符串
 */
function maskPhone(phone) {
  if (!phone || phone.length < 7) return phone || '';
  return phone.slice(0, 3) + '****' + phone.slice(-4);
}

/**
 * 登录模式枚举
 * loading      - 静默登录中（显示加载）
 * select       - 登录方式选择（显示两个按钮）
 * own_password - 本账号密码登录（仅需输入密码）
 * bind         - 其他手机号绑定（需输入手机号+密码）
 */
const MODE = {
  LOADING: 'loading',
  SELECT: 'select',
  OWN_PASSWORD: 'own_password',
  BIND: 'bind',
};

Page({
  data: {
    // 当前模式
    loginMode: MODE.LOADING,

    // 本账号密码登录
    ownAccount: '',          // 原始手机号（用于提交）
    ownAccountDisplay: '',   // 界面显示值（自动填充时脱敏）
    isPhoneAutoFilled: false,// 是否系统自动填充
    ownPassword: '',

    // 其他手机号绑定登录
    account: '',
    password: '',

    // 通用状态
    loading: false,
    errorMsg: '',

    // 上次退出的手机号
    lastLogoutPhone: '',
    lastLogoutPhoneDisplay: '',

    // 是否允许下拉触发静默登录（仅 select 模式且有权限时）
    canPullDownSilent: false,
  },

  onLoad(query) {
    const isFromLogout = query.fromLogout === '1';
    const forcedMode = query.mode;
    const lastPhone = wx.getStorageSync('lastLogoutPhone') || '';

    if (isFromLogout) {
      // 来自退出登录
      this.handlePostLogout(lastPhone);
    } else if (forcedMode === 'own_password') {
      // app.js 检测到 preventSilentLogin，强制进入本账号密码模式
      this.startOwnPasswordMode(lastPhone);
    } else {
      // 正常打开：先尝试静默登录
      this.attemptSilentLogin();
    }
  },

  // ==================== 模式切换 ====================

  /**
   * 退出登录后的处理：检查 preventSilentLogin 标志决定模式
   */
  handlePostLogout(lastPhone) {
    const preventSilent = wx.getStorageSync('preventSilentLogin');
    if (preventSilent) {
      // 用户之前选了"本账号密码登录" → 直接进入该模式
      this.startOwnPasswordMode(lastPhone);
    } else {
      // 未禁止静默登录 → 显示登录方式选择
      this.showLoginSelect('', lastPhone);
    }
  },

  /**
   * 尝试静默免密登录
   */
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

      // 静默登录成功
      this.onLoginSuccess(res);
    } catch (err) {
      const sc = err?.statusCode;
      const detail = err?.data?.detail || '';

      if (sc === 401 || (detail && detail.code === 'UNBOUND')) {
        this.showLoginSelect('请选择登录方式');
      } else if (sc === 400) {
        this.showLoginSelect('登录凭证失效，请下拉重试');
      } else {
        this.showLoginSelect('网络异常，请下拉重试');
      }
    }
  },

  /**
   * 显示登录方式选择
   * @param {string} msg - 提示信息
   * @param {string} lastPhone - 上次手机号
   * @param {boolean} force - 是否强制显示选择页（跳过 preventSilentLogin 拦截）
   */
  showLoginSelect(msg, lastPhone, force) {
    // 非强制时检查 preventSilentLogin → 拦截并回到本账号密码模式
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
      // 未禁止静默登录时才显示下拉/按钮入口
      canPullDownSilent: !preventSilent,
    });
  },

  /**
   * 选择：本账号密码登录
   * 传递原始手机号（若有），由 startOwnPasswordMode 决定是否脱敏
   */
  onSelectOwnPassword() {
    const phone = this.data.ownAccount || this.data.lastLogoutPhone;
    this.startOwnPasswordMode(phone);
  },

  /**
   * 进入本账号密码登录模式
   * @param {string} phone - 手机号（自动填充时传入原始号码，手动进入时传空）
   */
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
    });
  },

  /**
   * 选择：其他手机号登录
   * 用户主动选择使用其他账号 → 清除 preventSilentLogin 标志，允许后续静默登录
   */
  onSelectOtherPhone() {
    wx.setStorageSync('preventSilentLogin', false);
    this.setData({
      loginMode: MODE.BIND,
      account: '',
      password: '',
      loading: false,
      errorMsg: '',
      canPullDownSilent: false,
    });
  },

  /**
   * 返回选择页（用户主动操作 → 强制展示 + 清除 preventSilentLogin，
   * 允许后续下拉静默登录）
   */
  onBackToSelect() {
    wx.setStorageSync('preventSilentLogin', false);
    this.showLoginSelect('', '', true);
  },

  // ==================== 提交登录 ====================

  /**
   * 本账号密码登录提交
   * 登录后设置 preventSilentLogin = true，禁止后续静默登录
   */
  async handleOwnPasswordLogin() {
    const { ownAccount, ownPassword } = this.data;
    if (!ownAccount || ownAccount.length !== 11) {
      this.setData({ errorMsg: '手机号不正确' });
      return;
    }
    if (!ownPassword || ownPassword.length < 6) {
      this.setData({ errorMsg: '密码至少6位' });
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

      // ✅ 本账号密码登录成功 → 禁止后续 openid 静默登录
      wx.setStorageSync('preventSilentLogin', true);
      this.onLoginSuccess(res);
    } catch (err) {
      this._handleLoginError(err);
    }
  },

  /**
   * 其他手机号绑定提交
   * 登录后清除 preventSilentLogin，允许使用 openid 静默登录
   */
  async handleBindLogin() {
    const { account, password } = this.data;
    if (account.length !== 11) {
      this.setData({ errorMsg: '请输入正确的11位手机号' });
      return;
    }
    if (password.length < 6) {
      this.setData({ errorMsg: '密码至少6位' });
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

      // ✅ 其他手机号绑定成功 → 清除禁止标志，允许后续静默登录
      wx.setStorageSync('preventSilentLogin', false);
      wx.setStorageSync('lastLogoutPhone', '');
      this.onLoginSuccess(res);
    } catch (err) {
      this._handleLoginError(err);
    }
  },

  /**
   * 统一错误处理
   */
  _handleLoginError(err) {
    const sc = err?.statusCode;
    const detail = err?.data?.detail || '';
    const errMsg = err?.errMsg || err?.message || JSON.stringify(err);

    let msg = '';
    if (sc === 401) {
      msg = '手机号或密码错误';
    } else if (sc === 400) {
      msg = typeof detail === 'string' ? detail : '参数有误';
    } else if (errMsg.includes('CONNECTION_RESET')) {
      msg = '无法连接服务器，请确保后端已启动';
    } else if (errMsg.includes('timeout')) {
      msg = '请求超时，请检查服务器是否正常';
    } else {
      msg = `请求失败: ${errMsg}`;
    }
    this.setData({ errorMsg: msg, loading: false });
  },

  /**
   * 登录成功统一处理
   */
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
    // 用全屏 loading 遮罩覆盖页面切换白屏
    wx.showLoading({ title: '正在进入…', mask: true });
    wx.reLaunch({ url: '/pages/index/index' });
  },

  // ==================== 下拉刷新 ====================

  /**
   * 手动触发静默登录（select 模式下的按钮入口）
   */
  onSilentLogin() {
    this._performSilentLoginWithPullDown();
  },

  /**
   * 下拉刷新：
   * - 处于 select 模式且未禁止静默登录 → 触发 openid 静默登录
   * - 其他情况 → 仅停止刷新
   */
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

  /**
   * 下拉触发的静默登录（内部包装，处理 stopPullDownRefresh）
   */
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

  /**
   * 本账号输入框获得焦点
   * 自动填充态 → 清除脱敏值，切换为手动输入
   */
  onOwnAccountFocus() {
    if (this.data.isPhoneAutoFilled) {
      this.setData({
        ownAccount: '',
        ownAccountDisplay: '',
        isPhoneAutoFilled: false,
      });
    }
  },

  /**
   * 本账号输入
   * 自动填充清除后进入手动输入态，正常显示明文
   */
  onOwnAccountInput(e) {
    const val = e.detail.value;
    this.setData({
      ownAccount: val,
      ownAccountDisplay: val,
    });
  },

  onOwnPasswordInput(e) { this.setData({ ownPassword: e.detail.value }); },
  onAccountInput(e) { this.setData({ account: e.detail.value }); },
  onPasswordInput(e) { this.setData({ password: e.detail.value }); },
});
