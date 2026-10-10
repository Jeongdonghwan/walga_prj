# -*- coding: utf-8 -*-
"""PyMySQL 직접 사용 (ORM 없음). 요청당 커넥션 1개, app.py의 teardown에서 정리."""
import pymysql
from flask import current_app, g


def get_db():
    if "db" not in g:
        cfg = current_app.config
        g.db = pymysql.connect(
            host=cfg["DB_HOST"], port=cfg["DB_PORT"], user=cfg["DB_USER"],
            password=cfg["DB_PASSWORD"], database=cfg["DB_NAME"],
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            autocommit=True, init_command="SET time_zone='+09:00'",  # 서버 TZ와 무관하게 KST
        )
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def rows(sql, args=()):
    cur = get_db().cursor()
    cur.execute(sql, args)
    return cur.fetchall()


def one(sql, args=()):
    cur = get_db().cursor()
    cur.execute(sql, args)
    return cur.fetchone()


def run(sql, args=()):
    cur = get_db().cursor()
    cur.execute(sql, args)
    return cur.lastrowid
