import os
from databricks import sql
from databricks.sdk.core import Config

_cfg = Config()  # app service principal auth, provided automatically by Apps


def _connect():
    return sql.connect(
        server_hostname=_cfg.host,
        http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
        credentials_provider=lambda: _cfg.authenticate,
    )


class DatabricksRecordRepository:
    def __init__(self, table: str | None = None):
        self.table = table or os.environ["CAPA_SOURCE_TABLE"]

    def _query(self, statement: str, params: dict | None = None) -> list[dict]:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(statement, params or {})
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # Mirror every public method of SqliteRecordRepository, e.g.:
    def list_records(self, limit: int = 1000) -> list[dict]:
        return self._query(f"SELECT * FROM {self.table} LIMIT :limit", {"limit": limit})

    def get_record(self, pr_id: int) -> dict | None:
        rows = self._query(f"SELECT * FROM {self.table} WHERE PR_ID = :pr_id", {"pr_id": pr_id})
        return rows[0] if rows else None

    def ping(self) -> bool:  # for /ready
        return bool(self._query("SELECT 1 AS ok"))