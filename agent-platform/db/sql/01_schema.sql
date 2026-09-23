-- SUIG-10 智能体中台 · MySQL 初始化 DDL
-- 权威表结构（与 db/models/entities.py 的 ORM 定义一致）。
-- 由 deploy/docker-compose.yml 挂载到 /docker-entrypoint-initdb.d/ 首次启动执行。
--
-- 铁律：MySQL 只存业务元数据，不存大文本与向量。

CREATE DATABASE IF NOT EXISTS `agent_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `agent_platform`;

-- ---------------------------------------------------------------------------
-- 1. 用户
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `user` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(64)     NOT NULL,
  `api_key`    VARCHAR(64)     NOT NULL COMMENT '只存哈希，不存明文',
  `status`     TINYINT         NOT NULL DEFAULT 1 COMMENT '1正常 0停用',
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_api_key` (`api_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户';

-- ---------------------------------------------------------------------------
-- 2. 知识库（owner_id 是权限校验的唯一依据）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `knowledge_base` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(128)    NOT NULL,
  `owner_id`   BIGINT UNSIGNED NOT NULL,
  `status`     TINYINT         NOT NULL DEFAULT 1 COMMENT '1启用 0归档',
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_kb_owner` (`owner_id`),
  CONSTRAINT `fk_kb_owner` FOREIGN KEY (`owner_id`) REFERENCES `user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库';

-- ---------------------------------------------------------------------------
-- 3. 文档（status 是双写一致性的 source of truth）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `document` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `kb_id`       BIGINT UNSIGNED NOT NULL,
  `file_name`   VARCHAR(255)    NOT NULL,
  `file_hash`   CHAR(64)        NOT NULL COMMENT 'sha256：同库重传去重 + 摄入幂等',
  `status`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0未处理 1处理中 2完成 3失败',
  `chunk_count` INT UNSIGNED    NOT NULL DEFAULT 0,
  `error_msg`   VARCHAR(1024)   DEFAULT NULL,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_kb_hash` (`kb_id`, `file_hash`),
  KEY `idx_doc_kb_status` (`kb_id`, `status`),
  CONSTRAINT `fk_doc_kb` FOREIGN KEY (`kb_id`) REFERENCES `knowledge_base` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档元信息';

-- ---------------------------------------------------------------------------
-- 4. 会话（thread_id 是 LangGraph RedisSaver 的寻址键）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `conversation` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id`    BIGINT UNSIGNED NOT NULL,
  `kb_id`      BIGINT UNSIGNED DEFAULT NULL,
  `thread_id`  VARCHAR(64)     NOT NULL COMMENT '多实例共享状态的寻址键',
  `status`     VARCHAR(16)     NOT NULL DEFAULT 'active',
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_conv_user` (`user_id`),
  KEY `idx_conv_thread` (`thread_id`),
  CONSTRAINT `fk_conv_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`),
  CONSTRAINT `fk_conv_kb`   FOREIGN KEY (`kb_id`)   REFERENCES `knowledge_base` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话会话';

-- ---------------------------------------------------------------------------
-- 5. 消息元数据（正文不入库，只留摘要 + 命中切片引用）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `message` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `conv_id`      BIGINT UNSIGNED NOT NULL,
  `role`         VARCHAR(16)     NOT NULL COMMENT 'user/assistant/tool',
  `msg_type`     VARCHAR(16)     NOT NULL DEFAULT 'text',
  `es_chunk_ref` JSON            DEFAULT NULL COMMENT '命中切片 chunk_id 列表',
  `token_count`  INT UNSIGNED    NOT NULL DEFAULT 0,
  `latency_ms`   INT UNSIGNED    NOT NULL DEFAULT 0,
  `created_at`   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_msg_conv` (`conv_id`),
  CONSTRAINT `fk_msg_conv` FOREIGN KEY (`conv_id`) REFERENCES `conversation` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消息元数据';

-- ---------------------------------------------------------------------------
-- 6. Agent 配置（支持版本字段，为灰度预留）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent_config` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `kb_id`       BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(128)    NOT NULL,
  `graph_type`  VARCHAR(64)     NOT NULL COMMENT 'rag_graph/tool_graph/...',
  `temperature` FLOAT           NOT NULL DEFAULT 0.7,
  `top_k`       INT             NOT NULL DEFAULT 5,
  `conf`        JSON            NOT NULL,
  `version`     INT             NOT NULL DEFAULT 1,
  `status`      TINYINT         NOT NULL DEFAULT 1,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_agent_kb` (`kb_id`),
  CONSTRAINT `fk_agent_kb` FOREIGN KEY (`kb_id`) REFERENCES `knowledge_base` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 配置';
