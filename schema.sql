-- 왈가(walga) DB 스키마. 실행: mysql -u root -p < schema.sql
CREATE DATABASE IF NOT EXISTS walga CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE walga;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  nickname VARCHAR(20) NOT NULL UNIQUE,
  pw_hash VARCHAR(255) NOT NULL,
  points INT NOT NULL DEFAULT 0,
  streak INT NOT NULL DEFAULT 0,
  last_vote_date DATE NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS issues (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(200) NOT NULL,
  summary TEXT,
  category VARCHAR(20),
  is_main TINYINT NOT NULL DEFAULT 0,          -- 오늘의 왈가
  opt_yes VARCHAR(30) NOT NULL DEFAULT '찬성',
  opt_no VARCHAR(30) NOT NULL DEFAULT '반대',
  open_date DATE NOT NULL,
  closed TINYINT NOT NULL DEFAULT 0,
  yes_cnt INT NOT NULL DEFAULT 0,
  no_cnt INT NOT NULL DEFAULT 0,
  view_cnt INT NOT NULL DEFAULT 0,
  slug VARCHAR(200),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_open (open_date, closed)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS votes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  issue_id INT NOT NULL,
  user_id INT NULL,
  anon_hash VARCHAR(64) NULL,
  choice TINYINT NOT NULL,                      -- 1=yes 0=no
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user (issue_id, user_id),
  UNIQUE KEY uq_anon (issue_id, anon_hash),
  FOREIGN KEY (issue_id) REFERENCES issues(id)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS posts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  board VARCHAR(20) NOT NULL,             -- config.BOARDS 키,
  user_id INT NULL,
  anon_nick VARCHAR(20) NULL,
  anon_pw_hash VARCHAR(255) NULL,
  title VARCHAR(120) NOT NULL,
  content MEDIUMTEXT NOT NULL,
  has_image TINYINT NOT NULL DEFAULT 0,
  rec_cnt INT NOT NULL DEFAULT 0,
  cmt_cnt INT NOT NULL DEFAULT 0,
  view_cnt INT NOT NULL DEFAULT 0,
  is_best TINYINT NOT NULL DEFAULT 0,
  best_at DATETIME NULL,
  status TINYINT NOT NULL DEFAULT 1,           -- 0=블라인드
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_board (board, status, created_at),
  INDEX idx_best (is_best, best_at)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS comments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  target ENUM('post','issue') NOT NULL,
  target_id INT NOT NULL,
  parent_id INT NULL,
  user_id INT NULL,
  anon_nick VARCHAR(20) NULL,
  anon_pw_hash VARCHAR(255) NULL,
  content TEXT NOT NULL,
  rec_cnt INT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_target (target, target_id, created_at)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS recommends (
  id INT AUTO_INCREMENT PRIMARY KEY,
  target ENUM('post','comment') NOT NULL,
  target_id INT NOT NULL,
  user_id INT NULL,
  anon_hash VARCHAR(64) NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user (target, target_id, user_id),
  UNIQUE KEY uq_anon (target, target_id, anon_hash)
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS reports (
  id INT AUTO_INCREMENT PRIMARY KEY,
  target ENUM('post','comment') NOT NULL,
  target_id INT NOT NULL,
  reason VARCHAR(50),
  reporter_hash VARCHAR(64) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_reporter (target, target_id, reporter_hash)   -- 1인 1신고
) CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS attendance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  date DATE NOT NULL,
  UNIQUE KEY uq_day (user_id, date),
  FOREIGN KEY (user_id) REFERENCES users(id)
) CHARACTER SET utf8mb4;
