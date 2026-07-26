from uuid import UUID

from polis.store.engine import Database


class Repository:
    def __init__(self, db: Database, run_id: UUID) -> None:
        self.db = db
        self.run_id = run_id
