-- SUIG-34 · P2 多租户 + RBAC 增量 DDL
-- 在既有 6 表（user/knowledge_base/document/conversation/message/agent_config）基础上：
--   1) 新增 RBAC 基础表：sys_tenant / sys_role / sys_permission / sys_user_role / sys_role_permission；
--   2) 为既有业务对象贯标 tenant_id（多租户隔离字段）；
--   3) 迁移既有单用户数据：一人一个默认租户 + 默认角色/权限绑定，保证 P0 前功能不回归。
--
-- 幂等：全部用 IF NOT EXISTS / information_schema 守卫，可重复执行、不炸既有数据。
-- 依赖 db/sql/01_schema.sql 已建 6 张业务表（schema 应用顺序 01 → 02）。
-- 注意：本文件需在启用了过程/DELIMITER 的 SQL 客户端下整段执行；
--       组件程序批量执行时建议按语句拆分（见 scripts/init_db.py 或运维手动执行）。

USE `agent_platform`;

-- ---------------------------------------------------------------------------
-- 0. 工具：列存在性判断（幂等 ALTER 守卫）
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS `_add_col_if_missing`;
DELIMITER $$
CREATE PROCEDURE `_add_col_if_missing`(
    IN p_table VARCHAR(64),
    IN p_column VARCHAR(64),
    IN p_ddl VARCHAR(512)
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = p_table AND column_name = p_column
    ) THEN
        SET @s = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_ddl);
        PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 1. 租户
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sys_tenant` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(128)    NOT NULL,
  `code`       VARCHAR(64)     NOT NULL COMMENT '唯一租户标识（入参寻址）',
  `status`     TINYINT         NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='租户';

-- ---------------------------------------------------------------------------
-- 2. 角色（租户维度；code 在租户内唯一）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sys_role` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant_id`  BIGINT UNSIGNED NOT NULL,
  `name`       VARCHAR(128)    NOT NULL,
  `code`       VARCHAR(64)     NOT NULL,
  `status`     TINYINT         NOT NULL DEFAULT 1,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_tenant_code` (`tenant_id`, `code`),
  KEY `idx_role_tenant` (`tenant_id`),
  CONSTRAINT `fk_role_tenant` FOREIGN KEY (`tenant_id`) REFERENCES `sys_tenant` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色';

-- ---------------------------------------------------------------------------
-- 3. 权限（tenant_id=0 为系统内置全局权限；业务读权限以 code 寻址）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sys_permission` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `tenant_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '0=系统内置全局权限；>0=租户自定义',
  `code`        VARCHAR(64)     NOT NULL COMMENT '例如 kb:read / doc:create',
  `name`        VARCHAR(128)    NOT NULL,
  `status`      TINYINT         NOT NULL DEFAULT 1,
  `created_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at`  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_perm_code` (`code`),
  KEY `idx_perm_tenant` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限';

-- ---------------------------------------------------------------------------
-- 4. 用户-角色绑定（用户在租户内被授予角色）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sys_user_role` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id`    BIGINT UNSIGNED NOT NULL,
  `role_id`    BIGINT UNSIGNED NOT NULL,
  `created_at` DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_role` (`user_id`, `role_id`),
  KEY `idx_sur_role` (`role_id`),
  CONSTRAINT `fk_sur_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`),
  CONSTRAINT `fk_sur_role` FOREIGN KEY (`role_id`) REFERENCES `sys_role` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-角色';

