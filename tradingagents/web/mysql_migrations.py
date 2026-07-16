"""Ordered MySQL schema migrations for the CloudBase Web store."""

from __future__ import annotations

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                `key` VARCHAR(100) PRIMARY KEY,
                value LONGTEXT NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                updated_by VARCHAR(255) NOT NULL DEFAULT ''
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash VARCHAR(64) PRIMARY KEY,
                created_at VARCHAR(40) NOT NULL,
                expires_at VARCHAR(40)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS access_whitelist (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                email VARCHAR(320) NOT NULL UNIQUE,
                uid VARCHAR(255),
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                daily_limit INT NOT NULL DEFAULT 5,
                allowed_models LONGTEXT NOT NULL,
                note LONGTEXT NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                INDEX idx_access_whitelist_uid (uid)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS app_users (
                uid VARCHAR(255) PRIMARY KEY,
                email VARCHAR(320) NOT NULL DEFAULT '',
                display_name VARCHAR(255) NOT NULL DEFAULT '',
                role VARCHAR(32) NOT NULL DEFAULT 'user',
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                daily_limit INT NOT NULL DEFAULT 5,
                created_at VARCHAR(40) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                INDEX idx_app_users_email (email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS model_configs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                provider VARCHAR(100) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                base_url VARCHAR(1000),
                quick_model VARCHAR(255) NOT NULL,
                deep_model VARCHAR(255) NOT NULL,
                api_key_ciphertext LONGTEXT NOT NULL,
                api_key_nonce LONGTEXT NOT NULL,
                api_key_masked VARCHAR(255) NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                is_default TINYINT(1) NOT NULL DEFAULT 0,
                created_by VARCHAR(255) NOT NULL DEFAULT '',
                created_at VARCHAR(40) NOT NULL,
                updated_at VARCHAR(40) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS analysis_reports (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                run_id VARCHAR(36),
                ticker VARCHAR(64) NOT NULL,
                stock_name VARCHAR(255) NOT NULL DEFAULT '',
                trade_date VARCHAR(20) NOT NULL,
                analysts LONGTEXT NOT NULL,
                sections LONGTEXT NOT NULL,
                decision LONGTEXT NOT NULL,
                owner VARCHAR(320) NOT NULL DEFAULT '',
                owner_key VARCHAR(512) NOT NULL DEFAULT '',
                owner_uid VARCHAR(255) NOT NULL DEFAULT '',
                owner_email VARCHAR(320) NOT NULL DEFAULT '',
                created_at VARCHAR(40) NOT NULL,
                INDEX idx_reports_owner_key (owner_key),
                INDEX idx_reports_run_id (run_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id VARCHAR(36) PRIMARY KEY,
                owner_key VARCHAR(512) NOT NULL,
                owner_uid VARCHAR(255) NOT NULL DEFAULT '',
                owner_email VARCHAR(320) NOT NULL DEFAULT '',
                ticker VARCHAR(64) NOT NULL,
                stock_name VARCHAR(255) NOT NULL DEFAULT '',
                trade_date VARCHAR(20) NOT NULL,
                asset_type VARCHAR(32) NOT NULL DEFAULT 'stock',
                analysts LONGTEXT NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'queued',
                current_agent VARCHAR(255) NOT NULL DEFAULT '',
                last_event_seq BIGINT NOT NULL DEFAULT 0,
                error_type VARCHAR(255) NOT NULL DEFAULT '',
                error_message LONGTEXT NOT NULL,
                report_id BIGINT,
                created_at VARCHAR(40) NOT NULL,
                started_at VARCHAR(40),
                finished_at VARCHAR(40),
                heartbeat_at VARCHAR(40),
                INDEX idx_runs_owner_status (owner_key, status),
                INDEX idx_runs_status_created (status, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS analysis_run_events (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                run_id VARCHAR(36) NOT NULL,
                seq BIGINT NOT NULL,
                event VARCHAR(100) NOT NULL,
                data LONGTEXT NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                UNIQUE KEY uq_run_event_seq (run_id, seq),
                INDEX idx_run_events_run_seq (run_id, seq)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS active_analysis_owners (
                owner_key VARCHAR(512) PRIMARY KEY,
                run_id VARCHAR(36) NOT NULL UNIQUE,
                acquired_at VARCHAR(40) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
        ),
    ),
)
