-- ========================================
-- 设备分享功能 - 数据库迁移
-- ========================================

-- 1. 分享记录表：记录A分享给B的关系
-- share_token 用于小程序分享卡片携带的参数
CREATE TABLE IF NOT EXISTS `device_share` (
  `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
  `from_user_id` INT       NOT NULL COMMENT '分享者 user_id',
  `to_user_id`   INT       DEFAULT NULL COMMENT '接受者 user_id（接受后写入）',
  `share_token`  VARCHAR(64) NOT NULL COMMENT '分享令牌（卡片携带参数）',
  `status`       VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending / accepted / revoked',
  `device_keys`  TEXT       NOT NULL COMMENT '分享的设备 key 列表（JSON数组）',
  `created_at`   BIGINT    NOT NULL,
  `accepted_at`  BIGINT    DEFAULT NULL,
  `expires_at`   BIGINT    NOT NULL COMMENT '过期时间戳（24h后过期）',
  INDEX `idx_token` (`share_token`),
  INDEX `idx_from_user` (`from_user_id`),
  INDEX `idx_to_user` (`to_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备分享记录';

-- 2. 分享配置映射表：B 接受分享后，记录 B 使用的凭证
-- 每次接受分享，在此生成一份 B 的 systemconfig 凭证
CREATE TABLE IF NOT EXISTS `shared_device_config` (
  `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
  `share_id`        BIGINT    NOT NULL COMMENT '关联 device_share.id',
  `to_user_id`      INT       NOT NULL COMMENT '接受者 user_id',
  `platform`        VARCHAR(32) NOT NULL COMMENT '平台：cloudpets/petkit/xiaomi',
  `device_key`      VARCHAR(64) NOT NULL COMMENT '设备唯一标识',
  `config_account`  VARCHAR(128) NOT NULL COMMENT '自动生成的账号',
  `config_password` VARCHAR(256) NOT NULL COMMENT '自动生成的密码',
  `created_at`      BIGINT    NOT NULL,
  INDEX `idx_to_user_platform` (`to_user_id`, `platform`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分享设备配置映射';
