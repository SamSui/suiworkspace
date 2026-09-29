-- SUIG-31 · P1.1 域模型收敛 V1.0（6 → 21 表增量迁移）
-- ============================================================================
-- 目标   ：在「既有 6 表」的 MySQL 库上增量收敛为 V1.0 的 21 表域模型。
-- 前提   ：库内已有 01_schema.sql 建出的 6 表（user / knowledge_base /
--          document / conversation / message / agent_config）。
-- 兼容性 ：幂等重跑不报错；既存表数据不清空；既有 ORM / 单测不破坏。
--   - `agent_config` → `agent` 演进改名（数据原样保留，`(改)` 标记）。
--   - 新增 15 表：sys_tenant / sys_user / sys_role / sys_permission /
--     agent_version / agent_tool / agent_knowledge / document_chunk /
--     model / model_provider / tool / tool_permission / agent_run /
--     agent_event / audit_log。
-- 说明   ：MySQL 8 不支持 ADD COLUMN IF NOT EXISTS，用存储过程以
--          information_schema 探测，保证幂等。
-- 计量   ：最终 5 既有保留(user/kb/doc/conv/message) + 1 演进改名(agent) +
--          15 新增 = 21 张表。
-- ============================================================================

USE `agent_platform`;

-- ---------------------------------------------------------------------------
-- 0. 工具过程（幂等辅助）
--    __ensure_column：探测列存在性后才 ALTER（ADD COLUMN 幂等）。
--    __ensure_index ：探测索引存在性后才 ADD INDEX。
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS `__SUIG31_ensure_column`;
DELIMITER $$
CREATE PROCEDURE `__SUIG31_ensure_column`(
  IN p_table VARCHAR(64),
  IN p_column VARCHAR(64),
  IN p_ddl    VARCHAR(1024)
)
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND COLUMN_NAME = p_column
  ) THEN
    SET @sql := CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN ', p_ddl);
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;

DROP PROCEDURE IF EXISTS `__SUIG31_ensure_index`;
DELIMITER $$
CREATE PROCEDURE `__SUIG31_ensure_index`(IN p_tbl VARCHAR(64), IN p_idx VARCHAR(64), IN p_cols VARCHAR(512))
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_tbl AND INDEX_NAME = p_idx
  ) THEN
    SET @sql := CONCAT('ALTER TABLE `', p_tbl, '` ADD INDEX `', p_idx, '` (', p_cols, ')');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 1. agent_config → agent 演进改名（数据原样保留，幂等）
--    1) 新表 agent 若不存在则建出（CREATE 幂等）；
--    2) 把既有 agent_config 数据按原 id 迁入 agent（WHERE NOT EXISTS 防重跑重复）；
--    3) 数据迁完后删旧表 agent_config（收敛完成）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `agent` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `kb_id`       BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(128)    NOT NULL,
  `graph_type`  VARCHAR(64)     NOT NULL COMMENT 'rag_graph/tool_graph/...',
  `temperature` FLOAT           NOT NULL DEFAULT 0.7,
  `top_k`       INT             NOT NULL DEFAULT 5,
  `conf`        JSON            NOT NULL,
  `version`     INT             NOT NULL DEFAULT 1,
  `status`      TINYINT         NOT NULL DEFAULT 1 COMMENT 'AgentStatus',
  `description` VARCHAR(512)    DEFAULT NULL,
  `tenant_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '多租户占位',
  `agent_type`  VARCHAR(32)     NOT NULL DEFAULT 'rag',
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_agent_kb`     (`kb_id`),
  KEY `idx_agent_tenant` (`tenant_id`),
  CONSTRAINT `fk_agent_kb` FOREIGN KEY (`kb_id`) REFERENCES `knowledge_base` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 定义（由 agent_config 演进）';

