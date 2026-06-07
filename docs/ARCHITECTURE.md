# NicheSpirit (冷门器灵) 架构文档

> 版本: 1.0  
> 最后更新: 2026-05-27

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构总览](#2-系统架构总览)
3. [数据库层](#3-数据库层)
4. [后端服务层](#4-后端服务层)
5. [核心链路详解](#5-核心链路详解)
   - [5.1 登录链路](#51-登录链路)
   - [5.2 添加云设备链路](#52-添加云设备链路)
   - [5.3 添加体脂秤链路](#53-添加体脂秤链路)
   - [5.4 Dashboard 数据刷新链路](#54-dashboard-数据刷新链路)
   - [5.5 定时任务缓存刷新链路](#55-定时任务缓存刷新链路)
   - [5.6 体脂秤测量链路](#56-体脂秤测量链路)
   - [5.7 Token 双写链路](#57-token-双写链路)
   - [5.8 设备分享链路](#58-设备分享链路)
   - [5.9 登录绑定冲突链路](#59-登录绑定冲突链路)
6. [前端模块详解](#6-前端模块详解)
7. [异常处理矩阵](#7-异常处理矩阵)
8. [附录：完整 API 清单](#8-附录完整-api-清单)

---

## 1. 项目概述

NicheSpirit 是一个微信小程序 + FastAPI 后端的智能家居管理中心，支持三种设备类型：

| 设备 | 厂商 | 连接方式 | 数据源 |
|------|------|----------|--------|
| 智能猫厕所 | PetKit (小佩) | 云端 API (pypetkitapi) | `petkit_service.py` |
| 智能喂食机 | CloudPets (云宠) | 云端 API (httpx) | `cloudpets_service.py` |
| 体脂秤 | Xiaomi (小米) | 本地 BLE (微信蓝牙) | `ble_scale.js` + `app.js` BLE |

### 核心设计理念

- **Token 鉴权**: 无 JWT，使用 `secrets.token_hex(32)` + SHA256 哈希存储于 `user.token_hash`
- **设备配置加密**: XOR + Base64 轻度混淆存储于 `systemconfig` 表
- **DeviceCache 热点缓存**: 启动时全量加载 `user_id>0 AND is_active=1` 的设备配置到内存 DeviceRecord，按 `user_id→platform` 分组，惰性重建。不额外使用 CacheManager 或 SessionCache —— 避免缓存层级冗余
- **Token 双写**: 每次云 API 调用后，最新 token/session 同时写回 DB 的 `key='token'` 设备配置组 + DeviceRecord 惰性重建
- **BLE 发布-订阅**: 全局单次扫描，多页面订阅广播数据
- **BLE 条件启动**: 仅当 DeviceCache 检测到当前用户存在 `platform='xiaomi'` 且 `is_complete=true` 的设备时，前端才启动 BLE 扫描

---

## 2. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        微信小程序前端                                │
│                                                                    │
│  app.js (全局)                                                      │
│   ├─ 生命周期: onLaunch → checkAndAutoLogin                          │
│   ├─ BLE 条件启动: 仅当前用户 DeviceCache 有 xiaomi 平台时才 init     │
│   ├─ BLE 扫描: 持续运行, handleDeviceFound → notifyScaleListeners   │
│   ├─ 设备发现: subscribeDeviceDiscovery (供绑定界面使用)              │
│   └─ Dashboard: fetchDashboardData (30s缓存+并发锁+15s超时)          │
│                                                                    │
│  pages/                                                             │
│   ├─ login/     → 登录页 (5种模式详见 6.1 节)                       │
│   ├─ index/     → 首页 (设备卡片渲染 + 体脂秤绑定流程)              │
│   ├─ scale/     → 体脂秤页 (BIA算法+状态机IDLE→MEASURING→COMPLETED) │
│   ├─ settings/  → 设置页 (设备配置状态 + 账号管理)                  │
│   └─ privacy/   → 隐私协议                                         │
│                                                                    │
│  utils/                                                             │
│   ├─ cloud_request.js → HTTP封装 (local/cloud双模式, 自动重试)       │
│   └─ ble_scale.js     → 小米体脂秤2 BLE协议解析器                   │
└───────────────────────┬─────────────────────────────────────────────┘
                        │ HTTP/HTTPS
                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         FastAPI 后端                                │
│                                                                    │
│  lifespan: init_db → 加载配置 → DeviceCache.load_all → 初始化服务    │
│           → 注册定时任务(90s) → 启动缓存清理                         │
│                                                                    │
│  AppState (全局单例，asyncio.Lock 互斥)                              │
│   ├─ state.petkit       → PetKitService                              │
│   ├─ state.petkit_lock  ← asyncio.Lock() 防多用户交替覆盖            │
│   ├─ state.cloudpets    → CloudPetsService                           │
│   ├─ state.cloudpets_lock ← asyncio.Lock() 防多用户交替覆盖          │
│   └─ BLE 设备(xiaomi) 每个用户独立，无需全局锁                       │
│                                                                    │
│  服务层 → petkit_service.py / cloudpets_service.py                  │
│  缓存层 → DeviceCache (唯一缓存，合并了 CacheManager+SessionCache)   │
│  配置层 → config_manager.py / config_encryptor.py                  │
│  调度层 → TaskScheduler (1个定时任务: dashboard_cache_refresh)      │
│  分享层 → share_routes.py (4个端点)                                 │
│  数据层 → SQLModel (6个模型) + MySQL                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 数据库层

### 3.1 表结构

```sql
-- 6 张表，全部 InnoDB + utf8mb4

user
├── id              INT PK AUTO_INCREMENT
├── phone_number    VARCHAR(20) NOT NULL, UNIQUE KEY
├── password_hash   VARCHAR(256)       -- bcrypt 哈希
├── openid          VARCHAR(64) UNIQUE -- 微信 OpenID（设备标识）
├── unionid         VARCHAR(64)
├── session_key     VARCHAR(64)        -- 微信临时密钥
├── nickname        VARCHAR(100)
├── token_hash      VARCHAR(64) UNIQUE -- 登录 token SHA256
├── token_expires_at BIGINT
├── privacy_consent_at BIGINT
└── created_at / updated_at BIGINT

systemconfig              -- KV 配置表
├── id              INT PK AUTO_INCREMENT
├── user_id         INT DEFAULT 0     -- 0=全局, >0=用户设备
├── key             VARCHAR(50)       -- account/password/token/ble_address/...
├── value           TEXT              -- 加密或明文
├── platform        VARCHAR(32)       -- petkit/cloudpets/xiaomi
├── device_name     VARCHAR(100)      -- 真实设备名（如"小佩智能全自动猫厕所 MAX2"）
├── is_encrypted    TINYINT(1)
├── is_active       TINYINT(1) DEFAULT 1  -- 软删除标记
└── updated_at      BIGINT
INDEX: idx_user_key(user_id, key), idx_user_platform_active(user_id, platform, is_active)

family_member            -- 家庭成员（体脂计算用）
├── user_id INT FK→user
├── name / gender / age / height
├── relationship    VARCHAR(20)       -- self/spouse/child/parent/other
├── is_active       TINYINT(1)
└── sort_order      INT

weightrecord             -- 体重测量记录
├── user_id / member_id (FK)
├── weight / impedance / bmi / body_fat / muscle / water / protein
├── visceral_fat / bone_mass / bmr
└── timestamp       BIGINT            -- 测量时间（用于时段去重）

device_share             -- 设备分享记录
├── from_user_id / to_user_id (FK)
├── from_openid     VARCHAR(64)       -- 冗余，openid 级联查询
├── share_token     VARCHAR(64) UNIQUE -- SHA256 令牌
├── status          VARCHAR(16)       -- pending/accepted/revoked
├── device_keys     TEXT              -- JSON 数组
└── expires_at      BIGINT            -- 24h 过期

shared_device_config     -- 分享配置映射
├── share_id        BIGINT FK→device_share
├── to_user_id      INT FK→user
├── platform        VARCHAR(32)
├── device_key      VARCHAR(64)
├── config_account  VARCHAR(128)      -- 生成的共享账号名
└── config_password VARCHAR(256)      -- 分享者原文密码
```

### 3.2 SystemConfig 记录约定

| 场景 | user_id | platform          | device_name | key 集合 | is_encrypted |
|------|---------|-------------------|-------------|----------|-------------|
| 全局配置 | 0 | NULL              | NULL | WECHAT_APPID, WECHAT_SECRET, TOKEN_EXPIRE_HOURS, ... | 0 |
| CloudPets 设备 | >0 | cloudpets         | 真实设备名 | account, password | 1 |
| PetKit 设备 | >0 | petkit            | 真实设备名 | account, password | 1 |
| 体脂秤 | >0 | xiaomi            | 蓝牙名(如"MIBFS") | ble_address | 0 |
| 服务 session | >0 | cloudpets, petkit | NULL | token | 0 |

---

## 4. 后端服务层

### 4.1 模块依赖关系

```
FastAPI main.py
  ├── config_manager.py      ← 设备配置 CRUD (含 update_cloud_device_token 双写)
  │     ├── config_encryptor.py  ← XOR+Base64 加解密
  │     └── models/models.py     ← SQLModel ORM
  ├── device_cache.py         ← 唯一缓存层 (合并了旧 CacheManager+SessionCache)
  ├── services/
  │   ├── petkit_service.py   ← PetKitClient (pypetkitapi, 内置 _memory_session)
  │   └── cloudpets_service.py ← httpx client (内置 _memory_token)
  ├── scheduler/
  │   └── task_scheduler.py   ← 异步定时任务
  ├── share_routes.py         ← 分享路由
  ├── utils/
  │   ├── config_encryptor.py
  │   └── token_extractor.py  ← 小米云 token 提取
  └── models/
      ├── db.py               ← 数据库引擎
      └── models.py           ← 6 个 SQLModel
```

### 4.2 缓存层级（仅 DeviceCache，无冗余层级）

```
DeviceCache (内存, { user_id: { platform: DeviceRecord } })
  ├── load_all()              → 启动时全量加载所有 is_active=1 AND user_id>0 的设备
  ├── get_user_platforms(uid) → 惰性加载(先查内存→未命中查DB→重建缓存)
  ├── invalidate_user(uid)    → 设备变更时清除，下次查询自动重载
  └── is_complete 校验        → 云设备需 account+password+token，BLE 需 ble_address

服务层内置内存缓存:
  ├── PetKitService._memory_session   ← 每次登录/刷新后同步更新
  └── CloudPetsService._memory_token  ← 每次登录/刷新后同步更新
```

**为什么不需要 CacheManager / SessionCache**:
- `CacheManager(通用内存缓存)` 的职责完全可由 DeviceCache 覆盖——设备配置和仪表盘数据的生成入口统一为 `device_cache.get_user_platforms()` + `update_cloud_device_token()`，无需第二套 LRU+TTL 缓存
- `SessionCache(二级内存→DB)` 的职责已由各 Service 类的 `_memory_session`/`_memory_token` 字段直接覆盖——每次登录/API 调用后更新内存，同时写回 DB，无需独立的缓存管理类
- **精简结果**: 以 DeviceCache 作为唯一缓存入口，服务层自管自身 token 内存缓存，二者职责清晰、无冗余

### 4.3 全局状态 (AppState)

```python
class AppState:
    petkit: Optional[PetKitService]      # 全局 PetKit 实例 (asyncio.Lock 保护)
    petkit_lock: asyncio.Lock             # 防多用户并发覆盖
    cloudpets: Optional                  # 全局 CloudPets 实例 (asyncio.Lock 保护)
    cloudpets_lock: asyncio.Lock          # 防多用户并发覆盖
    data_refresh_task: Optional          # 后台刷新任务句柄
```

**注意**: 
- **云设备 (PetKit/CloudPets)**: `state.petkit` / `state.cloudpets` 是全局单例。`_init_service_for_user()` 中通过 `async with state.petkit_lock:` 确保同一时间只有一个请求在修改/使用全局实例。`_get_petkit_for_user()` / `_get_cloudpets_for_user()` 在 user_id 不匹配时创建临时实例，不涉及全局锁
- **BLE 设备 (xiaomi)**: 纯本地蓝牙，每个用户独立绑定，无全局实例冲突，无需锁

---

## 5. 核心链路详解

### 5.1 登录链路

```
用户触发登录
  │
  ├──▶ [静默登录] POST /api/auth/silent-login { code }
  │     │
  │     ├── wx_code2session(code) → 获取 openid
  │     ├── SELECT user WHERE openid=?  → 未找到 → 401 UNBOUND
  │     └── 找到 → 更新 session_key, 签发 token
  │
  └──▶ [账密登录] POST /api/auth/bind { account, password, code, force_bind?, skip_bind? }
        │
        ├── Step 1: wx_code2session(code) → openid
        ├── Step 2: SELECT user WHERE phone_number=account
        │     ├── 已有用户 → 校验密码 (bcrypt)
        │     └── 新用户 → INSERT user (phone, password_hash, nickname)
        ├── Step 3: 设备绑定冲突检测
        │     ├── SELECT user WHERE openid=? AND id!=user.id
        │     ├── 有冲突且 force_bind=false, skip_bind=false
        │     │     → 返回 409 { code: "DEVICE_BOUND", bound_user: {phone_masked, nickname} }
        │     ├── 有冲突且 force_bind=true  → 清空旧用户的 openid → 绑定当前用户
        │     └── skip_bind=true  → 跳过 openid 更新（保持原绑定）
        ├── Step 4: 签发 token (SHA256)
        │     ├── raw_token = secrets.token_hex(32)
        │     ├── token_hash = SHA256(raw_token)
        │     └── user.token_hash, user.token_expires_at
        │
        └── 返回 { token, user_id, phone_number, openid, nickname, is_new_user, openid_bound }

前端 onLoginSuccess:
  wx.setStorageSync('token', raw_token)
  wx.setStorageSync('userInfo', { user_id, phone_number, openid, nickname })
  wx.reLaunch('/pages/index/index')
```

**关键节点逻辑**:

| 节点 | 判断条件 | 动作 |
|------|----------|------|
| 密码校验 | `verify_password(password, user.password_hash)` | 失败 → 401 "手机号或密码错误" |
| 首次设密 | `not user.password_hash` | 直接 hash 写入（允许先使用后设密） |
| 冲突检测 | `User.openid == openid AND User.id != current.id` | 存在 → 返回 409 DEVICE_BOUND |
| 换绑 (force) | `force_bind=True` | `old_user.openid = None` 清除旧绑定 |
| 拒绝 (skip) | `skip_bind=True` | 不更新 `User.openid`，保留原绑定 |
| Token 过期 | `token_expires_at < now` | 下次请求时 401，需重新登录 |

**异常处理**:

| 异常场景 | HTTP 状态码 | 处理方式 |
|----------|-------------|----------|
| 手机号格式错误 | 400 | 前端提示"请输入正确的11位手机号" |
| 密码长度不足 | 400 | 前端提示"密码至少4位" |
| wx.login code 无效 | 400 | 前端降级展示选择页 |
| 手机号或密码错误 | 401 | 前端提示"手机号或密码错误" |
| 设备绑定冲突 | 409 | 前端弹窗确认"是否改绑？" |

---

### 5.2 添加云设备链路

```
前端 onSubmitDeviceConfig()
  │
  ├── POST /api/devices/add { device_type, platform, account, password, device_name? }
  │
  ▼
add_device_api()
  │
  ├── Step 1: 验证凭据
  │     ├── is_scale = (platform == 'xiaomi')
  │     ├── scale → init_ok = True
  │     └── 非 scale → 调用 _init_service_for_user()
  │           ├── cloudpets: CloudPetsService(user_id).initialize(account, password)
  │           │     ├── _load_token_from_db() → 无 → _login()
  │           │     │     ├── POST /app/terminal/user/login
  │           │     │     ├── 提取 authorization token
  │           │     │     ├── _save_token_to_db(token) → systemconfig(key='token')
  │           │     │     └── self._memory_token = token
  │           │     └── 返回 True/False
  │           │
  │           └── petkit: PetKitService(account, password, user_id).initialize()
  │                 ├── _load_session_from_db() → 无 → _login()
  │                 │     ├── 创建 aiohttp session + PetKitClient
  │                 │     ├── client.get_devices_data()
  │                 │     ├── _save_session_to_db() → systemconfig(key='token')
  │                 │     └── self._memory_session = session_data
  │                 └── 返回 True/False
  │
  ├── [Step 1.5] 提取 token 回传
  │     ├── petkit  → state.petkit._memory_session → json.dumps → token
  │     ├── cloudpets → state.cloudpets._memory_token → token
  │     └── token 为空 → 日志警告（is_complete=False）
  │
  ├── Step 2: 持久化到 DB
  │     ├── add_cloud_device(uid, platform, account, password, token, device_name)
  │     │     └── 事务中 upsert 三条 systemconfig 记录:
  │     │           ├── key='account',  value=AES(account),  is_encrypted=1
  │     │           ├── key='password', value=AES(password), is_encrypted=1
  │     │           └── key='token',    value=AES(token),    is_encrypted=1
  │     └── 返回 device_key
  │
  └── Step 3: 缓存失效
        ├── device_cache.invalidate_user(uid)  → 清除 DeviceCache
        ├── cache_manager.delete('dashboard_combined_data', ...)
        └── 日志

请求/响应数据格式:

POST /api/devices/add   ──▶  Request:
{
  "device_type": "feeder",       // 前端保留字段(后端仅用于 scale 判断)
  "platform": "cloudpets",       // 实际路由依据
  "account": "17757577548",
  "password": "15050514533",
  "device_name": "智能喂食机"     // 可选，后端默认使用 platform
}

◀── Response 200:
{
  "device_key": "cloudpets_智能喂食机",
  "device_type": "feeder",
  "device_name": "智能喂食机",
  "platform": "cloudpets",
  "status": "active"
}

◀── Response 400 (登录失败):
{
  "detail": "cloudpets 登录失败：账号或密码错误，请检查后重试"
}
```

**异常处理矩阵**:

| 异常点 | 条件 | 响应 | 日志 |
|--------|------|------|------|
| user_id 解析失败 | `int('abc')` | 400 | — |
| 服务初始化失败 | `init_ok == False` | 400 "登录失败" | `{platform} init failed: ...` |
| token 提取为空 | `mem_session is None` | 继续执行（warning） | `token 为空，设备配置组 is_complete=False` |
| add_cloud_device 异常 | DB 写入失败 | 500 | `保存云设备失败: ...` |
| 未捕获异常 | 其他 | 500 | `添加设备失败: ...` |

---

### 5.3 添加体脂秤链路

```
前端 confirmBindScale(e)
  │
  ├── deviceId = e.currentTarget.dataset.deviceId  (BLE MAC)
  ├── isDuplicate 检查 → 重复则弹窗拦截
  │
  ├── POST /api/devices/scale/bind { device_id, device_name }
  │
  ▼
bind_scale_device()
  │
  ├── 防重复校验
  │     ├── query systemconfig WHERE key='ble_device_id' AND user_id=uid
  │     └── 已存在且 device_id 相同 → 409 "无法重复添加同一蓝牙设备"
  │
  ├── 一用户一秤校验
  │     └── get_user_devices(uid, 'xiaomi') → 已有 → 409
  │
  ├── add_ble_device(uid, ble_address, device_name)
  │     └── systemconfig 插入/更新 1 条记录:
  │           (key='ble_address', value=MAC, platform='xiaomi',
  │            device_name='MIBFS', is_encrypted=0)
  │
  └── 缓存失效 + 成员初始化
        ├── device_cache.invalidate_user(uid)
        ├── cache_manager.delete(...)
        └── initScaleSelfMember() → 创建默认"自己"成员

请求/响应数据格式:

POST /api/devices/scale/bind   ──▶  Request:
{
  "device_id": "XX:XX:XX:XX:XX:XX",
  "device_name": "MIBFS"
}

◀── Response 200:
{
  "device_key": "xiaomi_MIBFS",
  "device_id": "xx:xx:xx:xx:xx:xx",
  "device_name": "MIBFS",
  "status": "active"
}

◀── Response 409 (重复):
{
  "detail": "无法重复添加同一蓝牙设备"
}
```

**BLE 扫描绑定流程**（前端）：

```
用户点击"体脂秤" → showScalePermissionDialog
  → 点击"开始绑定" → authorizeAndScan()
      → app.checkAndInitBluetooth() → wx.openBluetoothAdapter → startContinuousScan()
      → subscribeDeviceDiscovery(devices)  ← 每批次 BLE 广播触发
          → _filterDiscoveredDevices()
              → GET /api/devices/scale/bound (5s 缓存) 查已绑设备
              → 按信号强度排序, 标记已绑定设备
              → setData({ scaleDiscoveredDevices })
      → 10s 后自动停止扫描
      → 用户点击设备 → confirmBindScale()
```

---

### 5.4 Dashboard 数据刷新链路

```
前端 loadUserDevices()
  │
  └── app.fetchDashboardData(userId)
        │
        ├── 缓存命中(30s内): 直接返回 cachedDashboardData
        │
        ├── 缓存未命中:
        │     ├── 设置并发锁 dashboardFetching=true
        │     └── GET /api/dashboard/data?user_id=X
        │
        ▼
get_dashboard_data()
  │
  ├── 简单内存字典 (dashboard 组合数据, TTL=120s, 定时任务预刷新)
  │
  ├── device_cache.get_user_platforms(uid)
  │     └── 返回 { 'petkit': DeviceRecord, 'cloudpets': DeviceRecord, 'xiaomi': DeviceRecord }
  │
  ├── 提取平台配置
  │     ├── petkit_rec = platforms.get('petkit')  (is_complete 校验)
  │     ├── cloudpets_rec = platforms.get('cloudpets')
  │     └── xiaomi_rec = platforms.get('xiaomi')
  │
  ├── 构建 device_platforms 列表 (供前端渲染)
  │
  ├── [并行] fetch_petkit_devices()
  │     ├── 优先复用 state.petkit (user_id 匹配)
  │     ├── 否则创建临时 PetKitService + initialize()
  │     ├── 调用 get_devices() → 自动刷新 session
  │     ├── [双写] 提取 _memory_session → update_cloud_device_token()
  │     └── cache_manager.set('_petkit_devices', ttl=300)
  │
  ├── [并行] fetch_cloudpets_data()
  │     ├── 同上模式
  │     ├── get_servings_today() + get_feeding_plans()
  │     ├── [双写] 提取 _memory_token → update_cloud_device_token()
  │     └── cache_manager.set('_cloudpets_servings', ttl=120)
  │
  ├── device_cache.invalidate_user(uid)  ← token 已刷新
  │
  ├── 体脂秤统计 (WeightRecord 表)
  │     └── today_count + latest_body_fat
  │
  ├── 组装 dashboard_data
  │     └── cache_manager.set('_dashboard_combined_data', ttl=120)
  │
  └── 返回 dashboard_data

响应数据格式:

◀── GET /api/dashboard/data?user_id=1  Response 200:
{
  "device_platforms": [                   // 设备平台列表（渲染依据）
    {
      "platform": "petkit",
      "device_name": "小佩全自动猫厕所",
      "device_key": "petkit_小佩全自动猫厕所",
      "is_ble": false,
      "is_complete": true
    },
    {
      "platform": "cloudpets",
      "device_name": "智能喂食机",
      "device_key": "cloudpets_智能喂食机",
      "is_ble": false,
      "is_complete": true
    },
    {
      "platform": "xiaomi",
      "device_name": "MIBFS",
      "device_key": "xiaomi_MIBFS",
      "is_ble": true,
      "is_complete": true
    }
  ],
  "petkit_devices": [ ... ],              // PetKit API 设备列表
  "cloudpets_servings": { "result": 3 },  // CloudPets 今日出粮
  "cloudpets_plans": [ ... ],             // 喂食计划
  "scale_stats": {                        // 体脂秤统计
    "today_count": 2,
    "latest_body_fat": 18.5
  },
  "xiaomi_config": true,                  // 体脂秤是否已配置
  "has_shared_devices": false             // 是否有共享设备
}
```

**缓存键与 TTL 一览**：

| 缓存键 | TTL | 存储位置 | 失效时机 |
|--------|-----|----------|----------|
| `user_{id}_dashboard_combined_data` | 120s | CacheManager | 设备增删、定时刷新 |
| `user_{id}_petkit_devices` | 300s | CacheManager | 设备增删、定时刷新 |
| `user_{id}_cloudpets_servings` | 120s | CacheManager | 设备增删、定时刷新 |
| `user_{id}_cloudpets_plans` | 300s | CacheManager | 设备增删、定时刷新 |
| `user_{id}_petkit_stats_{id}` | 180s | CacheManager | 定时刷新 |
| `{user_id: {platform: DeviceRecord}}` | 无（常驻内存） | DeviceCache | `invalidate_user()` 后惰性重建 |
| `app.globalData.cachedDashboardData` | 30s (前端) | 小程序内存 | 登出、设备增删 |

---

### 5.5 定时任务缓存刷新链路

```
scheduler.add_task('dashboard_cache_refresh', interval=90s)
  │
  ▼
refresh_dashboard_cache()
  │
  ├── SELECT user_id FROM systemconfig WHERE platform IN ('petkit','cloudpets') AND key='account' AND is_active=1
  ├── + SELECT to_user_id FROM shared_device_config (共享设备用户)
  │
  ├── 对每个用户并行执行 refresh_single_user(uid):
  │     ├── cache_manager.delete('_dashboard_combined_data')  ← 清除旧缓存
  │     ├── device_cache.get_user_platforms(uid)              ← 从缓存获取配置
  │     │     └── { 'petkit': DeviceRecord, 'cloudpets': DeviceRecord }
  │     ├── 提取各平台 account/password
  │     ├── 有 PetKit → _get_petkit_for_user() → get_devices()
  │     │     ├── 自动刷新 session (如过期)
  │     │     └── [双写] update_cloud_device_token()
  │     ├── 有 CloudPets → _get_cloudpets_for_user() → get_servings_today()
  │     │     └── [双写] update_cloud_device_token()
  │     ├── device_cache.invalidate_user(uid)
  │     └── cache_manager.set('_dashboard_combined_data', ttl=120)
  │
  └── 统计: elapsed time, success/error count
```

**与 dashboard 端点的关系**:

| 特性 | Dashboard API | 定时任务 |
|------|--------------|----------|
| 触发时机 | 用户访问首页 (pull-to-refresh) | 每 90 秒系统自动执行 |
| 缓存策略 | 先读缓存，未命中时重建 | 先清除缓存后重建 |
| 目标用户 | 当前请求的 user_id | 所有有配置的用户 |
| token 双写 | ✅ update_cloud_device_token() | ✅ 同上 |
| DeviceCache 刷新 | ✅ invalidate_user() | ✅ 同上 |

---

### 5.6 体脂秤测量链路

```
BLE 设备广播数据 (13-14 bytes)
  │
  ├── wx.onBluetoothDeviceFound → handleDeviceFound(res)
  │     ├── 过滤: device.name 包含 'mibfs' 或 'mi scale'
  │     ├── 解析: BLEUtils.parse(buffer)
  │     │     ├── 控制字节 → 单位/稳定/下秤/阻抗有效
  │     │     ├── 体重 = raw / 200 (KG)
  │     │     ├── 阻抗 = raw (200-2000Ω)
  │     │     └── 设备时间 = UTC timestamp
  │     ├── 新鲜度检测: 设备时间 vs 当前时间 < 10s
  │     ├── 去重: 5s 窗口内体重+阻抗+稳定标志相同
  │     └── notifyScaleListeners(data)
  │
  ▼
scale.js handleScaleData(data)
  │
  ├── 过滤: weight < 3kg → 跳过
  ├── 体重骤降检测: 下秤判定
  ├── 阻抗等待: 延迟 500ms 等阻抗数据
  ├── 稳定性检测: 连续 3 次差值 < 0.3kg 或 isStabilized 标志
  │
  ├── lockAndCalculate(data) → 锁定体重/阻抗
  │     ├── autoMatchMember(weight)
  │     │     └── 按体重差异匹配成员 (±10kg 容差)
  │     └── calculateBodyMetrics(weight, impedance)
  │           ├── BMI = weight / (height_m²)
  │           ├── FFM = Asian-adjusted BIA formula
  │           ├── Body Fat % = (weight - FFM) / weight
  │           ├── Water % = FFM * 0.732 / weight
  │           ├── Muscle Mass, Protein, BMR, Visceral Fat, Bone Mass
  │           └── 范围分类: low/normal/high/very-high
  │
  └── autoSaveMeasurement()
        └── POST /api/scale/measurements {
              weight, impedance, body_fat, muscle, water,
              protein, visceral_fat, bone_mass, bmr,
              member_id, device_name, unit
            }

后端 → create_scale_measurement()
  ├── 时段去重: 按早(04-10)/午(10-16)/晚(16-22)/宵夜(22-04) 覆盖更新
  ├── INSERT/UPDATE weightrecord
  └── 返回 { id, ... }
```

**BLE 协议格式** (13 bytes payload):

```
Byte 0-1:   控制字节 (小端)
  Bit 0:     LBS  单位=磅
  Bit 1:     JIN  单位=斤
  Bit 7:     下秤标志
  Bit 9:     阻抗有效
  Bit 10:    稳定(备选)
  Bit 13:    稳定(主标志)
Byte 2-3:   年 (UTC)
Byte 4:     月
Byte 5:     日
Byte 6:     时
Byte 7:     分
Byte 8:     秒
Byte 9-10:  阻抗 (小端, Ω)
Byte 11-12: 体重 (小端, raw/200=KG)
```

---

### 5.7 Token 双写链路

```
任何需要调用云 API 的端点:
  ┌── GET /api/dashboard/data
  ├── GET /api/petkit/devices
  ├── POST /api/cloudpets/feed
  ├── 定时任务 refresh_dashboard_cache
  └── ...

调用服务方法 → service.get_devices()
  │
  ├── PetKit: _refresh_devices()
  │     ├── client.get_devices_data()
  │     ├── 成功 → _save_session_to_db()
  │     │     ├── 提取 cookies + auth_headers
  │     │     ├── systemconfig(key='token') 写入/更新
  │     │     └── self._memory_session = session_data
  │     └── 401 → _login() → 重试
  │
  └── CloudPets: _request()
        ├── 正常 → 返回响应
        ├── 401 → _login() → _save_token_to_db()
        │     ├── systemconfig(key='token') 写入/更新
        │     └── self._memory_token = token
        └── 重试请求

调用完毕时的双写动作:
  │
  ├── [上层] 提取 _memory_session / _memory_token
  │     └── update_cloud_device_token(uid, platform, token, device_name)
  │           └── systemconfig(key='token', platform, device_name) 写入/更新
  │
  └── [上层] device_cache.invalidate_user(uid)
        └── 下次 get_user_platforms() 惰性重建，is_complete=True
```

**双写路径对比**:

| 写入路径 | 存储位置 | key | 触发时机 | 调用者 |
|----------|----------|-----|----------|--------|
| 服务层 | `systemconfig` | `token` | 每次登录/刷新 | 服务层 `_save_session_to_db()` |
| 上层 | `systemconfig` | `token` (in device config group) | Dashboard/定时任务 | `update_cloud_device_token()` |
| 服务层内存 | `_memory_session` / `_memory_token` | — | 每次写入 DB 后 | 服务层自己 |
| DeviceCache | `{uid: {platform: DeviceRecord}}` | — | `invalidate_user` 后惰性重建 | 上层 |

---

### 5.8 设备分享链路

```
创建分享:
  POST /api/share/create { from_user_id, device_keys: ["cloudpets_cloudpets"] }
  ├── 生成 SHA256 share_token
  ├── INSERT device_share (status='pending', expires_at=24h)
  └── 返回 { share_token, share_link, expires_at }

接受分享:
  POST /api/share/accept { share_token, to_user_id }
  ├── SELECT device_share WHERE token AND status='pending'
  ├── 校验: 未过期, 非自己分享给自己
  ├── 从 device_keys 解析平台: ["cloudpets"] → {'cloudpets'}
  ├── 读取分享者的原始凭据:
  │     SELECT FROM systemconfig WHERE user_id=from_user_id AND platform IN ('cloudpets') AND is_active=1
  ├── 生成 shared_account = "{原账号}_shared_{to_user_id}"
  ├── INSERT shared_device_config (share_id, to_user_id, platform, config_account, config_password)
  ├── UPDATE device_share (status='accepted', to_user_id)
  ├── [缓存] device_cache.invalidate_user(to_user_id)
  └── 返回 { success: true, configured: [...] }

查询分享:
  GET /api/share/list?user_id=1&role=from|to

撤销分享:
  POST /api/share/revoke?share_id=1&user_id=1
  ├── UPDATE device_share SET status='revoked'
  ├── [缓存] device_cache.invalidate_user(share.to_user_id)
  └── 返回 { success: true }

Dashboard 消费共享数据:
  _get_shared_platform_credentials(uid)
  ├── SELECT FROM shared_device_config WHERE to_user_id=uid
  ├── 对每个平台, 查询分享者的原始凭据:
  │     SELECT FROM systemconfig WHERE user_id=from_user_id AND platform=X AND key IN ('account','password') AND is_active=1
  └── 返回 { 'petkit': {account, password}, 'cloudpets': {...} }
```

---

### 5.9 登录绑定冲突链路

```
POST /api/auth/bind { account, password, code }
  │
  ├── wx_code2session(code) → openid
  ├── 查找/创建用户 (phone_number)
  │
  ├── 冲突检测:
  │     SELECT user WHERE openid=? AND id!=user.id
  │     ├── old_user exists → has_conflict = True
  │     └── old_user not exists → 正常绑定
  │
  ├── has_conflict AND not force_bind AND not skip_bind
  │     └── HTTP 409 {
  │           "detail": {
  │             "code": "DEVICE_BOUND",
  │             "message": "当前设备已绑定其他账号",
  │             "bound_user": { "phone_masked": "180****1234", "nickname": "用户1234" }
  │           }
  │         }
  │
  │  前端接收到 409:
  │    wx.showModal({
  │      title: '设备已绑定账号',
  │      content: '当前设备已绑定账号 180****1234，是否改绑为当前账号？',
  │      confirmText: '改绑', cancelText: '保留原绑定'
  │    })
  │    ├── 确认 → 重新 wx.login() → POST bind { ..., force_bind: true }
  │    │         → old_user.openid = None → user.openid = new_openid
  │    └── 拒绝 → 重新 wx.login() → POST bind { ..., skip_bind: true }
  │              → 不更新 user.openid → 下次静默登录仍用旧账号
  │
  └── 正常绑定:
        ├── user.openid = openid (唯一约束)
        ├── user.session_key = new_session_key
        └── 签发 token
```

**状态流转**:

```
设备 A 绑定账号 a
    │
    ├── 用户输入账号 b 登录
    │     ├── force_bind=false, skip_bind=false → 409 弹窗
    │     ├── 确认改绑 → 账号 a 失去设备 A, 账号 b 获得设备 A
    │     └── 拒绝改绑 → 账号 b 登录但不绑定, 设备 A 仍属 a
    │
    ├── 用户下拉静默登录 → openid 匹配 a → 直接进入 a
    │
    └── 账号 a 退出 → preventSilentLogin=true → 下次需输密码
```

---

## 6. 前端模块详解

### 6.1 登录页模式切换（5 种模式各阶段 UI 状态）

| 模式 | 枚举值 | UI 元素 | 触发条件 | 交互逻辑 |
|------|--------|---------|----------|----------|
| **静默登录** | `LOADING` | 居中 loading 动画 + "登录中…" 文字 | 页面首次加载，`mode` 参数为空 | 后台调用 `wx.login()` → `POST /api/auth/silent-login` → 成功跳首页，失败 401 则根据 `lastLogoutPhone` 分流 |
| **首次注册** | `PHONE_ONLY` | 手机号输入框 + 密码输入框 + 协议勾选 + "注册"按钮 | 静默登录失败且本地无退出记录 | 用户输入手机号+密码 → 按"注册" → `_doBindLogin()` → 成功跳首页；失败弹提示 |
| **登录选择** | `SELECT` | "本机密码登录"按钮 + "其他手机号登录"按钮 + 下拉静默登录提示 | 用户主动退出后进入 | 显示上次退出手机号的掩码；点击"本机"→`OWN_PASSWORD`，点击"其他"→`BIND`，下拉→再次静默登录 |
| **本机输入** | `OWN_PASSWORD` | 手机号(自动填充/可编辑) + 密码输入框 + "登录"按钮 + 返回按钮 | 用户在 SELECT 页点击"本机密码登录" | 手机号自动填充退出前号码；点击"登录"→`_doBindLogin({preventSilent:true})` |
| **绑定输入** | `BIND` | 手机号输入框 + 密码输入框 + "绑定"按钮 + 返回按钮 | 用户在 SELECT 页点击"其他手机号登录" | 点击"绑定"→`_doBindLogin({preventSilent:false, clearLogoutPhone:true})` |

```
流程:

onLoad(query)
  ├── fromLogout=1 → 直接 showLoginSelect (SELECT 模式)
  ├── mode=own_password → startOwnPasswordMode (OWN_PASSWORD 模式, 自动填手机号)
  ├── mode=phone_only → startPhoneOnlyMode (PHONE_ONLY 模式)
  └── 默认 → performSilentLoginCheck (LOADING 模式)
        ├── 成功 → onLoginSuccess (跳首页)
        ├── 401 UNBOUND:
        │     ├── 有 lastLogoutPhone → showLoginSelect (SELECT 模式)
        │     └── 无 lastLogoutPhone → startPhoneOnlyMode (PHONE_ONLY 模式)
        └── 网络错误:
              ├── 有 lastLogoutPhone → SELECT + 错误提示
              └── 无 lastLogoutPhone → PHONE_ONLY

每个模式提交时统一走 _doBindLogin():
  → POST /api/auth/bind
  ├── 200 → onLoginSuccess (保存 token+userInfo, 跳首页)
  └── 409 DEVICE_BOUND → wx.showModal 改绑确认弹窗
        ├── 确认 → 重新 wx.login + force_bind:true
        └── 拒绝 → 重新 wx.login + skip_bind:true
```

### 6.2 首页设备卡片渲染

```
loadUserDevices()
  │
  ├── fetchDashboardData(userId)
  │     └── 返回 dashboardData { device_platforms, petkit_devices, ... }
  │
  ├── 遍历 device_platforms:
  │     ├── is_ble=true → scale card → healthDevices[]
  │     ├── platform='cloudpets' → feeder card → petDevices[]
  │     └── platform='petkit' → litterbox card → petDevices[]
  │
  └── setData({ userDevices, petDevices, healthDevices })

WXML 渲染:
  <view wx:if="{{userDevices.length === 0}}"> 空状态 </view>
  <scroll-view wx:else>
    <view wx:if="{{petDevices.length > 0}}"> 宠物生活 </view>
      <navigator wx:for="{{petDevices}}" wx:key="device_key">
        <navigator wx:if="{{item.device_type === 'feeder'}}"> 喂食机卡片 </navigator>
        <navigator wx:if="{{item.device_type === 'litterbox'}}"> 猫厕所卡片 </navigator>
    <view wx:if="{{healthDevices.length > 0}}"> 健康与体质 </view>
      <navigator wx:for="{{healthDevices}}" wx:key="device_key">
        <navigator wx:if="{{item.device_type === 'scale'}}"> 体脂秤卡片 </navigator>
```

### 6.3 蓝牙扫描绑定流程

```
用户点击"体脂秤"
  ├── showScalePermissionDialog → 引导说明弹窗
  ├── 点击"开始绑定" → authorizeAndScan()
  │     ├── checkAndInitBluetooth()       ← Promise 化
  │     ├── initBluetoothManager()
  │     │     ├── wx.openBluetoothAdapter()
  │     │     ├── wx.onBluetoothDeviceFound(handleDeviceFound)
  │     │     └── startContinuousScan()     ← 持续扫描
  │     └── 成功 → startScaleScan()
  │
  ├── startScaleScan()
  │     ├── app.clearDiscoveredDevices()
  │     ├── globalData.suppressScaleAutoNavigate = true  ← 抑制跳转称重页
  │     ├── subscribeDeviceDiscovery(devices => {
  │     │     _filterDiscoveredDevices(devices)
  │     │       ├── GET /api/devices/scale/bound (5s 缓存)
  │     │       ├── 剔除已绑定, 按信号排序
  │     │       └── setData({ scaleDiscoveredDevices })
  │     └── 10s 超时自动停止
  │
  └── 用户点击设备列表中的一项 → confirmBindScale(e)
        ├── isDuplicate check → 重复则拦截
        ├── POST /api/devices/scale/bind
        ├── initScaleSelfMember()
        ├── cleanupScaleScan()
        │     ├── unsubscribeDeviceDiscovery()
        │     └── suppressScaleAutoNavigate = false
        └── loadUserDevices()  ← 刷新首页
```

---

## 7. 异常处理矩阵

### 7.1 网络层

| 场景 | 前端 | 后端 |
|------|------|------|
| 网络不可达 | `cloud_request.js` 自动重试 1 次后弹错 | — |
| 请求超时 (15s) | `fetchDashboardData` 超时保护, 弹"请求超时" | FastAPI 超时中间件 |
| 401 未授权 | 跳转登录页 | `get_current_user` 拒绝 |
| 后端 5xx | 弹"服务器错误" | 日志 `ERROR` |

### 7.2 数据库层

| 场景 | 处理方式 |
|------|----------|
| 连接失败 | SQLModel 异常向上传播, 统一 500 返回 |
| 唯一约束冲突 | `User.phone_number`/`openid`/`token_hash` 重复 → 400/409 |
| 外键约束 | `ON DELETE CASCADE` / `ON DELETE SET NULL` 自动处理 |
| 慢查询 | 批量查询 `get_configs_batch` 减少次数 |

### 7.3 服务层

| 场景 | PetKit | CloudPets |
|------|--------|-----------|
| 登录失败 | 返回 False, `add_device_api` 报 400 | 同左 |
| SSL 错误 | 自动降级(ssl=False)重试 | 无 SSL 问题 |
| Session 过期 | 自动 `_login()` 重试 | `_request()` 检测 401 后自动重登录 |
| API 调用失败 | `_refresh_devices` 捕获异常, 日志警告 | `_request` 重试 2 次后抛出 |
| 多次重试失败 | `max_retries=3` 后返回 False | `LOGIN_MAX_RETRIES=3` 后返回 False |

### 7.4 缓存层（仅 DeviceCache + 服务层内存）

| 场景 | DeviceCache | 服务层内存缓存 |
|------|-------------|---------------|
| 缓存未命中 | `get_user_platforms()` 触发 `_load_user_from_db()` 惰性加载 | `_load_session_from_db()` / `_load_token_from_db()` 两级读取 |
| 缓存过期 | 无 TTL, 主动 `invalidate_user()` 后惰性重建 | `SESSION_EXPIRY_MS=30min` / `SESSION_REFRESH_THRESHOLD=25min` |
| 并发写入 | 单线程 asyncio, `_devices` 字典操作原子 | 各 Service 自管, 无竞争 |
| 数据一致性 | `invalidate_user()` + `update_cloud_device_token()` 保证 | 每次登录/刷新同步写 DB + 更新内存 |

### 7.5 业务层

| 场景 | 处理方式 |
|------|----------|
| 体脂秤重复绑定 | 409 "无法重复添加同一蓝牙设备" |
| 一用户多个体脂秤 | 409 "已绑定体脂秤，请先删除" |
| 设备绑定冲突 | 409 + 前端弹窗确认 (force_bind/skip_bind) |
| 添加设备凭据无效 | 400 "登录失败：账号或密码错误" |
| 家庭成员缺失身高/年龄 | 前端自动打开编辑弹窗 |
| 体脂计算缺少阻抗 | 使用 BMI 估算公式降级计算 |

---

## 8. 附录：完整 API 清单

### 8.1 认证 API (5 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/bind` | 账密登录 + OpenID 绑定 (含冲突检测) |
| POST | `/api/auth/silent-login` | 静默免密登录 |
| GET | `/api/auth/check-phone` | 查询手机号是否已注册 |
| POST | `/api/auth/change-password` | 修改密码 |
| POST | `/api/auth/delete-account` | 注销账号 |
| GET | `/api/auth/check-config` | 检查用户设备配置状态 |

### 8.2 Dashboard API (1 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/data` | 聚合仪表盘数据 (设备列表+统计) |

### 8.3 设备管理 API (4 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/devices/add` | 添加云设备 (validate + persist + cache flush) |
| DELETE | `/api/devices/{device_key}` | 删除设备 (软删除) |
| POST | `/api/devices/scale/bind` | 绑定 BLE 体脂秤 |
| GET | `/api/devices/scale/bound` | 查询已绑体脂秤 |

### 8.4 PetKit API (6 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/petkit/devices` | 设备列表 |
| POST | `/api/petkit/clean` | 触发清理 |
| POST | `/api/petkit/deodorize` | 触发除臭 |
| GET | `/api/petkit/stats` | 今日统计 |
| GET | `/api/petkit/history` | 历史统计 (7天) |
| GET | `/api/petkit/devices-stats` | 设备+统计合并 |

### 8.5 CloudPets API (7 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cloudpets/servings_today` | 今日已出粮 |
| POST | `/api/cloudpets/feed` | 手动喂食 |
| GET | `/api/cloudpets/plans` | 获取喂食计划 |
| POST | `/api/cloudpets/plans` | 新增喂食计划 |
| PUT | `/api/cloudpets/plans/{plan_id}` | 修改喂食计划 |
| DELETE | `/api/cloudpets/plans/{plan_id}` | 删除喂食计划 |
| GET | `/api/cloudpets/feeder/status` | 喂食器实时状态 |

### 8.6 家庭成员 API (5 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/family-members` | 获取成员列表 |
| POST | `/api/family-members` | 添加成员 |
| PUT | `/api/family-members/{member_id}` | 更新成员 |
| DELETE | `/api/family-members/{member_id}` | 软删除成员 |
| GET | `/api/family-members/{member_id}/history` | 成员体重历史 |

### 8.7 体脂秤 API (3 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/scale/measurements` | 创建测量记录 (时段去重) |
| GET | `/api/scale/members` | 获取体脂秤成员 (含最近体重) |
| PUT | `/api/scale/members/{member_id}` | 更新体脂秤成员 |

### 8.8 系统配置 API (2 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/config` | 获取系统配置 (密码掩码) |
| POST | `/api/system/config` | 保存系统配置 |

### 8.9 分享 API (4 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/share/create` | 创建分享 |
| POST | `/api/share/accept` | 接受分享 |
| GET | `/api/share/list` | 查询分享记录 |
| POST | `/api/share/revoke` | 撤销分享 |

### 8.10 静态页面 (5 个)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 首页 |
| GET | `/litterbox` | 猫厕所页面 |
| GET | `/feeder` | 喂食机页面 |
| GET | `/feeder/plans` | 喂食计划页面 |
| GET | `/scale` | 体脂秤页面 |
