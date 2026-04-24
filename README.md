# AutoHome

根据 `ARCHITECTURE_DESIGN.md` 搭建的智能家居控制中心初版，包含：

- FastAPI 后端接口
- 米家风 Web 控制台
- MySQL 建表脚本
- 微信小程序基础骨架
- 模拟 PetKit / CloudPets / 小米云 服务层

## 目录

- `backend/app`：后端源码
- `backend/static`：静态控制台
- `database/schema_mysql.sql`：数据库建表脚本
- `miniprogram`：微信小程序骨架

## 本地启动

1. 安装依赖：`pip install -r requirements.txt`
2. 可选：复制 `.env.example` 为 `.env` 并配置 MySQL
3. 启动：`uvicorn backend.app.main:app --reload`

如果没有 MySQL，后端会默认退回到本地 SQLite 文件，方便先联调界面与接口。

## 当前实现范围

- 已实现文档列出的核心 API 路由
- 已实现配置加密、缓存管理、定时刷新、体脂指标计算
- 第三方平台当前为演示适配层，保留真实接入扩展点
- 小程序已提供页面骨架和统一请求封装