-- 数据收敛：仅当库内确实存在旧表 agent_config 时才迁移数据并收尾。
-- （全新安装时 01_schema 已建出 agent，无 agent_config，分支自动跳过，保证幂等。）
DROP PROCEDURE IF EXISTS `__SUIG31_converge_agent_config`;
DELIMITER $$
CREATE PROCEDURE `__SUIG31_converge_agent_config`()
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_config'
  ) THEN
    -- 数据回填：既有 agent_config 数据原样迁入 agent（只补到空 id，防重跑重复）
    INSERT INTO `agent` (`id`,`kb_id`,`name`,`graph_type`,`temperature`,`top_k`,`conf`,`version`,`status`)
    SELECT ce.`id`, ce.`kb_id`, ce.`name`, ce.`graph_type`, ce.`temperature`, ce.`top_k`, ce.`conf`,
           ce.`version`, ce.`status`
    FROM `agent_config` AS ce
    WHERE NOT EXISTS (SELECT 1 FROM `agent` AS a WHERE a.`id` = ce.`id`);

    -- 收敛完成：移除旧表（数据已在上一步原样保留）
    DROP TABLE IF EXISTS `agent_config`;
  END IF;
END$$
DELIMITER ;
CALL `__SUIG31_converge_agent_config`();

-- ---------------------------------------------------------------------------
-- 2. 既有 4 表补 `tenant_id` 占位列 + 索引（幂等；不动既有数据）
--    注：conversation 在 01_schema 中已有 thread_id 等，此处仅补 tenant_id 列。
-- ---------------------------------------------------------------------------
CALL `__SUIG31_ensure_column`('knowledge_base', 'tenant_id', 'BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''多租户占位''');
CALL `__SUIG31_ensure_column`('document',       'tenant_id', 'BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''多租户占位''');
CALL `__SUIG31_ensure_column`('conversation',   'tenant_id', 'BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''多租户占位''');

CALL `__SUIG31_ensure_index`('knowledge_base', 'idx_kb_tenant', '`tenant_id`');
CALL `__SUIG31_ensure_index`('document',       'idx_doc_tenant', '`tenant_id`');
CALL `__SUIG31_ensure_index`('conversation',   'idx_conv_tenant', '`tenant_id`');

-- ---------------------------------------------------------------------------
-- 3. 新增 15 表（CREATE TABLE IF NOT EXISTS 天然幂等）
--    本小节所有表本轮仅 DDL 入库，未接入路由。
-- ---------------------------------------------------------------------------

-- 3.1 多租户 / 权限
CREATE TABLE IF NOT EXISTS `sys_tenant` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(128)    NOT NULL,
  `code`       VARCHAR(64)     NOT NULL,
  `status`     TINYINT         NOT NULL DEFAULT 1,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_tenant_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='租户（DDL-only）';

CREATE TABLE IF NOT EXISTS `sys_user` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant_id`     BIGINT UNSIGNED NOT NULL,
  `username`      VARCHAR(64)     NOT NULL,
  `password_hash` VARCHAR(128)    NOT NULL COMMENT '只存哈希',
  `status`        TINYINT         NOT NULL DEFAULT 1,
  `last_login_at` DATETIME(3)     DEFAULT NULL,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_sys_user_username` (`username`),
  KEY `idx_sys_user_tenant` (`tenant_id`),
  CONSTRAINT `fk_sys_user_tenant` FOREIGN KEY (`tenant_id`) REFERENCES `sys_tenant` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='租户内用户（DDL-only）';

CREATE TABLE IF NOT EXISTS `sys_role` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant_id`   BIGINT UNSIGNED NOT NULL,
  `name`        VARCHAR(64)     NOT NULL,
  `code`        VARCHAR(64)     NOT NULL,
  `description` VARCHAR(255)    DEFAULT NULL,
  `status`      TINYINT         NOT NULL DEFAULT 1,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_role_code` (`code`),
  KEY `idx_sys_role_tenant` (`tenant_id`),
  CONSTRAINT `fk_sys_role_tenant` FOREIGN KEY (`tenant_id`) REFERENCES `sys_tenant` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色（DDL-only）';

CREATE TABLE IF NOT EXISTS `sys_permission` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant_id`  BIGINT UNSIGNED NOT NULL,
  `name`       VARCHAR(64)     NOT NULL,
  `code`       VARCHAR(128)    NOT NULL,
  `resource`   VARCHAR(128)    DEFAULT NULL COMMENT 'resource:action',
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_perm_code` (`code`),
  KEY `idx_sys_perm_tenant` (`tenant_id`),
  CONSTRAINT `fk_sys_perm_tenant` FOREIGN KEY (`tenant_id`) REFERENCES `sys_tenant` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限点（DDL-only）';

