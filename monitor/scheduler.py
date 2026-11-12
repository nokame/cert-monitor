import threading
import time

from . import certchecker


class Scheduler:
    def __init__(self, config, mailer):
        self.config = config
        self.mailer = mailer
        self.interval = max(1, config["check_interval_hours"]) * 3600
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cert-checker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(5):
            try:
                self.check_all()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def check_all(self):
        import sqlite3

        from .config import load_config

        db = sqlite3.connect(self.config["db_path"])
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        rows = db.execute("SELECT * FROM sites WHERE enabled = 1").fetchall()
        for row in rows:
            result = certchecker.check_site(dict(row))
            certchecker.save_result(db, row["id"], result, self.config)
            certchecker.evaluate_and_notify(db, self.config, self.mailer, row["id"], result)
        db.commit()
        db.close()
