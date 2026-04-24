const { getApiBase } = require("./utils/runtime_config")

App({
  globalData: {
    apiBase: getApiBase(),
    currentUserId: 1
  }
})
