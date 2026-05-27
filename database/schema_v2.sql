-- ============================================================
-- AutoHome 智能家居系统 - 数据库架构 v2
-- 核心变更：
--   1. user 表新增 token_hash / token_expires_at（替换JWT）
--   2. systemconfig 保留KV结构，service_credentials统一用key前缀约定
--   3. 添加外键约束保障数据完整性
--   4. device_share 添加共享者openid字段供openid级联查询
-- ============================================================

-- ============================================================
-- 0. 清空旧表（注意顺序：先删有外键引用的表）
-- ============================================================
DROP TABLE IF EXISTS `shared_device_config`;
DROP TABLE IF EXISTS `device_share`;
DROP TABLE IF EXISTS `weightrecord`;
DROP TABLE IF EXISTS `family_member`;
DROP TABLE IF EXISTS `systemconfig`;
DROP TABLE IF EXISTS `user`;


-- ============================================================
-- 1. 用户表
-- ============================================================
CREATE TABLE `user` (
    `id`               INT AUTO_INCREMENT PRIMARY KEY,
    `phone_number`     VARCHAR(20) NOT NULL,
    `password_hash`    VARCHAR(256) DEFAULT NULL COMMENT 'bcrypt哈希',
    `openid`           VARCHAR(64) DEFAULT NULL COMMENT '微信OpenID',
    `unionid`          VARCHAR(64) DEFAULT NULL,
    `session_key`      VARCHAR(64) DEFAULT NULL COMMENT '微信临时密钥',
    `nickname`         VARCHAR(100) DEFAULT '',

    -- 鉴权字段（替换JWT）
    `token_hash`       VARCHAR(64) DEFAULT NULL COMMENT '登录token的SHA256哈希',
    `token_expires_at` BIGINT      DEFAULT NULL COMMENT 'token过期时间戳(毫秒)',

    `privacy_consent_at` BIGINT DEFAULT NULL,
    `created_at`         BIGINT NOT NULL,
    `updated_at`         BIGINT NOT NULL,

    UNIQUE KEY `uk_phone` (`phone_number`),
    UNIQUE KEY `uk_openid` (`openid`),
    UNIQUE KEY `uk_token_hash` (`token_hash`) COMMENT '加速token查询',
    INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';


-- ============================================================
-- 2. 系统配置表（保留KV结构）
--    user_id=0 视为全局配置
--    key命名约定：
--      - account/password:        用户设备凭证
--      - {platform}_session_data:  第三方平台登录session缓存
--      - {platform}_token:         第三方平台token缓存
--      - app_version / 其他:       全局配置
-- ============================================================
CREATE TABLE `systemconfig` (
    `id`           INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`      INT          NOT NULL DEFAULT 0 COMMENT '0=全局',
    `key`          VARCHAR(50)  NOT NULL COMMENT '配置键',
    `value`        TEXT         NOT NULL COMMENT '配置值(加密或明文)',
    `platform`     VARCHAR(32)  DEFAULT NULL COMMENT 'petkit/cloudpets/xiaomi',
    `device_name`  VARCHAR(100) DEFAULT NULL,
    `is_encrypted` TINYINT(1)   NOT NULL DEFAULT 0,
    `is_active`    TINYINT(1)   NOT NULL DEFAULT 1,
    `updated_at`   BIGINT       NOT NULL,

    INDEX `idx_user_key` (`user_id`, `key`(30)),
    INDEX `idx_user_platform_active` (`user_id`, `platform`, `is_active`)

    -- 注意：user_id=0 用作全局配置，因此不建立外键约束
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置表（KV模式，user_id=0为全局配置）';


-- ============================================================
-- 3. 家庭成员表
-- ============================================================
CREATE TABLE `family_member` (
    `id`            INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`       INT          NOT NULL,
    `name`          VARCHAR(50)  NOT NULL,
    `gender`        VARCHAR(10)  DEFAULT '',
    `age`           INT          DEFAULT 0,
    `height`        DECIMAL(5,2) DEFAULT 0,
    `avatar_color`  VARCHAR(100) DEFAULT '',
    `relationship`  VARCHAR(20)  DEFAULT '' COMMENT 'self/spouse/child/parent/other',
    `sort_order`    INT          DEFAULT 0,
    `is_active`     TINYINT(1)   NOT NULL DEFAULT 1,
    `created_at`    BIGINT       NOT NULL,
    `updated_at`    BIGINT       NOT NULL,

    INDEX `idx_user_sort` (`user_id`, `sort_order`),
    INDEX `idx_user_active` (`user_id`, `is_active`),

    CONSTRAINT `fk_family_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='家庭成员表';


-- ============================================================
-- 4. 体重记录表
-- ============================================================
CREATE TABLE `weightrecord` (
    `id`              INT AUTO_INCREMENT PRIMARY KEY,
    `user_id`         INT          NOT NULL,
    `member_id`       INT          NOT NULL COMMENT '家庭成员ID',
    `weight`          DECIMAL(5,2) NOT NULL,
    `impedance`       INT          DEFAULT NULL,
    `bmi`             DECIMAL(5,2) DEFAULT NULL,
    `body_fat`        DECIMAL(5,2) DEFAULT NULL,
    `muscle`          DECIMAL(5,2) DEFAULT NULL,
    `water`           DECIMAL(5,2) DEFAULT NULL,
    `protein`         DECIMAL(5,2) DEFAULT NULL,
    `visceral_fat`    DECIMAL(5,2) DEFAULT NULL,
    `bone_mass`       DECIMAL(5,2) DEFAULT NULL,
    `bmr`             DECIMAL(8,2) DEFAULT NULL,
    `timestamp`       BIGINT       NOT NULL COMMENT '测量时间(毫秒)',
    `created_at`      BIGINT       NOT NULL,

    INDEX `idx_user_timestamp` (`user_id`, `timestamp` DESC),
    INDEX `idx_member_timestamp` (`member_id`, `timestamp` DESC),
    INDEX `idx_xiaomi_pending` (`xiaomi_pushed`, `timestamp`),

    CONSTRAINT `fk_weight_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_weight_member` FOREIGN KEY (`member_id`) REFERENCES `family_member`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='体重记录表';


-- ============================================================
-- 5. 分享记录表（新增 openid 字段支持openid级联查询）
-- ============================================================
CREATE TABLE `device_share` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `from_user_id`  INT          NOT NULL COMMENT '分享者',
    `to_user_id`    INT          DEFAULT NULL COMMENT '接受者(接受后写入)',
    `from_openid`   VARCHAR(64)  DEFAULT NULL COMMENT '分享者openid(冗余，加速查询)',
    `share_token`   VARCHAR(64)  NOT NULL COMMENT '分享令牌',
    `status`        VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/accepted/revoked',
    `device_keys`   TEXT         NOT NULL COMMENT 'JSON数组',
    `created_at`    BIGINT       NOT NULL,
    `accepted_at`   BIGINT       DEFAULT NULL,
    `expires_at`    BIGINT       NOT NULL,

    INDEX `idx_token` (`share_token`),
    INDEX `idx_from_user` (`from_user_id`),
    INDEX `idx_to_user` (`to_user_id`),
    INDEX `idx_from_openid` (`from_openid`),
    INDEX `idx_status_expires` (`status`, `expires_at`) COMMENT '加速过期清理',

    CONSTRAINT `fk_share_from` FOREIGN KEY (`from_user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_share_to` FOREIGN KEY (`to_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='设备分享记录';


-- ============================================================
-- 6. 分享配置映射表
-- ============================================================
CREATE TABLE `shared_device_config` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `share_id`        BIGINT       NOT NULL COMMENT '关联device_share.id',
    `to_user_id`      INT          NOT NULL COMMENT '接受者',
    `platform`        VARCHAR(32)  NOT NULL,
    `device_key`      VARCHAR(64)  NOT NULL,
    `config_account`  VARCHAR(128) NOT NULL COMMENT '生成的共享账号名',
    `config_password` VARCHAR(256) NOT NULL COMMENT '分享者原文密码(请确保DB访问控制)',
    `created_at`      BIGINT       NOT NULL,

    INDEX `idx_to_user_platform` (`to_user_id`, `platform`),

    CONSTRAINT `fk_shared_share` FOREIGN KEY (`share_id`) REFERENCES `device_share`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_shared_user` FOREIGN KEY (`to_user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分享设备配置映射';


-- ============================================================
-- 初始化基础数据
-- ============================================================
INSERT INTO `systemconfig` (`user_id`, `key`, `value`, `is_encrypted`, `is_active`, `updated_at`)
VALUES
    (0, 'app_version', '0.6.0', 0, 1, UNIX_TIMESTAMP() * 1000),
    (0, 'PETKIT_DISABLE_SSL_VERIFY', 'false', 0, 1, UNIX_TIMESTAMP() * 1000),
    (0, 'WECHAT_APPID', 'wxa5e716fb0093a6c1', 0, 1, UNIX_TIMESTAMP() * 1000),
    (0, 'WECHAT_SECRET', '879cf5cf628f1dd9ea5cc2ed8fac672b', 0, 1, UNIX_TIMESTAMP() * 1000),
    (0, 'TOKEN_EXPIRE_HOURS', '720', 0, 1, UNIX_TIMESTAMP() * 1000)
    ON DUPLICATE KEY UPDATE `value` = VALUES(`value`);
