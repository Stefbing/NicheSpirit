const { request } = require("../../utils/cloud_request")

Page({
  data: {
    plans: [],
    servingsToday: 0,
    statusText: "",
    busy: false,
    newPlan: {
      time: "18:30",
      serving: 1,
      enable: true
    }
  },

  onShow() {
    this.loadData()
  },

  loadData() {
    this.setData({ statusText: "正在刷新喂食器状态..." })
    return Promise.all([
      request("/api/cloudpets/plans"),
      request("/api/cloudpets/servings_today")
    ])
      .then(([plans, servings]) => {
        this.setData({
          plans,
          servingsToday: servings.servings_today,
          statusText: "喂食器已更新"
        })
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "加载失败" })
      })
      .finally(() => {
        wx.stopPullDownRefresh()
      })
  },

  setNewPlanField(event) {
    const { field } = event.currentTarget.dataset
    this.setData({
      newPlan: {
        ...this.data.newPlan,
        [field]: event.detail.value
      }
    })
  },

  feedNow(event) {
    const serving = Number(event.currentTarget.dataset.serving || 1)
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/cloudpets/feed", "POST", { serving })
      .then((data) => {
        this.setData({ servingsToday: data.servings_today, statusText: data.message })
        wx.showToast({ title: "已投喂", icon: "success" })
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "投喂失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  addPlan() {
    if (this.data.busy) return
    this.setData({ busy: true })
    request("/api/cloudpets/plans", "POST", {
      time: this.data.newPlan.time,
      serving: Number(this.data.newPlan.serving),
      enable: this.data.newPlan.enable
    })
      .then(() => {
        wx.showToast({ title: "计划已添加", icon: "success" })
        this.loadData()
      })
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "添加计划失败" })
      })
      .finally(() => {
        this.setData({ busy: false })
      })
  },

  togglePlan(event) {
    const id = event.currentTarget.dataset.id
    const plan = this.data.plans.find((item) => item.id === id)
    if (!plan) return
    request(`/api/cloudpets/plans/${id}`, "PUT", { enable: !plan.enable })
      .then(() => this.loadData())
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "更新计划失败" })
      })
  },

  deletePlan(event) {
    const id = event.currentTarget.dataset.id
    request(`/api/cloudpets/plans/${id}`, "DELETE")
      .then(() => this.loadData())
      .catch((err) => {
        this.setData({ statusText: err.errMsg || err.message || "删除计划失败" })
      })
  },

  onPullDownRefresh() {
    this.loadData()
  }
})
