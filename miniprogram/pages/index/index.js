const { request } = require("../../utils/cloud_request")
const { getApiBase } = require("../../utils/runtime_config")

Page({
  data: {
    dashboard: {
      scene: {},
      overview: [],
      rooms: [],
      devices: [],
      petkit_devices: [],
      cloudpets: {},
      xiaomi: {},
      hotspots: [],
      has_devices: false,
      need_bind: true,
      user: null
    },
    connection: {
      apiBase: ""
    },
    quickActions: [
      { label: "手动喂食", action: "feed" },
      { label: "立即清理", action: "clean" },
      { label: "除臭", action: "deodorize" },
      { label: "刷新数据", action: "refresh" }
    ],
    statusText: "准备就绪",
    busy: false
  },

  refreshConnection() {
    this.setData({
      connection: {
        apiBase: getApiBase()
      }
    })
  },

  goSettings() {
    wx.navigateTo({ url: "/pages/settings/settings" })
  },

  goFeeder() {
    wx.navigateTo({ url: "/pages/feeder/feeder" })
  },

  goLitterbox() {
    wx.navigateTo({ url: "/pages/litterbox/litterbox" })
  },

  goScale() {
    wx.navigateTo({ url: "/pages/scale/scale" })
  },

  setStatus(statusText) {
    this.setData({ statusText })
  },

  runQuickAction(event) {
    const action = event.currentTarget.dataset.action
    if (this.data.busy) return

    if (action === "refresh") {
      this.loadDashboard()
      return
    }

    this.setData({ busy: true })
    const primaryPetkitDevice = this.data.dashboard.petkit_devices[0]
    const handlers = {
      feed: () => request("/api/cloudpets/feed", "POST", { serving: 1 }),
      clean: () => request("/api/petkit/clean", "POST", { device_id: primaryPetkitDevice?.id || "petkit-t4-01" }),
      deodorize: () => request("/api/petkit/deodorize", "POST", { device_id: primaryPetkitDevice?.id || "petkit-t4-01" })
    }

    const handler = handlers[action]
    if (!handler) {
      this.setData({ busy: false })
      return
    }

    handler()
      .then((result) => {
        wx.showToast({ title: "已执行", icon: "success" })
        this.setStatus(result.message || "操作已完成")
        this.loadDashboard()
      })
      .catch((err) => {
        this.setStatus(err.errMsg || err.message || "操作失败")
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  loadDashboard() {
    this.setStatus("正在刷新数据...")
    return request("/api/dashboard/data")
      .then((dashboard) => {
        this.setData({
          dashboard: {
            scene: {},
            overview: [],
            rooms: [],
            petkit_devices: [],
            cloudpets: {},
            xiaomi: {},
            hotspots: [],
            ...dashboard
          }
        })
        this.setStatus("数据已更新")
      })
      .catch((err) => {
        this.setStatus(err.errMsg || err.message || "数据加载失败")
      })
      .finally(() => {
        wx.stopPullDownRefresh()
      })
  },

  onShow() {
    this.refreshConnection()
    this.loadDashboard()
  },

  onPullDownRefresh() {
    this.refreshConnection()
    this.loadDashboard()
  }
})
