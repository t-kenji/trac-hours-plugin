# -*- coding: utf-8 -*-
#
# Copyright (C) 2012 Ryan J Ollos <ryan.j.ollos@gmail.com>
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

import shutil
import tempfile
import unittest
from datetime import datetime

from trac.test import EnvironmentStub, Mock
from trac.ticket.model import Ticket
from trac.util.translation import _

from trachours.hours import TracHoursPlugin
from trachours.setup import SetupTracHours

from trachours.tests import revert_trachours_schema_init


class HoursTicketManipulatorTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(default_data=True,
                                   enable=['trac.*', 'trachours.*'])
        self.env.path = tempfile.mkdtemp()
        setup = SetupTracHours(self.env)
        with self.env.db_transaction as db:
            setup.upgrade_environment(db=db)
        self.hours_thp = TracHoursPlugin(self.env)

    def tearDown(self):
        self.env.reset_db()
        revert_trachours_schema_init(self.env)
        shutil.rmtree(self.env.path)

    def test_add_ticket_hours(self):
        tid = 1
        worker = 'joe'
        seconds_worked = 120
        #when = datetime.now(utc)
        when = datetime.now()
        comment = "joe's hours"
        self.hours_thp.add_ticket_hours(tid, worker, seconds_worked, None,
                                        when, comment)
        hours = self.hours_thp.get_ticket_hours(tid)
        self.assertEqual(1, hours[0]['id'])
        self.assertEqual(tid, hours[0]['ticket'])
        self.assertEqual(worker, hours[0]['worker'])
        self.assertEqual(seconds_worked, hours[0]['seconds_worked'])
        self.assertEqual(worker, hours[0]['submitter'])
        # FIXME: See FIXME in add_ticket_hours
        #self.assertEqual(to_timestamp(when), hours[0]['time_started'])
        self.assertEqual(comment, hours[0]['comments'])

    def test_delete_ticket_hours(self):
        tid = 1
        self.hours_thp.add_ticket_hours(tid, 'joe', 180)
        self.hours_thp.add_ticket_hours(tid, 'jim', 600)
        self.hours_thp.delete_ticket_hours(tid)
        hours = self.hours_thp.get_ticket_hours(tid)
        self.assertEqual([], hours)

    def test_prepare_ticket_exists(self):
        req = ticket = fields = actions = {}
        self.assertEquals(None,
            self.hours_thp.prepare_ticket(req, ticket, fields, actions))

    def test_validate_ticket_negativevalue_returnstuple(self):
        req = {}
        ticket = Ticket(self.env)
        ticket['estimatedhours'] = '-1'
        self.assertTrue(ticket.get_value_or_default('estimatedhours'))
        msg = _("Please enter a positive value for Estimated Hours")
        self.assertEquals(msg, self.hours_thp.validate_ticket(req, ticket)[0][1])

    def test_validate_ticket_notanumber_returnstuple(self):
        req = {}
        ticket = Ticket(self.env)
        ticket['estimatedhours'] = 'a'
        msg = _("Please enter a number for Estimated Hours")
        self.assertEquals(msg, self.hours_thp.validate_ticket(req, ticket)[0][1])

    def test_validate_ticket_empty_setstozero(self):
        req = {}
        ticket = Ticket(self.env)
        ticket['estimatedhours'] = ''
        self.hours_thp.validate_ticket(req, ticket)
        self.assertEquals('0', ticket['estimatedhours'])

    def test_validate_ticket_fielddoesnotexist_returnstuple(self):
        req = {}
        self.env.config.remove('ticket-custom', 'estimatedhours')
        self.env.config.save()
        ticket = Ticket(self.env)
        msg = _("""The field is not defined. Please check your configuration.""")
        self.assertEquals(msg, self.hours_thp.validate_ticket(req, ticket)[0][1])


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(HoursTicketManipulatorTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='test_suite')
