const { request } = require("../../utils/cloud_request")

Page({
  data: {
    devices: [],
    statusText: "",
    busy: false
  },

  onShow() {
    this.loadDevices()
  },

  loadDevices() {
    this.setData({ statusText: "正在刷新猫厕所状态..." })
    return request("/api/petkit/devices")
      .then((devices) => this.setData({ devices, statusText: "设备已更新" }))
      .catch((err) => this.setData({ statusText: err.errMsg || err.message || "加载失败" }))
      .finally(() => {
        wx.stopPullDownRefresh()
      })
  },

  cleanDevice(event) {
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/petkit/clean", "POST", { device_id: event.currentTarget.dataset.id })
      .then((result) => {
        wx.showToast({ title: "已清理", icon: "success" })
        this.setData({ statusText: result.message || "已发送清理指令" })
        this.loadDevices()
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "清理失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  deodorizeDevice(event) {
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/petkit/deodorize", "POST", { device_id: event.currentTarget.dataset.id })
      .then((result) => {
        wx.showToast({ title: "已除臭", icon: "success" })
        this.setData({ statusText: result.message || "已发送除臭指令" })
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "除臭失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  onPullDownRefresh() {
    this.loadDevices()
  }
})
