const { setApiBase } = require("../../utils/cloud_request")
const { getApiBase } = require("../../utils/runtime_config")

Page({
  data: {
    apiBase: "",
    testing: false,
    message: ""
  },

  onShow() {
    this.setData({
      apiBase: getApiBase(),
      message: ""
    })
  },

  onInputApiBase(event) {
    this.setData({ apiBase: event.detail.value })
  },

  saveSettings() {
    const apiBase = setApiBase(this.data.apiBase)
    getApp().globalData.apiBase = apiBase
    this.setData({
      apiBase,
      message: "已保存"
    })
  },

  testConnection() {
    this.setData({ testing: true, message: "" })
    const apiBase = setApiBase(this.data.apiBase)
    wx.request({
      url: `${apiBase}/api/dashboard/data`,
      method: "GET",
      timeout: 8000,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          this.setData({ message: "连接成功，后端可访问" })
          return
        }
        this.setData({ message: `连接失败：HTTP ${res.statusCode}` })
      },
      fail: (err) => {
        this.setData({ message: `连接失败：${err.errMsg || err.message || "unknown error"}` })
      },
      complete: () => {
        this.setData({ testing: false })
      }
    })
  }
})
