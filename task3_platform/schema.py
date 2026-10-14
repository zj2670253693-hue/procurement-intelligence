"""Explicit MySQL8 schema; no creation happens on import or API startup."""
SCHEMA_VERSION = 1

_BODIES = {
    "t3_users": """
      user_id CHAR(32) CHARACTER SET ascii PRIMARY KEY,
      username VARCHAR(64) COLLATE utf8mb4_bin NOT NULL UNIQUE,
      password_hash VARCHAR(256) NOT NULL, role VARCHAR(16) NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at DATETIME(6) NOT NULL""",
    "t3_sessions": """
      token_hash CHAR(64) CHARACTER SET ascii PRIMARY KEY,
      user_id CHAR(32) CHARACTER SET ascii NOT NULL, csrf_hash CHAR(64) NOT NULL,
      origin VARCHAR(512) NOT NULL, expires_at DATETIME(6) NOT NULL,
      revoked BOOLEAN NOT NULL DEFAULT FALSE,
      FOREIGN KEY(user_id) REFERENCES t3_users(user_id), INDEX(expires_at)""",
    "t3_jobs": """
      task_id CHAR(32) CHARACTER SET ascii PRIMARY KEY,
      upload_dataset_id CHAR(35) CHARACTER SET ascii NOT NULL UNIQUE,
      owner_id CHAR(32) CHARACTER SET ascii NOT NULL, mode VARCHAR(16) NOT NULL,
      status VARCHAR(32) NOT NULL, phase VARCHAR(32) NOT NULL,
      request_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
      request_fingerprint CHAR(64) NOT NULL, input_fingerprint CHAR(64) NULL,
      processing_fingerprint CHAR(64) NOT NULL, spec_json JSON NOT NULL,
      manifest_json JSON NULL, diagnostics_json JSON NULL,
      epoch BIGINT UNSIGNED NOT NULL DEFAULT 0, worker_token VARCHAR(128) NULL,
      lease_until DATETIME(6) NULL, cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
      created_at DATETIME(6) NOT NULL, accepted_at DATETIME(6) NULL,
      started_at DATETIME(6) NULL, finished_at DATETIME(6) NULL,
      received_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
      expanded_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
      reserved_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
      used_calls BIGINT UNSIGNED NOT NULL DEFAULT 0,
      reserved_input_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0,
      reserved_output_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0,
      UNIQUE(owner_id,request_key), INDEX(status,created_at),
      INDEX(owner_id,input_fingerprint,processing_fingerprint),
      FOREIGN KEY(owner_id) REFERENCES t3_users(user_id)""",
    "t3_datasets": """
      upload_dataset_id CHAR(35) CHARACTER SET ascii PRIMARY KEY,
      task_id CHAR(32) CHARACTER SET ascii NOT NULL UNIQUE,
      owner_id CHAR(32) CHARACTER SET ascii NOT NULL, availability VARCHAR(32) NOT NULL,
      task2_dataset_id CHAR(67) CHARACTER SET ascii NULL,
      manifest_json JSON NULL, created_at DATETIME(6) NOT NULL,
      FOREIGN KEY(task_id) REFERENCES t3_jobs(task_id),
      FOREIGN KEY(owner_id) REFERENCES t3_users(user_id)""",
    "t3_upload_files": """
      task_id CHAR(32) CHARACTER SET ascii NOT NULL,
      file_id CHAR(32) CHARACTER SET ascii NOT NULL, original_name VARCHAR(512) NOT NULL,
      expected_bytes BIGINT UNSIGNED NOT NULL, part_hashes JSON NOT NULL,
      received_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
      relative_path VARCHAR(1024) NULL, sha256 CHAR(64) NULL,
      PRIMARY KEY(task_id,file_id), FOREIGN KEY(task_id) REFERENCES t3_jobs(task_id)""",
    "t3_upload_parts": """
      task_id CHAR(32) CHARACTER SET ascii NOT NULL,
      file_id CHAR(32) CHARACTER SET ascii NOT NULL, part_no INT UNSIGNED NOT NULL,
      sha256 CHAR(64) NOT NULL, bytes BIGINT UNSIGNED NOT NULL,
      relative_path VARCHAR(1024) NOT NULL,
      PRIMARY KEY(task_id,file_id,part_no),
      FOREIGN KEY(task_id,file_id) REFERENCES t3_upload_files(task_id,file_id)""",
    "t3_announcements": """
      upload_dataset_id CHAR(35) CHARACTER SET ascii NOT NULL,
      announcement_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
      html_sha256 CHAR(64) NOT NULL, zip_sha256 CHAR(64) NULL,
      html_relative_path VARCHAR(1024) NOT NULL, zip_relative_path VARCHAR(1024) NULL,
      payload_json JSON NOT NULL, diagnostics_json JSON NOT NULL,
      PRIMARY KEY(upload_dataset_id,announcement_id),
      FOREIGN KEY(upload_dataset_id) REFERENCES t3_datasets(upload_dataset_id)""",
    "t3_entities": """
      upload_dataset_id CHAR(35) CHARACTER SET ascii NOT NULL,
      announcement_id VARCHAR(128) CHARACTER SET ascii NOT NULL, row_no INT UNSIGNED NOT NULL,
      product_name TEXT NULL, category TEXT NULL, brand TEXT NULL, spec_model MEDIUMTEXT NULL,
      unit_price TEXT NULL, quantity TEXT NULL, total_price TEXT NULL,
      unit_price_numeric DECIMAL(65,30) NULL, quantity_numeric DECIMAL(65,30) NULL,
      total_price_numeric DECIMAL(65,30) NULL, payload_json JSON NOT NULL,
      PRIMARY KEY(upload_dataset_id,announcement_id,row_no),
      FOREIGN KEY(upload_dataset_id,announcement_id)
        REFERENCES t3_announcements(upload_dataset_id,announcement_id)""",
    "t3_detail_entities": """
      upload_dataset_id CHAR(35) CHARACTER SET ascii NOT NULL,
      announcement_id VARCHAR(128) CHARACTER SET ascii NOT NULL, row_no INT UNSIGNED NOT NULL,
      payload_json JSON NOT NULL, source_fingerprint CHAR(64) NOT NULL,
      PRIMARY KEY(upload_dataset_id,announcement_id,row_no),
      FOREIGN KEY(upload_dataset_id,announcement_id)
        REFERENCES t3_announcements(upload_dataset_id,announcement_id)""",
    "t3_item_checkpoints": """
      task_id CHAR(32) CHARACTER SET ascii NOT NULL,
      announcement_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
      input_fingerprint CHAR(64) NOT NULL, processing_fingerprint CHAR(64) NOT NULL,
      result_sha256 CHAR(64) NOT NULL, db_state VARCHAR(32) NOT NULL,
      epoch BIGINT UNSIGNED NOT NULL, updated_at DATETIME(6) NOT NULL,
      PRIMARY KEY(task_id,announcement_id), FOREIGN KEY(task_id) REFERENCES t3_jobs(task_id)""",
    "t3_inference_attempts": """
      attempt_id CHAR(32) CHARACTER SET ascii PRIMARY KEY,
      task_id CHAR(32) CHARACTER SET ascii NOT NULL, request_hash CHAR(64) NOT NULL,
      announcement_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
      epoch BIGINT UNSIGNED NOT NULL, state VARCHAR(32) NOT NULL,
      request_json JSON NOT NULL, response_json JSON NULL,
      reserved_input_tokens BIGINT UNSIGNED NOT NULL,
      reserved_output_tokens BIGINT UNSIGNED NOT NULL,
      error_code VARCHAR(64) NULL, created_at DATETIME(6) NOT NULL,
      sent_at DATETIME(6) NULL, responded_at DATETIME(6) NULL,
      UNIQUE(task_id,request_hash), FOREIGN KEY(task_id) REFERENCES t3_jobs(task_id)""",
    "t3_artifacts": """
      upload_dataset_id CHAR(35) CHARACTER SET ascii NOT NULL,
      name VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, relative_path VARCHAR(1024) NOT NULL,
      sha256 CHAR(64) NOT NULL, bytes BIGINT UNSIGNED NOT NULL, kind VARCHAR(32) NOT NULL,
      PRIMARY KEY(upload_dataset_id,name),
      FOREIGN KEY(upload_dataset_id) REFERENCES t3_datasets(upload_dataset_id)""",
    "t3_runtime_state": """
      singleton_id TINYINT UNSIGNED PRIMARY KEY, schema_version INT UNSIGNED NOT NULL,
      reserved_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
      active_task_id CHAR(32) CHARACTER SET ascii NULL,
      CHECK(singleton_id=1)""",
}

TABLES = tuple(_BODIES)
DDL = tuple("CREATE TABLE IF NOT EXISTS " + name + " (" + body +
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
            for name, body in _BODIES.items())