-- ---------------------------------------------------------------------------
-- 5. 角色-权限
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sys_role_permission` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `role_id`       BIGINT UNSIGNED NOT NULL,
  `permission_id` BIGINT UNSIGNED NOT NULL,
  `created_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_role_perm` (`role_id`, `permission_id`),
  KEY `idx_srp_perm` (`permission_id`),
  CONSTRAINT `fk_srp_role` FOREIGN KEY (`role_id`) REFERENCES `sys_role` (`id`),
  CONSTRAINT `fk_srp_perm` FOREIGN KEY (`permission_id`) REFERENCES `sys_permission` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色-权限';

-- ---------------------------------------------------------------------------
-- 6. 业务对象 tenant_id 贯标（幂等 ALTER）
--    权威隔离靠 knowledge_base.tenant_id；其下 document/conversation/agent_config
--    声明显式的 tenant_id 列，查询路径可直接过滤（任务：全业务对象贯标）。
-- ---------------------------------------------------------------------------
CALL `_add_col_if_missing`('user', 'tenant_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('user', 'role_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('knowledge_base', 'tenant_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('document', 'tenant_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('conversation', 'tenant_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('message', 'tenant_id', 'BIGINT UNSIGNED NULL');
CALL `_add_col_if_missing`('agent_config', 'tenant_id', 'BIGINT UNSIGNED NULL');

DROP PROCEDURE IF EXISTS `_add_col_if_missing`;

-- 访问路径索引（批量加，均幂等）
SET @has_idx := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='user' AND index_name='idx_user_tenant');
SET @sql := IF(@has_idx=0, 'ALTER TABLE `user` ADD KEY `idx_user_tenant` (`tenant_id`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has_idx := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='knowledge_base' AND index_name='idx_kb_tenant');
SET @sql := IF(@has_idx=0, 'ALTER TABLE `knowledge_base` ADD KEY `idx_kb_tenant` (`tenant_id`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has_idx := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='agent_config' AND index_name='idx_agent_tenant');
SET @sql := IF(@has_idx=0, 'ALTER TABLE `agent_config` ADD KEY `idx_agent_tenant` (`tenant_id`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has_idx := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='conversation' AND index_name='idx_conv_tenant');
SET @sql := IF(@has_idx=0, 'ALTER TABLE `conversation` ADD KEY `idx_conv_tenant` (`tenant_id`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has_idx := (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='document' AND index_name='idx_doc_tenant');
SET @sql := IF(@has_idx=0, 'ALTER TABLE `document` ADD KEY `idx_doc_tenant` (`tenant_id`)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------------------
-- 7. 系统内置权限种子（幂等：INSERT ... SELECT ... WHERE NOT EXISTS）
-- ---------------------------------------------------------------------------
INSERT INTO `sys_permission` (`tenant_id`, `code`, `name`)
SELECT 0, perms.code, perms.name FROM (
  SELECT 'kb:list'   AS code, '知识库：列表'   AS name UNION ALL
  SELECT 'kb:read'   AS code, '知识库：读取'   AS name UNION ALL
  SELECT 'kb:create' AS code, '知识库：创建'   AS name UNION ALL
  SELECT 'kb:update' AS code, '知识库：更新'   AS name UNION ALL
  SELECT 'kb:delete' AS code, '知识库：删除'   AS name UNION ALL
  SELECT 'agent:list'   AS code, 'Agent：列表'   AS name UNION ALL
  SELECT 'agent:create' AS code, 'Agent：创建'   AS name UNION ALL
  SELECT 'doc:upload'   AS code, '文档：上传'    AS name UNION ALL
  SELECT 'doc:read'     AS code, '文档：读取'    AS name UNION ALL
  SELECT 'doc:delete'   AS code, '文档：删除'    AS name UNION ALL
  SELECT 'task:read'    AS code, '任务：读取'    AS name
) perms
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` sp WHERE sp.code = perms.code);

-- ---------------------------------------------------------------------------
-- 8. 默认租户 + 默认管理员角色 + 全量权限，迁移既有单用户数据（一人一默认租户）
--    幂等：仅当 user.tenant_id 为空时执行。
-- ---------------------------------------------------------------------------
-- 8a. 每个既有 user 建一个默认租户（code=t{user_id}）
INSERT INTO `sys_tenant` (`name`, `code`)
SELECT CONCAT('默认租户-', u.id), CONCAT('t', u.id)
FROM `user` u
WHERE u.tenant_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM `sys_tenant` t WHERE t.code = CONCAT('t', u.id));

-- 8b. 把 user.tenant_id 指向其默认租户
UPDATE `user` u
LEFT JOIN `sys_tenant` t ON t.code = CONCAT('t', u.id)
SET u.tenant_id = t.id
WHERE u.tenant_id IS NULL
  AND t.id IS NOT NULL;

-- 8c. 每租户一个 admin 角色
INSERT INTO `sys_role` (`tenant_id`, `name`, `code`)
SELECT t.id, '管理员', 'admin'
FROM `sys_tenant` t
WHERE NOT EXISTS (SELECT 1 FROM `sys_role` r WHERE r.tenant_id = t.id AND r.code = 'admin');

-- 8d. admin 角色挂够全部系统权限
INSERT INTO `sys_role_permission` (`role_id`, `permission_id`)
SELECT r.id, p.id
FROM `sys_role` r
JOIN `sys_permission` p ON p.tenant_id = 0
WHERE r.code = 'admin'
  AND NOT EXISTS (SELECT 1 FROM `sys_role_permission` rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id);

-- 8e. 把 user.role_id 指到其租户的 admin 角色（冗余取权）
UPDATE `user` u
JOIN `sys_role` r ON r.tenant_id = u.tenant_id AND r.code = 'admin'
SET u.role_id = r.id
WHERE (u.role_id IS NULL OR u.role_id = 0)
  AND u.tenant_id IS NOT NULL;

-- 8f. 用户-角色绑定（幂等）
INSERT INTO `sys_user_role` (`user_id`, `role_id`)
SELECT u.id, u.role_id
FROM `user` u
WHERE u.role_id IS NOT NULL AND u.role_id > 0
  AND NOT EXISTS (SELECT 1 FROM `sys_user_role` sur
                  WHERE sur.user_id = u.id AND sur.role_id = u.role_id);