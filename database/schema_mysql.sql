CREATE DATABASE IF NOT EXISTS auto_home DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE auto_home;

CREATE TABLE IF NOT EXISTS user (
  id INT PRIMARY KEY AUTO_INCREMENT,
  phone_number VARCHAR(20) NOT NULL UNIQUE,
  nickname VARCHAR(100) DEFAULT '新用户',
  gender VARCHAR(10) DEFAULT 'male',
  age INT DEFAULT 25,
  height INT DEFAULT 175,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS systemconfig (
  id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  `key` VARCHAR(50) NOT NULL,
  value TEXT NOT NULL,
  platform VARCHAR(50) DEFAULT 'global',
  device_name VARCHAR(100) DEFAULT '',
  is_encrypted TINYINT(1) DEFAULT 0,
  updated_at BIGINT NOT NULL,
  INDEX idx_user_key (user_id, `key`),
  INDEX idx_platform (platform),
  CONSTRAINT fk_config_user FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS weightrecord (
  id INT PRIMARY KEY AUTO_INCREMENT,
  user_id INT NOT NULL,
  weight DECIMAL(5,2) NOT NULL,
  impedance INT NULL,
  bmi DECIMAL(5,2) NULL,
  body_fat DECIMAL(5,2) NULL,
  muscle DECIMAL(5,2) NULL,
  water DECIMAL(5,2) NULL,
  visceral_fat DECIMAL(5,2) NULL,
  bone_mass DECIMAL(5,2) NULL,
  bmr DECIMAL(8,2) NULL,
  timestamp BIGINT NOT NULL,
  xiaomi_pushed TINYINT(1) DEFAULT 0,
  xiaomi_push_time TIMESTAMP NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user_id (user_id),
  INDEX idx_timestamp (timestamp DESC),
  CONSTRAINT fk_weight_user FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);
