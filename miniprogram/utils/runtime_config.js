const DEFAULT_API_BASE = "http://127.0.0.1:8000"

function normalizeApiBase(value) {
  const trimmed = String(value || "").trim().replace(/\/+$/, "")
  return trimmed || DEFAULT_API_BASE
}

function getApiBase() {
  return normalizeApiBase(wx.getStorageSync("apiBase"))
}

function setApiBase(apiBase) {
  const normalized = normalizeApiBase(apiBase)
  wx.setStorageSync("apiBase", normalized)
  return normalized
}

module.exports = {
  DEFAULT_API_BASE,
  getApiBase,
  normalizeApiBase,
  setApiBase
}
