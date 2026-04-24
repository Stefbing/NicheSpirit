const app = getApp()
const { getApiBase, setApiBase: persistApiBase } = require("./runtime_config")

function request(url, method = "GET", data = {}) {
  const apiBase = app.globalData.apiBase || getApiBase()
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${apiBase}${url}`,
      method,
      data,
      timeout: 10000,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(new Error(`request failed with status ${res.statusCode}`))
      },
      fail: reject
    })
  })
}

function setApiBase(apiBase) {
  const normalized = persistApiBase(apiBase)
  app.globalData.apiBase = normalized
  return normalized
}

module.exports = { request, setApiBase }
