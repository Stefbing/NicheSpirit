const cloudRequest = require('../../utils/cloud_request.js');

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

    // 底部行内协议校验
    inlineAgreed: false,
    privacyError: false,
    privacyShaking: false,

    // 协议/政策详情蒙层弹窗
    showAgreementModal: false,
    agreementAnimatingOut: false,
    agreementTab: 'service',   // 'service' | 'privacy'

    // 《用户服务协议》章节数据
    serviceArticles: [
      { title: '', text: '欢迎使用「冷门器灵」（以下简称"本小程序"）。本小程序由开发者（以下简称"我们"）运营。请您（以下简称"用户"或"您"）在开始使用本小程序前仔细阅读本协议的全部内容。如您不同意本协议的任何条款，请立即停止使用本小程序。' },
      { title: '一、协议范围', text: '1.1 本协议是您与我们之间关于您使用本小程序所订立的协议。\n1.2 本协议内容同时包括《隐私保护政策》及可能发布的各类规则，所有内容均为本协议不可分割的一部分，具有同等法律效力。' },
      { title: '二、账号注册与使用', text: '2.1 本小程序采用手机号+密码的注册登录方式，同时支持微信OpenID静默登录。\n2.2 您注册时需提供真实、准确的手机号码。如信息发生变更，请及时更新。\n2.3 您应妥善保管账号和密码，因账号密码泄露导致的损失由您自行承担。\n2.4 每个微信账号仅允许绑定一个本小程序账号，绑定后可用于静默免密登录。' },
      { title: '三、用户信息收集与使用', text: '3.1 我们仅收集实现功能所必需的信息，包括：\n    （1）手机号码：用于注册和登录验证；\n    （2）微信OpenID：用于静默免密登录和设备关联；\n    （3）设备账号密码（云宠、小佩、小米）：用于连接和管理您的智能设备；\n    （4）体重、体脂等健康数据：用于体脂秤数据记录和趋势分析；\n    （5）家庭成员信息：用于体脂计算和健康管理。\n3.2 我们不会收集您的聊天记录、位置信息（蓝牙扫描仅用于连接附近的体脂秤设备）。\n3.3 详细信息收集与使用规则请查阅《隐私保护政策》。' },
      { title: '四、用户权利', text: '4.1 您有权随时查看、修改您的个人信息。\n4.2 您有权要求注销账号，注销后我们将删除您的全部数据，包括但不限于账号信息、设备配置、体重记录、家庭成员信息等。\n4.3 您有权撤回已同意的隐私授权，撤回不影响撤回前基于授权已进行的数据处理活动。' },
      { title: '五、免责声明', text: '5.1 本小程序提供的智能设备控制功能依赖于第三方平台（云宠、小佩、小米）的API服务，我们不对第三方服务的可用性和稳定性承担责任。\n5.2 体脂秤测量数据仅供参考，不构成任何医疗建议。如有健康问题，请咨询专业医疗机构。' },
      { title: '六、协议变更', text: '6.1 我们有权根据法律法规变化或业务需要修改本协议，修改后的协议将通过弹窗或页面公告等方式通知您。\n6.2 如您不同意修改后的协议，请停止使用本小程序；继续使用视为同意修改后的协议。' },
      { title: '七、联系我们', text: '如您对本协议有任何疑问，或需要行使相关权利，可通过以下方式联系我们：\n    • 小程序内设置页 → 账号注销功能\n    • 通过微信小程序客服功能留言' },
    ],

    // 《隐私保护政策》章节数据
    privacyArticles: [
      { title: '', text: '我们深知个人信息对您的重要性，并会尽全力保护您的个人信息安全。本隐私保护政策说明了我们如何收集、使用、存储和保护您的个人信息。' },
      { title: '一、信息收集范围与类型', text: '我们在您使用本小程序的过程中收集以下信息：\n\n1. 注册登录信息\n    • 手机号码：用于账号注册和登录验证\n    • 微信OpenID：通过微信code2session接口获取，用于静默登录\n    • 密码：加密存储（bcrypt哈希），用于账密登录验证\n\n2. 设备配置信息\n    • 云宠（CloudPets）账号密码：用于连接和管理喂食机\n    • 小佩（PetKit）账号密码：用于连接和管理猫厕所\n    • 小米（Xiaomi）账号密码：用于连接体脂秤和推送数据至小米云\n    以上设备凭证均采用AES加密存储于服务器数据库\n\n3. 健康数据\n    • 体重（kg）、阻抗值\n    • BMI、体脂率、肌肉量、水分率、蛋白质率\n    • 内脏脂肪等级、骨量、基础代谢率\n    • 测量时间戳\n\n4. 家庭成员信息\n    • 姓名、性别、年龄、身高、关系（如：自己/配偶/子女）' },
      { title: '二、信息使用目的与方式', text: '我们收集的信息用于以下目的：\n\n1. 服务提供与维护\n    • 验证用户身份，提供账号登录功能\n    • 连接并控制您授权的智能设备（喂食机、猫厕所、体脂秤）\n    • 记录和展示体脂秤测量数据及历史趋势\n\n2. 数据同步\n    • 将体重数据推送至小米云，实现数据同步\n\n3. 服务优化\n    • 分析使用数据以改进产品功能和用户体验\n\n我们不会将您的个人信息用于上述目的之外的任何用途。' },
      { title: '三、信息存储与保护', text: '1. 存储位置：您的个人数据存储于中国大陆地区的服务器（微信云托管/腾讯云）。\n2. 存储期限：我们仅在提供服务所必需的期限内保留您的数据。账号注销后，我们将删除所有相关数据。\n3. 安全措施：\n    • 密码采用bcrypt哈希加密\n    • 设备凭证采用AES加密存储\n    • 通信使用HTTPS加密传输\n    • 定期安全审计和漏洞修复' },
      { title: '四、信息共享与公开', text: '1. 我们不会向第三方出售您的个人信息。\n2. 在以下情况下，我们可能共享您的信息：\n    • 获得您的明确同意\n    • 法律法规要求\n    • 保护我们或他人的合法权益\n3. 设备分享功能：当您主动分享设备给其他用户时，设备凭证将以加密方式共享给被分享用户。' },
      { title: '五、您的权利', text: '1. 访问权：您可查看已绑定的手机号、设备配置和健康数据。\n2. 更正权：您可修改个人信息和设备配置。\n3. 删除权：您可通过设置页的"账号注销"功能删除您的全部数据。\n4. 撤回同意权：您可在微信小程序设置中关闭授权。\n5. 注销账号：在设置页 → 账号信息维护与注销区 → 点击"注销账号"，确认后我们将删除所有数据。' },
      { title: '六、未成年人保护', text: '本小程序主要面向成年人。如您是未满18周岁的未成年人，请在监护人陪同下使用，并由监护人对您的使用行为负责。' },
      { title: '七、隐私政策更新', text: '我们可能会不时更新本隐私保护政策。更新后，我们将在您下次登录时提示您查看并重新确认同意。' },
      { title: '八、联系方式', text: '如您对本隐私保护政策有任何疑问或需行使权利，请通过小程序内设置页联系我们。' },
    ],
  },

  onLoad(query) {
    const isFromLogout = query.fromLogout === '1';
    const forcedMode = query.mode;
    const lastPhone = wx.getStorageSync('lastLogoutPhone') || '';

    // 区分首次登录 vs 退出后重入
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

  // ==================== 协议详情弹窗 ====================

  /** 显示服务协议或隐私政策详情蒙层弹窗 */
  viewAgreement() {
    this.setData({ showAgreementModal: true, agreementTab: 'service' });
  },

  viewPolicy() {
    this.setData({ showAgreementModal: true, agreementTab: 'privacy' });
  },

  /** 切换协议/政策 Tab */
  switchAgreementTab(e) {
    const tab = e.currentTarget.dataset.tab;
    this.setData({ agreementTab: tab });
  },

  /** 关闭协议详情蒙层弹窗（含滑出动画） */
  closeAgreementModal() {
    this.setData({ agreementAnimatingOut: true });
    setTimeout(() => {
      this.setData({
        showAgreementModal: false,
        agreementAnimatingOut: false,
      });
    }, 300);
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
