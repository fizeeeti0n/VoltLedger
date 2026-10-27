"""Tiny SQLite layer (stdlib only). Swap for Postgres by replacing this module."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

DB_PATH = os.environ.get("VOLT_DB", os.path.join(os.path.dirname(__file__), "..", "data", "voltledger.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  utility TEXT NOT NULL,
  filename TEXT NOT NULL,
  meta_json TEXT NOT NULL,
  records_json TEXT NOT NULL,
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, id DESC);
"""


def init():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def create_user(email, pw_hash):
    with conn() as c:
        cur = c.execute("INSERT INTO users(email, pw_hash) VALUES(?,?)", (email, pw_hash))
        return cur.lastrowid


def get_user_by_email(email):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def get_user(uid):
    with conn() as c:
        return c.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()


def save_report(uid, title, utility, filename, meta, records):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO reports(user_id,title,utility,filename,meta_json,records_json) VALUES(?,?,?,?,?,?)",
            (uid, title, utility, filename, json.dumps(meta), json.dumps(records)))
        return cur.lastrowid


def list_reports(uid):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,title,utility,filename,created FROM reports WHERE user_id=? ORDER BY id DESC", (uid,))]


def get_report(uid, rid):
    with conn() as c:
        r = c.execute("SELECT * FROM reports WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        return dict(r) if r else None


def delete_report(uid, rid):
    with conn() as c:
        return c.execute("DELETE FROM reports WHERE id=? AND user_id=?", (rid, uid)).rowcount