-- 3.2 Agent 版本 / 工具 / 知识绑定
CREATE TABLE IF NOT EXISTS `tool` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`          VARCHAR(128)    NOT NULL,
  `tool_type`     VARCHAR(32)     NOT NULL DEFAULT 'http',
  `params_schema` JSON            DEFAULT NULL,
  `endpoint`      VARCHAR(512)    DEFAULT NULL,
  `isolated`      TINYINT         NOT NULL DEFAULT 0 COMMENT 'SQL/代码类必须 1',
  `status`        TINYINT         NOT NULL DEFAULT 1,
  `tenant_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_tool_tenant` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工具（DDL-only）';

CREATE TABLE IF NOT EXISTS `agent_version` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `agent_id`    BIGINT UNSIGNED NOT NULL,
  `version_no`  INT             NOT NULL,
  `graph_type`  VARCHAR(64)     NOT NULL,
  `conf`        JSON            NOT NULL,
  `note`        VARCHAR(255)    DEFAULT NULL,
  `status`      TINYINT         NOT NULL DEFAULT 1,
  `tenant_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_agent_version` (`agent_id`, `version_no`),
  KEY `idx_agentver_tenant` (`tenant_id`),
  CONSTRAINT `fk_agentver_agent` FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 版本（DDL-only）';

CREATE TABLE IF NOT EXISTS `agent_tool` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `agent_id`   BIGINT UNSIGNED NOT NULL,
  `tool_id`    BIGINT UNSIGNED NOT NULL,
  `params`     JSON            NOT NULL,
  `enabled`    TINYINT         NOT NULL DEFAULT 1,
  `tenant_id`  BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_agent_tool` (`agent_id`, `tool_id`),
  KEY `idx_agenttool_tenant` (`tenant_id`),
  CONSTRAINT `fk_agenttool_agent` FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`),
  CONSTRAINT `fk_agenttool_tool`  FOREIGN KEY (`tool_id`)  REFERENCES `tool` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent-Tool 绑定（DDL-only）';

CREATE TABLE IF NOT EXISTS `agent_knowledge` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `agent_id`   BIGINT UNSIGNED NOT NULL,
  `kb_id`      BIGINT UNSIGNED NOT NULL,
  `enabled`    TINYINT         NOT NULL DEFAULT 1,
  `tenant_id`  BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_agent_kb` (`agent_id`, `kb_id`),
  KEY `idx_agentkb_tenant` (`tenant_id`),
  CONSTRAINT `fk_agentkb_agent` FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`),
  CONSTRAINT `fk_agentkb_kb`   FOREIGN KEY (`kb_id`)   REFERENCES `knowledge_base` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent-知识库 绑定（DDL-only）';

-- 3.3 文档切片元数据（对齐 ingest/chunker.py 的 Chunk 结构：index/text/token_count）
CREATE TABLE IF NOT EXISTS `document_chunk` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `doc_id`      BIGINT UNSIGNED NOT NULL,
  `chunk_id`    VARCHAR(64)     NOT NULL COMMENT '全局回链键(=ES _id/Milvus 主键)',
  `chunk_index` INT             NOT NULL DEFAULT 0,
  `token_count` INT             NOT NULL DEFAULT 0,
  `tenant_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_chunk_id` (`chunk_id`),
  KEY `idx_chunk_doc`    (`doc_id`),
  KEY `idx_chunk_tenant` (`tenant_id`),
  CONSTRAINT `fk_chunk_doc` FOREIGN KEY (`doc_id`) REFERENCES `document` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档切片元数据（原文只落 ES；DDL-only）';

-- 3.5 模型供应商
CREATE TABLE IF NOT EXISTS `model_provider` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`          VARCHAR(64)     NOT NULL,
  `provider_type` VARCHAR(32)     NOT NULL,
  `base_url`      VARCHAR(255)    DEFAULT NULL,
  `api_key_ref`   VARCHAR(255)    DEFAULT NULL COMMENT '加密引用/掩码，不落明文',
  `status`        TINYINT         NOT NULL DEFAULT 1,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_provider_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型供应商（DDL-only）';

