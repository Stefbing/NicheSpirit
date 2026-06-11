-- ============================================================
-- AutoHome 智能家居系统 - 终极融合生产架构 v2.1 (MySQL 适配版)
-- ============================================================

SET
FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS `shared_device_config`;
DROP TABLE IF EXISTS `device_share`;
DROP TABLE IF EXISTS `weightrecord`;
DROP TABLE IF EXISTS `family_member`;
DROP TABLE IF EXISTS `systemconfig`;
DROP TABLE IF EXISTS `user`;
SET
FOREIGN_KEY_CHECKS = 1;

-- 1. 用户表
CREATE TABLE `user`
(
    `id`                 INT AUTO_INCREMENT PRIMARY KEY,
    `phone_number`       VARCHAR(20) NOT NULL,
    `password_hash`      VARCHAR(256) DEFAULT NULL COMMENT 'bcrypt哈希',
    `openid`             VARCHAR(64)  DEFAULT NULL COMMENT '微信OpenID',
    `unionid`            VARCHAR(64)  DEFAULT NULL,
    `session_key`        VARCHAR(64)  DEFAULT NULL COMMENT '微信临时密钥',
    `nickname`           VARCHAR(100) DEFAULT '',
    `token_hash`         VARCHAR(64)  DEFAULT NULL COMMENT '登录token的SHA256哈希',
    `token_expires_at`   BIGINT       DEFAULT NULL COMMENT 'token过期时间戳(毫秒)',
    `privacy_consent_at` BIGINT       DEFAULT NULL,
    `created_at`         BIGINT      NOT NULL,
    `updated_at`         BIGINT      NOT NULL,
    UNIQUE KEY `uk_phone` (`phone_number`),
    UNIQUE KEY `uk_openid` (`openid`),
    UNIQUE KEY `uk_token_hash` (`token_hash`) COMMENT '加速token查询'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- 2. 系统配置表 (KV存储)
CREATE TABLE `systemconfig`
(
    `id`           INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`      INT         NOT NULL DEFAULT 0 COMMENT '0=全局',
    `key`          VARCHAR(50) NOT NULL COMMENT '配置键',
    `value`        TEXT        NOT NULL COMMENT '配置值(加密或明文)',
    `platform`     VARCHAR(32)          DEFAULT NULL COMMENT 'petkit/cloudpets/xiaomi',
    `device_name`  VARCHAR(100)         DEFAULT NULL,
    `is_encrypted` TINYINT(1)   NOT NULL DEFAULT 0,
    `is_active`    TINYINT(1)   NOT NULL DEFAULT 1,
    `updated_at`   BIGINT      NOT NULL,
    INDEX          `idx_user_key` (`user_id`, `key`(30)),
    INDEX          `idx_user_platform_key_active` (`user_id`, `platform`, `key`(30), `is_active`),
    INDEX          `idx_platform_key` (`platform`, `key`(30)) COMMENT '用于_get_first_user_with_platform等跨用户查询'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置表';

-- 3. 家庭成员表
CREATE TABLE `family_member`
(
    `id`           INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`      INT         NOT NULL,
    `name`         VARCHAR(50) NOT NULL,
    `gender`       VARCHAR(10)   DEFAULT '',
    `age`          INT           DEFAULT 0,
    `height`       DECIMAL(5, 2) DEFAULT 0,
    `avatar_color` VARCHAR(100)  DEFAULT '',
    `relationship` VARCHAR(20)   DEFAULT '' COMMENT 'self/spouse/child/parent/other',
    `sort_order`   INT           DEFAULT 0,
    `is_active`    TINYINT(1)   NOT NULL DEFAULT 1,
    `created_at`   BIGINT      NOT NULL,
    `updated_at`   BIGINT      NOT NULL,
    INDEX          `idx_user_active_sort` (`user_id`, `is_active`, `sort_order`) COMMENT '合并原idx_user_sort+idx_user_active',
    CONSTRAINT `fk_family_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='家庭成员表';

-- 4. 体重记录表 (高频查询进行针对性降序索引优化)
CREATE TABLE `weightrecord`
(
    `id`           INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`      INT           NOT NULL,
    `member_id`    INT           NOT NULL COMMENT '家庭成员ID',
    `weight`       DECIMAL(5, 2) NOT NULL,
    `impedance`    INT           DEFAULT NULL,
    `bmi`          DECIMAL(5, 2) DEFAULT NULL,
    `body_fat`     DECIMAL(5, 2) DEFAULT NULL,
    `muscle`       DECIMAL(5, 2) DEFAULT NULL,
    `water`        DECIMAL(5, 2) DEFAULT NULL,
    `protein`      DECIMAL(5, 2) DEFAULT NULL,
    `visceral_fat` DECIMAL(5, 2) DEFAULT NULL,
    `bone_mass`    DECIMAL(5, 2) DEFAULT NULL,
    `bmr`          DECIMAL(8, 2) DEFAULT NULL,
    `timestamp`    BIGINT        NOT NULL COMMENT '测量时间(毫秒)',
    `created_at`   BIGINT        NOT NULL,
    INDEX          `idx_user_ts_bodyfat` (`user_id`, `timestamp` DESC, `body_fat`) COMMENT '覆盖Dashboard统计+最新体脂子查询',
    INDEX          `idx_member_timestamp` (`member_id`, `timestamp` DESC),
    CONSTRAINT `fk_weight_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_weight_member` FOREIGN KEY (`member_id`) REFERENCES `family_member` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='体重记录表';

-- 5. 设备分享记录表
CREATE TABLE `device_share`
(
    `id`           BIGINT AUTO_INCREMENT PRIMARY KEY,
    `from_user_id` INT         NOT NULL COMMENT '分享者',
    `to_user_id`   INT                  DEFAULT NULL COMMENT '接受者(接受后写入)',
    `share_token`  VARCHAR(64) NOT NULL COMMENT '分享令牌',
    `status`       VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/accepted/revoked',
    `device_keys`  TEXT        NOT NULL COMMENT 'JSON数组',
    `created_at`   BIGINT      NOT NULL,
    `accepted_at`  BIGINT               DEFAULT NULL,
    `expires_at`   BIGINT      NOT NULL,
    INDEX          `idx_token` (`share_token`),
    INDEX          `idx_from_user_status` (`from_user_id`, `status`, `created_at` DESC) COMMENT '覆盖管理列表+待处理查询',
    INDEX          `idx_to_user` (`to_user_id`),
    INDEX          `idx_status_expires` (`status`, `expires_at`),
    CONSTRAINT `fk_share_from` FOREIGN KEY (`from_user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_share_to` FOREIGN KEY (`to_user_id`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='设备分享记录';

-- 6. 分享设备配置映射表
CREATE TABLE `shared_device_config`
(
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `share_id`        BIGINT       NOT NULL COMMENT '关联device_share.id',
    `to_user_id`      INT          NOT NULL COMMENT '接受者',
    `platform`        VARCHAR(32)  NOT NULL,
    `device_key`      VARCHAR(64)  NOT NULL,
    `config_account`  VARCHAR(128) NOT NULL COMMENT '生成的共享账号名',
    `config_password` VARCHAR(256) NOT NULL COMMENT '分享者原文密码(请确保DB访问控制)',
    `created_at`      BIGINT       NOT NULL,
    INDEX             `idx_to_user_platform` (`to_user_id`, `platform`),
    INDEX             `idx_share_id` (`share_id`) COMMENT 'FK级联查询优化，InnoDB不会自动为FK建索引',
    CONSTRAINT `fk_shared_share` FOREIGN KEY (`share_id`) REFERENCES `device_share` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_shared_user` FOREIGN KEY (`to_user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分享设备配置映射';

-- ============================================================
-- 7. 写入核心基础配置初始化
-- ============================================================
INSERT INTO `systemconfig` (`user_id`, `key`, `value`, `is_encrypted`, `is_active`, `updated_at`)
VALUES (0, 'app_version', '0.7.0', 0, 1, UNIX_TIMESTAMP() * 1000),
       (0, 'PETKIT_DISABLE_SSL_VERIFY', 'false', 0, 1, UNIX_TIMESTAMP() * 1000),
       (0, 'WECHAT_APPID', 'wxa5e716fb0093a6c1', 0, 1, UNIX_TIMESTAMP() * 1000),
       (0, 'WECHAT_SECRET', '879cf5cf628f1dd9ea5cc2ed8fac672b', 0, 1, UNIX_TIMESTAMP() * 1000),
       (0, 'TOKEN_EXPIRE_HOURS', '720', 0, 1, UNIX_TIMESTAMP() * 1000)
ON DUPLICATE KEY UPDATE `value` = VALUES (`value`);
