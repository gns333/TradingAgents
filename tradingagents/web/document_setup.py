"""Provision private-plan document collections/indexes with server credentials.

Run once before deploying. Configure ADMINONLY rules for each collection in
CloudBase; runtime never exposes these credentials to clients.
"""

from __future__ import annotations

from .document_client import DocumentClient, DocumentDatabaseError
from .document_store import COLLECTIONS
from .runtime import load_web_runtime_config


def provision(client):
    for collection in COLLECTIONS:
        try:
            client.request("POST", "/collections", body={"collectionName": collection})
        except DocumentDatabaseError as exc:
            if exc.code != "DATABASE_COLLECTION_ALREADY_EXIST":
                raise
    indexes = {
        "ta_whitelist": [{"name": "whitelist_id", "key": {"id": 1}, "unique": True}],
        "ta_users": [{"name": "users_order", "key": {"role": 1, "email": 1, "uid": 1}}],
        "ta_models": [{"name": "enabled_model", "key": {"enabled": 1, "id": -1}}],
        "ta_runs": [{"name": "queued_runs", "key": {"status": 1, "created_at": 1, "id": 1}}],
        "ta_events": [{"name": "run_event_seq", "key": {"run_id": 1, "seq": 1}, "unique": True}],
        "ta_reports": [{"name": "owner_reports", "key": {"owner_key": 1, "id": -1}}],
        "ta_operations": [{"name": "receipt_expiry", "key": {"expires_at": 1}, "expireAfterSeconds": 0}],
    }
    for collection, specs in indexes.items():
        client.request("POST", "/commands", body={"commands": [
            {"createIndexes": collection, "indexes": specs},
        ]})


def main():
    config = load_web_runtime_config()
    if config.mode != "cloudbase" or config.database_backend != "document":
        raise RuntimeError("Select CloudBase document backend before provisioning")
    provision(DocumentClient(config.cloudbase_env_id, config.cloudbase_api_key))
    print("CloudBase document collections and indexes are ready")


if __name__ == "__main__":
    main()
