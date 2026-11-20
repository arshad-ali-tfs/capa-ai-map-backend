import csv
import hashlib
import logging
import os
import re
import time
from databricks import sql
from databricks.sdk.core import Config
from capa_map.infrastructure.contracts import SourceRecord

logger = logging.getLogger(__name__)
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
        parts = self.table.split(".")
        if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
            raise ValueError("CAPA_SOURCE_TABLE must be a fully qualified catalog.schema.table identifier.")
        self.qualified_table = ".".join(f"`{part}`" for part in parts)

    def _query(self, statement: str, params: dict | None = None) -> list[dict]:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(statement, params or {})
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def metadata(self) -> dict:
        result = self._query(f"SELECT COUNT(*) AS total FROM {self.qualified_table}")[0]
        total = int(result['total'])
        return {'snapshot_id': hashlib.sha256(self.table.encode('utf-8')).hexdigest(),
                'source_rows': total, 'accepted_rows': total, 'quarantined_rows': 0,
                'source_table': self.table}

    def list_records(self, *, offset=0, limit=25, site=None, record_type=None):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('offset >= 0 and 1 <= limit <= 100 required')
        conditions, params = [], {}
        if site is not None:
            conditions.append('SITE = :site')
            params['site'] = site
        if record_type is not None:
            conditions.append('RECORD_TYPE = :record_type')
            params['record_type'] = record_type
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        total = int(self._query(f"SELECT COUNT(*) AS total FROM {self.qualified_table}{where}", params)[0]['total'])
        params.update(limit=limit, offset=offset)
        raw_items = self._query(f"SELECT * FROM {self.qualified_table}{where} ORDER BY PR_ID LIMIT :limit OFFSET :offset", params)
        items = []
        for row in raw_items:
            fields = {str(key).strip().lower(): '' if value is None else str(value) for key, value in row.items()}
            items.append({'pr_id': fields.get('pr_id', ''), 'site': fields.get('site', ''),
                          'division': fields.get('psg_division', ''), 'record_type': fields.get('record_type', ''),
                          'status': fields.get('status', ''), 'title': fields.get('record_name', ''),
                          'source_row': 0})
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit}

    def get_record(self, pr_id: str) -> SourceRecord | None:
        rows = self._query(f"SELECT * FROM {self.qualified_table} WHERE PR_ID = :pr_id", {"pr_id": pr_id})
        if not rows:
            return None
        original = rows[0]
        fields = {str(key).strip().lower(): '' if value is None else str(value) for key, value in original.items()}
        return SourceRecord(
            pr_id=str(fields.get('pr_id', pr_id)), site=fields.get('site', ''),
            division=fields.get('psg_division', ''), record_type=fields.get('record_type', ''),
            status=fields.get('status', ''), title=fields.get('record_name', ''),
            source_row=0, fields=fields, original=original,
        )

    def list_quarantine(self, *, offset=0, limit=25):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('offset >= 0 and 1 <= limit <= 100 required')
        return {'items': [], 'total': 0, 'offset': offset, 'limit': limit}

    def ping(self) -> bool:  # for /ready
        return bool(self._query("SELECT 1 AS ok"))

    def export_csv(self, destination, *, require_root_parent=True):
        """Write the source table to a CSV consumed by the existing MAP pipeline."""
        destination = os.fspath(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        started = time.perf_counter()
        row_count = 0
        logger.info(
            "warehouse_export_started table=%s warehouse_id=%s",
            self.table, os.environ.get("DATABRICKS_WAREHOUSE_ID", "missing"),
        )
        try:
            with _connect() as conn, conn.cursor() as cur, open(destination, "w", encoding="utf-8-sig", newline="") as out:
                cur.execute(f"SELECT * FROM {self.qualified_table} ORDER BY PR_ID")
                columns = [description[0] for description in cur.description]
                normalized = {re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") for name in columns}
                required = {"pr_id", "psg_division", "record_type", "status", "detailed_description", "investigation", "conclusion"}
                if require_root_parent:
                    required.add("root_parent_record_type")
                missing = sorted(required - normalized)
                if missing:
                    raise ValueError("CAPA_SOURCE_TABLE is missing MAP input columns: " + ", ".join(missing))
                writer = csv.writer(out)
                writer.writerow(columns)
                while batch := cur.fetchmany(1000):
                    for row in batch:
                        writer.writerow([
                            value.isoformat() if hasattr(value, "isoformat") else "" if value is None else str(value)
                            for value in row
                        ])
                    row_count += len(batch)
        except Exception as error:
            logger.exception(
                "warehouse_export_failed table=%s error_type=%s",
                self.table, type(error).__name__,
            )
            raise
        logger.info(
            "warehouse_export_completed table=%s rows=%d bytes=%d elapsed_seconds=%.2f",
            self.table, row_count, os.path.getsize(destination), time.perf_counter() - started,
        )
        return destination
