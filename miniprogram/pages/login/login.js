const cloudRequest = require('../../utils/cloud_request.js');

Page({
  data: {
    account: '',
    password: '',
    loading: false,
    errorMsg: '',
    // 静默登录失败后显示表单
    isSilentFailed: false,
  },

  onLoad() {
    // 进入页面后立即尝试静默登录
    this.attemptSilentLogin();
  },

  /**
   * 尝试静默免密登录
   * 调用 wx.login 获取 code → 调用后端 /api/auth/silent-login
   */
  async attemptSilentLogin() {
    try {
      // 1. 获取微信临时凭证
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject,
        });
      });

      if (!loginRes.code) {
        this.showBindForm('获取微信凭证失败，请重试');
        return;
      }

      // 2. 调用静默登录接口
      const res = await new Promise((resolve, reject) => {
        cloudRequest.callContainer({
          path: '/api/auth/silent-login',
          method: 'POST',
          data: { code: loginRes.code },
          success: resolve,
          fail: reject,
        });
      });

      // 3. 静默登录成功 → 保存 Token 并跳转首页
      this.onLoginSuccess(res);
    } catch (err) {
      const statusCode = err?.statusCode;
      const detail = err?.data?.detail || err?.errMsg || '';

      if (statusCode === 401 || (detail && detail.code === 'UNBOUND')) {
        // 未绑定 → 显示账密表单
        this.showBindForm('请登录以完成绑定');
      } else if (statusCode === 400) {
        this.showBindForm('登录凭证无效，请在当前微信中打开');
      } else {
        this.showBindForm('网络异常，请检查后重试');
      }
    }
  },

  /**
   * 账密绑定登录
   */
  async handleBindLogin() {
    const { account, password } = this.data;

    // 前端基础校验
    if (account.length !== 11) {
      this.setData({ errorMsg: '请输入正确的11位手机号' });
      return;
    }
    if (password.length < 6) {
      this.setData({ errorMsg: '密码长度不能少于6位' });
      return;
    }

    this.setData({ loading: true, errorMsg: '' });

    try {
      // 1. 重新获取 code
      const loginRes = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject,
        });
      });

      if (!loginRes.code) {
        this.setData({ loading: false, errorMsg: '获取微信凭证失败，请重试' });
        return;
      }

      // 2. 调用绑定接口
      const res = await new Promise((resolve, reject) => {
        cloudRequest.callContainer({
          path: '/api/auth/bind',
          method: 'POST',
          data: {
            account: account,
            password: password,
            code: loginRes.code,
          },
          success: resolve,
          fail: reject,
        });
      });

      // 3. 登录成功
      this.onLoginSuccess(res);
    } catch (err) {
      const statusCode = err?.statusCode;
      const detail = err?.data?.detail || '';
      const errMsg = err?.errMsg || err?.message || JSON.stringify(err);

      console.error('[Login] bind 请求失败详情:', { statusCode, detail, errMsg });

      if (statusCode === 401) {
        this.setData({ errorMsg: '手机号或密码错误', loading: false });
      } else if (statusCode === 400) {
        this.setData({ errorMsg: typeof detail === 'string' ? detail : '参数有误', loading: false });
      } else if (errMsg && errMsg.includes('CONNECTION_RESET')) {
        this.setData({ errorMsg: '无法连接服务器，请确保后端已启动', loading: false });
      } else if (errMsg && errMsg.includes('timeout')) {
        this.setData({ errorMsg: '请求超时，请检查服务器是否正常', loading: false });
      } else {
        this.setData({ errorMsg: `请求失败: ${errMsg}`, loading: false });
      }
    }
  },

  /**
   * 登录成功后的统一处理
   */
  onLoginSuccess(res) {
    const { token, user_id, phone_number, openid, nickname } = res;

    // 保存到本地存储
    wx.setStorageSync('token', token);
    wx.setStorageSync('userInfo', {
      user_id: user_id,
      phone_number: phone_number,
      openid: openid,
      nickname: nickname || `用户${phone_number.slice(-4)}`,
    });

    console.log('[Login] ✅ 登录成功，user_id:', user_id);

    // 跳转到首页（关闭当前页，防止返回）
    wx.reLaunch({ url: '/pages/index/index' });
  },

  /**
   * 静默登录失败 → 显示账密登录表单
   */
  showBindForm(msg) {
    console.log('[Login] 静默登录失败:', msg);
    this.setData({
      isSilentFailed: true,
      errorMsg: msg || '',
    });
  },

  onAccountInput(e) {
    this.setData({ account: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },
});
