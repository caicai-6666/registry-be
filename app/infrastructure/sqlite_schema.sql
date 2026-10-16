-- 一行对应一次审核申请；resource_id 为应用层关联，不跨数据库声明外键。
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS ingestion_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE CHECK(length(message_id) BETWEEN 1 AND 128),
    resource_id TEXT NULL CHECK(resource_id IS NULL OR (length(resource_id) BETWEEN 1 AND 20 AND resource_id NOT GLOB '*[^0-9]*' AND substr(resource_id,1,1) BETWEEN '1' AND '9')),
    file_url TEXT NULL CHECK(file_url IS NULL OR length(file_url) BETWEEN 1 AND 146),
    name TEXT NULL CHECK(name IS NULL OR length(name) BETWEEN 1 AND 200),
    abstract TEXT NULL CHECK(abstract IS NULL OR length(abstract) BETWEEN 1 AND 10000),
    embedding TEXT NULL CHECK(embedding IS NULL OR CASE WHEN json_valid(embedding) THEN json_type(embedding)='array' AND json_array_length(embedding)>0 ELSE 0 END),
    passport_recommendations TEXT NULL CHECK(passport_recommendations IS NULL OR CASE WHEN json_valid(passport_recommendations) THEN json_type(passport_recommendations)='array' ELSE 0 END),
    request_offset TEXT NULL CHECK(request_offset IS NULL OR (length(request_offset) BETWEEN 1 AND 20 AND request_offset NOT GLOB '*[^0-9]*')),
    result_message_id TEXT NULL CHECK(result_message_id IS NULL OR length(result_message_id) BETWEEN 1 AND 128),
    decision TEXT NULL CHECK(decision IS NULL OR decision IN ('approved','rejected')),
    passport TEXT NULL CHECK(passport IS NULL OR CASE WHEN json_valid(passport) THEN json_type(passport)='array' ELSE 0 END),
    source_platform TEXT NOT NULL CHECK(length(source_platform) BETWEEN 1 AND 128),
    source_id TEXT NULL CHECK(source_id IS NULL OR length(source_id) BETWEEN 1 AND 128),
    applicant TEXT NULL CHECK(applicant IS NULL OR length(applicant) BETWEEN 1 AND 200),
    source_reviewer TEXT NULL CHECK(source_reviewer IS NULL OR length(source_reviewer) BETWEEN 1 AND 200),
    applicant_note TEXT NOT NULL DEFAULT '' CHECK(length(applicant_note) <= 10000),
    reviewer TEXT NULL CHECK(reviewer IS NULL OR length(reviewer) BETWEEN 1 AND 200),
    reviewer_note TEXT NULL CHECK(reviewer_note IS NULL OR length(reviewer_note) <= 10000),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    requested_at TEXT NOT NULL,
    reviewed_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_reviews_pending_source ON ingestion_reviews(source_platform,source_id) WHERE status='pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_reviews_resource ON ingestion_reviews(resource_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_reviews_source ON ingestion_reviews(source_platform,source_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_reviews_status_time ON ingestion_reviews(status,requested_at);

-- 删除申请不上传附件；file_url 保存正式资源原文件名，根目录为 RESOURCE_ROOT。
-- 业务名称、摘要与上传人保存申请时快照，正式资源删除后仍可查询。
CREATE TABLE IF NOT EXISTS deletion_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE CHECK(length(message_id) BETWEEN 1 AND 128),
    resource_id TEXT NULL CHECK(resource_id IS NULL OR (length(resource_id) BETWEEN 1 AND 20 AND resource_id NOT GLOB '*[^0-9]*' AND substr(resource_id,1,1) BETWEEN '1' AND '9')),
    request_offset TEXT NULL CHECK(request_offset IS NULL OR (length(request_offset) BETWEEN 1 AND 20 AND request_offset NOT GLOB '*[^0-9]*')),
    result_message_id TEXT NULL CHECK(result_message_id IS NULL OR length(result_message_id) BETWEEN 1 AND 128),
    decision TEXT NULL CHECK(decision IS NULL OR decision IN ('approved','rejected')),
    source_platform TEXT NOT NULL CHECK(length(source_platform) BETWEEN 1 AND 128),
    source_id TEXT NULL CHECK(source_id IS NULL OR length(source_id) BETWEEN 1 AND 128),
    passport TEXT NULL CHECK(passport IS NULL OR CASE WHEN json_valid(passport) THEN json_type(passport)='array' ELSE 0 END),
    file_url TEXT NULL CHECK(file_url IS NULL OR length(file_url) BETWEEN 1 AND 146),
    name TEXT NULL CHECK(name IS NULL OR length(name) BETWEEN 1 AND 200),
    abstract TEXT NULL CHECK(abstract IS NULL OR length(abstract) BETWEEN 1 AND 10000),
    uploader TEXT NULL CHECK(uploader IS NULL OR length(uploader) BETWEEN 1 AND 200),
    applicant TEXT NULL CHECK(applicant IS NULL OR length(applicant) BETWEEN 1 AND 200),
    applicant_note TEXT NOT NULL DEFAULT '' CHECK(length(applicant_note) <= 10000),
    reviewer TEXT NULL CHECK(reviewer IS NULL OR length(reviewer) BETWEEN 1 AND 200),
    reviewer_note TEXT NULL CHECK(reviewer_note IS NULL OR length(reviewer_note) <= 10000),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    requested_at TEXT NOT NULL,
    reviewed_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_reviews_resource ON deletion_reviews(resource_id);
CREATE INDEX IF NOT EXISTS idx_deletion_reviews_source ON deletion_reviews(source_platform,source_id);
CREATE INDEX IF NOT EXISTS idx_deletion_reviews_status_time ON deletion_reviews(status,requested_at);

PRAGMA user_version = 8;
COMMIT;
