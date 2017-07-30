# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# Copyright (C) 2017 Emerson Castaneda <emecas@gmail.com>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

from datetime import datetime

from trac.core import Component, implements
from trac.db import DatabaseManager
from trac.db.schema import Column, Index, Table
from trac.env import IEnvironmentSetupParticipant

from trac.util.datefmt import to_utimestamp, utc
from trac.util.translation import domain_functions

from trachours.usermanual import *

#try:
#    from xmlrpc import *
#except:
#    pass


_, tag_, N_, ngettext, add_domain = domain_functions('trachours',
    '_', 'tag_', 'N_', 'ngettext', 'add_domain')


class SetupTracHours(Component):

    implements(IEnvironmentSetupParticipant)

    # totalhours be a computed field, but computed fields don't yet exist for trac
    custom_fields = {
        'estimatedhours': {
            'type': 'text',
            'label': _('Estimated Hours'),
            'value': '0'
        },
        'totalhours': {
            'type': 'text',
            'label': _('Total Hours'),
            'value': '0'
        }
    }

    # IEnvironmentSetupParticipant methods

    def __init__(self):
        from pkg_resources import resource_filename

        add_domain(self.env.path, resource_filename(__name__, 'locale'))
        self.db_installed_version =  self.version()
        self.db_version = 4

    def environment_created(self):
        if self.environment_needs_upgrade():
            self.upgrade_environment()

    def environment_needs_upgrade(self):
        return self._system_needs_upgrade()

    def _system_needs_upgrade(self):
       return self.db_installed_version < self.db_version

    def upgrade_environment(self, db=None):
        for version in range(self.version(), len(self.steps)):
            for step in self.steps[version]:
                step(self)

        cursor = db.cursor()
        cursor.execute("""UPDATE system SET value='%s' WHERE name='trachours.db_version'""" % len(self.steps))

        self.db_installed_version = len(self.steps)

    def _needs_user_manual(self):
        with self.env.db_transaction as db:
            cursor = db.cursor()
            cursor.execute("""
                SELECT MAX(version) FROM wiki WHERE name=%s
                   """, (user_manual_wiki_title,))

            #rows = self.env.db_query("""
            #        SELECT MAX(version) FROM wiki WHERE name=%s
            #        """, (user_manual_wiki_title,)

            for maxversion in cursor.fetchone():#dict(data=cursor.fetchall(), desc=cursor.description):
                maxversion = int(maxversion) if isinstance( maxversion, ( int, long ) ) else 0
                break
            else:
                maxversion = 0
        return maxversion < user_manual_version


    def _do_user_man_update(self):
        when = to_utimestamp(datetime.now(utc))
        with self.env.db_transaction as db:
            cursor = db.cursor()
            cursor.execute("""
                    INSERT INTO wiki
                  (name,version,time,author,ipnr,text,comment,readonly)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (user_manual_wiki_title, user_manual_version,
                      when, 'TracHours Plugin', '127.0.0.1',
                      user_manual_content, '', 0,))

    def version(self):
        with self.env.db_transaction as db:
            cursor = db.cursor()
            cursor.execute("""
            SELECT value FROM system WHERE name = 'trachours.db_version'
            """)
            version = cursor.fetchone()
        if version:
            return int(version[0])
        return 0

    def create_db(self):
        ticket_time_table = Table('ticket_time', key='id')[
            Column('id', auto_increment=True),
            Column('ticket', type='int'),
            Column('time_submitted', type='int'),
            Column('worker'),
            Column('submitter'),
            Column('time_started', type='int'),
            Column('seconds_worked', type='int'),
            Column('comments'),
            Index(['ticket']),
            Index(['worker']),
            Index(['time_started'])]

        with self.env.db_transaction as db:
            conn, _ = DatabaseManager(self.env).get_connector()
            stmts = conn.to_sql(ticket_time_table)
            for stmt in stmts:
                cur = db.cursor()
                cur.execute(stmt)

            cur = db.cursor()
            cur.execute("""
                INSERT INTO system (name, value) 
                VALUES ('trachours.db_version', '1')
                """)

    def update_custom_fields(self):
        ticket_custom = 'ticket-custom'

        for name in self.custom_fields:
            field = self.custom_fields[name].copy()
            field_type = field.pop('type', 'text')
            if not self.config.get(ticket_custom, field_type):
                self.config.set(ticket_custom, name, field_type)
            for key, value in field.items():
                self.config.set(ticket_custom, '%s.%s' % (name, key), value)
        self.config.save()

    def add_query_table(self):
        time_query_table = Table('ticket_time_query', key='id')[
            Column('id', auto_increment=True),
            Column('title'),
            Column('description'),
            Column('query')]

        with self.env.db_transaction as db:
            conn, _ = DatabaseManager(self.env).get_connector()
            stmts = conn.to_sql(time_query_table)
            for stmt in stmts:
                cur = db.cursor()
                cur.execute(stmt)

    def initialize_old_tickets(self):
        with self.env.db_transaction as db:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO ticket_custom (ticket, name, value)
                SELECT id, 'totalhours', '0' FROM ticket WHERE id NOT IN (
                SELECT ticket FROM ticket_custom WHERE name='totalhours');
                """)

    def install_manual(self):
        if self._needs_user_manual():
            self._do_user_man_update()


    # ordered steps for upgrading
    steps = [
        [create_db, update_custom_fields],  # version 1
        [add_query_table],  # version 2
        [initialize_old_tickets],  # version 3
        [install_manual], # version 4
    ]
