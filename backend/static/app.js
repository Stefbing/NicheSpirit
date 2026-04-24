const state = {
  petkitDevices: [],
  cloudpets: null,
  weightHistory: [],
  configs: [],
};

const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => Array.from(document.querySelectorAll(selector));

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function showToast(message) {
  const toast = qs("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function renderOverview(data) {
  qs("#scene-title").textContent = data.scene.title;
  qs("#scene-subtitle").textContent = data.scene.subtitle;
  qs("#overview-grid").innerHTML = data.overview.map((item) => `
    <article class="overview-item">
      <p class="muted">${item.label}</p>
      <div class="metric-value">${item.value}</div>
      <div class="metric-trend">${item.trend}</div>
    </article>
  `).join("");
  qs("#room-list").innerHTML = data.rooms.map((room) => `
    <article class="mini-card">
      <p>${room.name}</p>
      <strong>${room.count} 台设备</strong>
    </article>
  `).join("");
}

function renderPetkit() {
  const markup = state.petkitDevices.map((device) => `
    <article class="device-card">
      <div class="device-title">
        <div>
          <strong>${device.name}</strong>
          <p class="muted">${device.model}</p>
        </div>
        <span class="status-pill">${device.status}</span>
      </div>
      <div>
        <p class="muted">猫砂余量 ${device.sand_level}%</p>
        <div class="progress-track"><div class="progress-bar" style="width:${device.sand_level}%"></div></div>
      </div>
      <p>今日访问 ${device.stats.today_visits} 次，平均停留 ${device.stats.avg_duration}</p>
      <div class="actions-row">
        <button class="action-btn primary" onclick="petkitAction('clean', '${device.id}')">立即清理</button>
        <button class="action-btn" onclick="petkitAction('deodorize', '${device.id}')">除臭</button>
      </div>
    </article>
  `).join("");
  qs("#litterbox-list").innerHTML = markup;
  qs("#dashboard-devices").innerHTML = markup;
}

function renderFeeder() {
  if (!state.cloudpets) return;
  const feeder = state.cloudpets;
  qs("#feeder-card").innerHTML = `
    <article class="device-card">
      <div class="device-title">
        <div>
          <strong>${feeder.name}</strong>
          <p class="muted">设备在线，电量 ${feeder.battery}%</p>
        </div>
        <span class="status-pill">${feeder.status}</span>
      </div>
      <div>
        <p class="muted">粮仓余量 ${feeder.food_level}%</p>
        <div class="progress-track"><div class="progress-bar" style="width:${feeder.food_level}%"></div></div>
      </div>
      <p>今日已出粮 <strong>${feeder.servings_today}</strong> 份</p>
      <div class="actions-row">
        <button class="action-btn primary" onclick="feedNow(1)">投喂 1 份</button>
        <button class="action-btn" onclick="feedNow(2)">投喂 2 份</button>
      </div>
      <div class="stack">
        ${feeder.plans.map((plan) => `
          <article class="mini-card">
            <div class="device-title">
              <strong>${plan.time}</strong>
              <span class="tag">${plan.enable ? "启用中" : "已关闭"}</span>
            </div>
            <p class="muted">${plan.serving} 份 · 周 ${plan.days.join("/")}</p>
          </article>
        `).join("")}
      </div>
    </article>
  `;
}

function renderHealth() {
  const maxWeight = Math.max(...state.weightHistory.map((item) => item.weight), 1);
  qs("#health-chart").innerHTML = state.weightHistory.slice().reverse().map((item) => {
    const height = Math.max(60, (item.weight / maxWeight) * 180);
    const label = new Date(item.timestamp).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
    return `
      <div>
        <div class="chart-bar" style="height:${height}px"></div>
        <div class="chart-label">${item.weight}kg</div>
        <div class="chart-label">${label}</div>
      </div>
    `;
  }).join("");
  qs("#health-history").innerHTML = state.weightHistory.map((item) => `
    <article class="history-item">
      <div class="device-title">
        <strong>${item.weight} kg</strong>
        <span class="tag">${item.xiaomi_pushed ? "已同步" : "待同步"}</span>
      </div>
      <p class="muted">BMI ${item.bmi} · 体脂 ${item.body_fat}% · 水分 ${item.water}%</p>
    </article>
  `).join("");
}

function renderConfigs() {
  qs("#config-list").innerHTML = state.configs.map((item) => `
    <article class="config-item">
      <div class="device-title">
        <strong>${item.key}</strong>
        <span class="tag">${item.platform}</span>
      </div>
      <p class="muted">${item.device_name || "全局配置"}</p>
      <p>${item.is_encrypted ? "已加密保存" : item.value}</p>
    </article>
  `).join("");
}

async function loadAll() {
  const [dashboard, petkit, plans, servings, weight, configs] = await Promise.all([
    request("/api/dashboard/data"),
    request("/api/petkit/devices"),
    request("/api/cloudpets/plans"),
    request("/api/cloudpets/servings_today"),
    request("/api/scale/history/1"),
    request("/api/config/list"),
  ]);

  state.petkitDevices = petkit;
  state.cloudpets = { ...(dashboard.cloudpets || {}), plans, servings_today: servings.servings_today };
  state.weightHistory = weight;
  state.configs = configs;

  renderOverview(dashboard);
  renderPetkit();
  renderFeeder();
  renderHealth();
  renderConfigs();
}

async function petkitAction(action, deviceId) {
  const data = await request(`/api/petkit/${action}`, {
    method: "POST",
    body: JSON.stringify({ device_id: deviceId }),
  });
  showToast(data.message);
}

async function feedNow(serving) {
  const data = await request("/api/cloudpets/feed", {
    method: "POST",
    body: JSON.stringify({ serving }),
  });
  state.cloudpets.servings_today = data.servings_today;
  renderFeeder();
  showToast(data.message);
}

async function initActions() {
  qsa(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      qsa(".nav-item").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      document.querySelector(`[data-panel="${button.dataset.tab}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  qs("#refresh-all").addEventListener("click", async () => {
    await request("/api/cache/refresh", { method: "POST" });
    await loadAll();
    showToast("状态已刷新");
  });

  qs("#xiaomi-login").addEventListener("click", async () => {
    await request("/api/xiaomi/login", {
      method: "POST",
      body: JSON.stringify({ account: "demo@xiaomi" }),
    });
    showToast("小米云连接成功");
  });

  qs("#config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    await request("/api/config", {
      method: "POST",
      body: JSON.stringify({
        user_id: 1,
        key: formData.get("key"),
        value: formData.get("value"),
        platform: formData.get("platform") || "custom",
        device_name: formData.get("device_name") || "",
        is_encrypted: formData.get("is_encrypted") === "on",
      }),
    });
    event.currentTarget.reset();
    state.configs = await request("/api/config/list");
    renderConfigs();
    showToast("配置已保存");
  });
}

window.petkitAction = petkitAction;
window.feedNow = feedNow;

initActions().then(loadAll).catch((error) => {
  console.error(error);
  showToast("数据加载失败，请检查后端服务");
});