-- 3.5.2 模型（model）
CREATE TABLE IF NOT EXISTS `model` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `provider_id` BIGINT UNSIGNED NOT NULL,
  `model_name`  VARCHAR(128)    NOT NULL,
  `meta`        JSON            DEFAULT NULL,
  `status`      TINYINT         NOT NULL DEFAULT 1,
  `tenant_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_provider_model` (`provider_id`, `model_name`),
  KEY `idx_model_tenant` (`tenant_id`),
  CONSTRAINT `fk_model_provider` FOREIGN KEY (`provider_id`) REFERENCES `model_provider` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型（DDL-only）';

-- 3.6 工具权限
CREATE TABLE IF NOT EXISTS `tool_permission` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `role_id`    BIGINT UNSIGNED NOT NULL,
  `tool_id`    BIGINT UNSIGNED NOT NULL,
  `level`      VARCHAR(16)     NOT NULL DEFAULT 'none',
  `tenant_id`  BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_role_tool` (`role_id`, `tool_id`),
  KEY `idx_toolperm_tenant` (`tenant_id`),
  CONSTRAINT `fk_toolperm_role` FOREIGN KEY (`role_id`) REFERENCES `sys_role` (`id`),
  CONSTRAINT `fk_toolperm_tool` FOREIGN KEY (`tool_id`) REFERENCES `tool` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工具授权（DDL-only）';

-- 3.7 Agent 运行 / 事件 / 审计
CREATE TABLE IF NOT EXISTS `agent_run` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id`        VARCHAR(64)     NOT NULL,
  `agent_id`      BIGINT UNSIGNED NOT NULL,
  `agent_version` INT             NOT NULL DEFAULT 1,
  `conv_id`       BIGINT UNSIGNED DEFAULT NULL,
  `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT 'RunStatus',
  `start_time`    DATETIME(3)     DEFAULT NULL,
  `end_time`      DATETIME(3)     DEFAULT NULL,
  `token_usage`   JSON            DEFAULT NULL,
  `error_code`    VARCHAR(128)    DEFAULT NULL,
  `error_message` VARCHAR(1024)   DEFAULT NULL,
  `tenant_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_run_id` (`run_id`),
  KEY `idx_run_agent`  (`agent_id`),
  KEY `idx_run_status` (`status`),
  KEY `idx_run_tenant` (`tenant_id`),
  CONSTRAINT `fk_run_agent` FOREIGN KEY (`agent_id`) REFERENCES `agent` (`id`),
  CONSTRAINT `fk_run_conv`  FOREIGN KEY (`conv_id`)  REFERENCES `conversation` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 运行（DDL-only）';

CREATE TABLE IF NOT EXISTS `agent_event` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id`     BIGINT UNSIGNED NOT NULL,
  `event_type` VARCHAR(32)     NOT NULL,
  `node_name`  VARCHAR(128)    DEFAULT NULL,
  `payload`    JSON            DEFAULT NULL,
  `seq`        INT             NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_event_run`  (`run_id`),
  KEY `idx_event_type` (`event_type`),
  CONSTRAINT `fk_event_run` FOREIGN KEY (`run_id`) REFERENCES `agent_run` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 运行时事件（DDL-only）';

CREATE TABLE IF NOT EXISTS `audit_log` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `actor_type`    VARCHAR(16)     NOT NULL COMMENT 'user/agent/system',
  `actor_id`      VARCHAR(64)     NOT NULL,
  `action`        VARCHAR(128)    NOT NULL,
  `resource_type` VARCHAR(64)     DEFAULT NULL,
  `resource_id`   VARCHAR(64)     DEFAULT NULL,
  `diff_context`  JSON            DEFAULT NULL COMMENT '变更前后快照',
  `ip`            VARCHAR(48)     DEFAULT NULL,
  `tenant_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_audit_actor` (`actor_type`, `actor_id`),
  KEY `idx_audit_tenant` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='审计日志（只增；DDL-only）';

-- ---------------------------------------------------------------------------
-- 4. 收尾：清理工具过程（保持库面整洁；均为本脚本专有，后缀规避覆盖）
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS `__SUIG31_converge_agent_config`;
DROP PROCEDURE IF EXISTS `__SUIG31_ensure_index`;
DROP PROCEDURE IF EXISTS `__SUIG31_ensure_column`;

-- 结束：此时应有 21 张表（5 既有 + agent 演进 + 15 新增）。