# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 t-kenji <protect.2501@gmail.com>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.

from trac.db import DatabaseManager

def execute_non_query(env, sql, *params):
    with env.db_transaction as db:
        cur = db.cursor()
        cur.execute(sql, params)

def get_scalar(env, sql, column=0, *params):
    with env.db_transaction as db:
        cur = db.cursor()
        cur.execute(sql, params)
        data = cur.fetchone()
        if data:
            return data[column]

def get_column(env, table, column):
    with env.db_transaction as db:
        cur = db.cursor()
        cur.execute("""
            SELECT %s FROM %s
            """ % (column, table))
        return [datum[0] for datum in cur.fetchall()]

def create_table(env, table):
    conn, _ = DatabaseManager(env).get_connector()
    stmts = conn.to_sql(table)
    for stmt in stmts:
        execute_non_query(env, stmt)

def get_all_dict(env, sql, *params):
    with env.db_transaction as db:
        cur = db.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        desc = cur.description

        results = []
        for row in rows:
            row_dict = {}
            for field, col in zip(row, desc):
                row_dict[col[0]] = field
            results.append(row_dict)
        return results
