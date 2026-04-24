const { request } = require("../../utils/cloud_request")

Page({
  data: {
    history: [],
    xiaomiStatus: {},
    statusText: "",
    busy: false
  },

  onShow() {
    this.loadData()
  },

  loadData() {
    this.setData({ statusText: "正在刷新体重数据..." })
    return Promise.all([
      request("/api/scale/history/1"),
      request("/api/xiaomi/status")
    ])
      .then(([history, xiaomiStatus]) => {
        this.setData({
          history,
          xiaomiStatus,
          statusText: xiaomiStatus.message || "体重数据已更新"
        })
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "加载失败" })
      })
      .finally(() => {
        wx.stopPullDownRefresh()
      })
  },

  loginXiaomi() {
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/xiaomi/login", "POST", { account: "demo-account" })
      .then(() => this.loadData())
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "登录失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  pushLatestWeight() {
    const latest = this.data.history[0]
    if (!latest) {
      this.setData({ statusText: "没有可推送的体重记录" })
      return
    }
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/xiaomi/push-weight", "POST", { record_id: latest.id })
      .then(() => {
        wx.showToast({ title: "已推送", icon: "success" })
        this.loadData()
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "推送失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  onPullDownRefresh() {
    this.loadData()
  }
})
