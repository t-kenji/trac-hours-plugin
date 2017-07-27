# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

from trac.core import Component, implements
from trac.perm import PermissionCache
from trac.ticket.api import ITicketChangeListener, ITicketManipulator
from trac.web.api import ITemplateStreamFilter
from genshi.builder import tag
from genshi.filters.transform import Transformer

from hours import TracHoursPlugin

import re

try:
    from mail2trac.email2ticket import ReplyToTicket
    from mail2trac.interface import IEmailHandler
    from mail2trac.utils import emailaddr2user
except ImportError:
    ReplyToTicket = IEmailHandler = emailaddr2user = None


class TracHoursByComment(Component):

    if IEmailHandler:
        implements(IEmailHandler, ITicketChangeListener,
                   ITicketManipulator, ITemplateStreamFilter)
    else:
        implements(ITicketChangeListener,
                   ITicketManipulator, ITemplateStreamFilter)

    # for ticket comments: 1.5 hours or 1:30 hours
    hours_regex = r'(([0-9]+(\.[0-9]+)?)|([0-9]+:[0-5][0-9])) *hours'

    # for singular hours: 1 hour
    singular_hour_regex = r'((^)|(\s*))1 *hour((\W)|($))'

    # ITemplateStreamFilter methods

    def filter_stream(self, req, method, filename, stream, data):
        if filename == 'ticket.html' \
           and 'TICKET_ADD_HOURS' in req.perm(data['ticket'].resource):

            filter = Transformer('//div[@id="trac-add-comment"]//fieldset')

            field = tag.div(
                tag.label(u'Add work hours:', _for='field-work-hours'),
                tag.input(type='time', id='field-work-hours', name='field_work_hours')
                )
            stream = stream | filter.before(field)
        return stream

    # ITicketManipulator methods

    def prepare_ticket(self, req, ticket, fields, actions):
        pass

    def validate_ticket(self, req, ticket):
        if 'TICKET_ADD_HOURS' in req.perm('ticket', ticket.id):
            comment = req.args.get('comment')
            if comment:
                req.args['comment'] = self.munge_comment(comment, ticket)

            hours = req.args.get('field_work_hours')
            if 'submit' in req.args and hours:
                worklog = u'worked for {} hours'.format(hours)
                if comment:
                    self.add_hours_by_comment(worklog, ticket.id, req.authname)
                else:
                    req.args['comment'] = self.munge_comment(worklog, ticket)

        return []

    def munge_comment(self, comment, ticket):
        def replace(match, ticket=ticket):
            """
            callback for re.sub; this will markup the hours link
            """
            return u'[%s %s]' % (('/hours/%s' % ticket.id), match.group())

        comment = re.sub(self.hours_regex, replace, comment)
        comment = re.sub(self.singular_hour_regex, u'[/hours/%s 1 hour]'
                                                   % ticket.id, comment)
        return comment

    # IEmailHandler methods

    def match(self, message):
        reporter = emailaddr2user(self.env, message['from'])
        reply_to_ticket = ReplyToTicket(self.env)
        perm = PermissionCache(self.env, reporter)
        if 'TICKET_ADD_HOURS' in perm('ticket',
                                      reply_to_ticket.ticket(message).id):
            return bool(reply_to_ticket.ticket(message))
        else:
            return False

    def invoke(self, message, warnings):
        reply_to_ticket = ReplyToTicket(self.env)
        ticket = reply_to_ticket.ticket(message)
        payload = message.get_payload()
        if isinstance(payload, basestring):
            if message.get('Content-Disposition', 'inline') == 'inline' \
                    and message.get_content_maintype() == 'text':
                message.set_payload(self.munge_comment(payload, ticket))
        else:
            for _message in payload:
                self.invoke(_message, warnings)
        return message

    # ITicketChangeListener methods

    def ticket_changed(self, ticket, comment, author, old_values):
        perm = PermissionCache(self.env, author)
        if perm.has_permission('TICKET_ADD_HOURS'):
            self.add_hours_by_comment(comment, ticket.id, author)

    def ticket_created(self, ticket):
        pass

    def ticket_deleted(self, ticket):
        TracHoursPlugin(self.env).delete_ticket_hours(ticket.id)

    # Internal methods

    def add_hours_by_comment(self, comment, ticket, worker):
        """
        add hours to a ticket via a comment string
        * comment : the comment string
        * ticket : the id of the ticket
        * worker : who worked the hours
        """
        trachours = TracHoursPlugin(self.env)
        for match in re.finditer(self.hours_regex, comment):
            hours = match.groups()[0]
            if ':' in hours:
                hours, minutes = hours.split(':')
                seconds = 3600.0*float(hours) + 60.0*float(minutes)
            else:
                seconds = 3600.0*float(hours)
            _comment = re.sub('\[/hours/[0-9]+ ' + self.hours_regex + '\]',
                              match.group(), comment)
            trachours.add_ticket_hours(ticket, worker, seconds,
                                       comments=_comment)

        for match in re.finditer(self.singular_hour_regex, comment):
            _comment = re.sub('\[/hours/[0-9]+ 1 hour\]', '1 hour', comment)
            trachours.add_ticket_hours(ticket, worker, 3600.0,
                                       comments=_comment)
