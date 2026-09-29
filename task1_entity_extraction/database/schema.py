"""
数据库表结构定义（MySQL DDL）
"""

# ============================================================
# 建库语句
# ============================================================
CREATE_DATABASE = """
CREATE DATABASE IF NOT EXISTS {db_name}
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;
"""

# ============================================================
# 建表语句
# ============================================================

# 1. 公告元数据表
CREATE_TABLE_ANNOUNCEMENTS = """
CREATE TABLE IF NOT EXISTS announcements (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    announcement_id VARCHAR(255) NOT NULL UNIQUE COMMENT '公告唯一标识（文件名）',
    title           VARCHAR(1024) DEFAULT '' COMMENT '公告标题',
    file_path       VARCHAR(1024) DEFAULT '' COMMENT 'HTML 文件路径',
    raw_text        LONGTEXT COMMENT '公告全文文本（含附件）',
    status          VARCHAR(20) DEFAULT 'pending' COMMENT '处理状态：pending/processing/done/failed',
    error_message   TEXT COMMENT '错误信息',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公告元数据表';
"""

# 2. 标的物提取结果表（任务一核心：7个字段）
CREATE_TABLE_ENTITIES = """
CREATE TABLE IF NOT EXISTS extracted_entities (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    announcement_id VARCHAR(255) NOT NULL COMMENT '关联公告ID',
    product_name    VARCHAR(512)  DEFAULT '' COMMENT '产品服务名称',
    category        VARCHAR(255)  DEFAULT '' COMMENT '品目',
    brand           VARCHAR(255)  DEFAULT '' COMMENT '品牌（产品供应商）',
    spec_model      VARCHAR(2048) DEFAULT '' COMMENT '规格型号',
    unit_price      VARCHAR(100)  DEFAULT '' COMMENT '单价',
    quantity        VARCHAR(100)  DEFAULT '' COMMENT '数量',
    total_price     VARCHAR(100)  DEFAULT '' COMMENT '总价',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_announcement (announcement_id),
    INDEX idx_product_name (product_name),
    INDEX idx_brand (brand),
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='标的物提取结果表（任务一）';
"""

# 3. 项目关系表（任务二：支撑关系建模）
CREATE_TABLE_PROJECTS = """
CREATE TABLE IF NOT EXISTS project_relations (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    announcement_id   VARCHAR(255) NOT NULL COMMENT '关联公告ID',
    project_name      VARCHAR(512) DEFAULT '' COMMENT '项目名称',
    procurement_unit  VARCHAR(255) DEFAULT '' COMMENT '采购单位',
    winning_supplier  VARCHAR(512) DEFAULT '' COMMENT '中标供应商',
    project_amount    VARCHAR(1024) DEFAULT '' COMMENT '项目金额',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_announcement (announcement_id),
    INDEX idx_procurement_unit (procurement_unit),
    INDEX idx_winning_supplier (winning_supplier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='项目关系表（任务二）';
"""

# 4. 投标人表（任务二：竞标主体）
CREATE_TABLE_BIDDERS = """
CREATE TABLE IF NOT EXISTS bidders (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    announcement_id VARCHAR(255) NOT NULL COMMENT '关联公告ID',
    bidder_name     VARCHAR(512) NOT NULL COMMENT '投标人/供应商名称',
    is_winner       TINYINT(1) DEFAULT 0 COMMENT '是否中标：0=未中标，1=中标',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_announcement (announcement_id),
    INDEX idx_bidder_name (bidder_name),
    INDEX idx_is_winner (is_winner)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='投标人表（任务二）';
"""

# 所有建表语句
ALL_TABLES = [
    CREATE_TABLE_ANNOUNCEMENTS,
    CREATE_TABLE_ENTITIES,
    CREATE_TABLE_PROJECTS,
    CREATE_TABLE_BIDDERS,
]
