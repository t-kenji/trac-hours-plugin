# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

import re
import calendar
import csv
import time
from StringIO import StringIO
from datetime import datetime, timedelta
from pkg_resources import parse_version

from genshi.filters import Transformer
from genshi.filters.transform import StreamBuffer
from trac import __version__ as TRAC_VERSION
from trac.core import *
from trac.ticket import Ticket
from trac.ticket.model import Milestone
from trac.util.datefmt import format_date, parse_date, user_time
from trac.util.html import html as tag
from trac.util.translation import _
from trac.web.api import IRequestHandler, ITemplateStreamFilter
from trac.web.chrome import (
    Chrome, ITemplateProvider, add_ctxtnav, add_link, add_stylesheet
)

from hours import TracHoursPlugin, _
from sqlhelper import get_all_dict
from utils import hours_format


class TracHoursRoadmapFilter(Component):

    implements(ITemplateStreamFilter)

    # ITemplateStreamFilter methods

    def filter_stream(self, req, method, filename, stream, data):
        """
        filter the stream for the roadmap (/roadmap)
        and milestones /milestone/<milestone>
        """

        if filename in ('roadmap.html', 'milestone_view.html') and \
                'TICKET_VIEW_HOURS' in req.perm:
            trac_hours = TracHoursPlugin(self.env)

            hours = {}

            milestones = data.get('milestones')
            this_milestone = None

            if milestones is None:
                # /milestone view : only one milestone
                milestones = [data['milestone']]
                this_milestone = milestones[0].name
                find_xpath = "//div[@class='milestone']/h1"
                xpath = "//div[@class='milestone']/div[1]"
            else:
                # /roadmap view
                find_xpath = "//*[@class='milestone']//h2/a"
                xpath = "//*[@class='milestone']/div[1]"

            for milestone in milestones:
                hours[milestone.name] = dict(totalhours=0.,
                                             estimatedhours=0., )

                tickets = [tid for tid, in self.env.db_query("""
                    SELECT id FROM ticket WHERE milestone=%s
                    """, (milestone.name,))]

                if tickets:
                    hours[milestone.name]['date'] = \
                        Ticket(self.env, tickets[0])['time']
                for ticket in tickets:
                    ticket = Ticket(self.env, ticket)

                    # estimated hours for the ticket
                    try:
                        estimated_hours = float(ticket['estimatedhours'])
                    except (ValueError, TypeError):
                        estimated_hours = 0.
                    hours[milestone.name]['estimatedhours'] += estimated_hours

                    # total hours for the ticket (seconds -> hours)
                    total_hours = trac_hours.get_total_hours(
                        ticket.id) / 3600.0
                    hours[milestone.name]['totalhours'] += total_hours

                    # update date for oldest ticket
                    if ticket['time'] < hours[milestone.name]['date']:
                        hours[milestone.name]['date'] = ticket['time']

            b = StreamBuffer()
            stream |= Transformer(find_xpath).copy(b).end().select(xpath). \
                append(
                self.MilestoneMarkup(req, b, hours, req.href, this_milestone))

        return stream

    class MilestoneMarkup(object):
        """Iterator for Transformer markup injection"""

        def __init__(self, req, buffer, hours, href, this_milestone):
            self.req = req
            self.buffer = buffer
            self.hours = hours
            self.href = href
            self.this_milestone = this_milestone

        def __iter__(self):
            if self.this_milestone is not None:  # for /milestone/xxx
                milestone = self.this_milestone
            else:
                milestone = self.buffer.events[3][1]
            if milestone not in self.hours.keys():
                return iter([])
            hours = self.hours[milestone]
            estimated_hours = hours['estimatedhours']
            total_hours = hours['totalhours']
            if not (estimated_hours or total_hours):
                return iter([])
            items = []
            if estimated_hours:
                if parse_version(TRAC_VERSION) < parse_version('1.0'):
                    items.append(tag.dt(_("Estimated Hours:")))
                    items.append(tag.dd(str(estimated_hours)))
                else:
                    items.append(tag.span(_("Estimated Hours: "),
                                          str(estimated_hours),
                                          class_="first interval"))
            date = hours['date']
            link = self.href("hours", milestone=milestone,
                             from_date=user_time(self.req, format_date, date))
            if parse_version(TRAC_VERSION) < parse_version('1.0'):
                items.append(tag.dt(tag.a(_("Total Hours:"), href=link)))
                items.append(
                    tag.dd(tag.a(hours_format % total_hours, href=link)))
                return iter(tag.dl(*items))
            else:
                items.append(tag.span(tag.a(_("Total Hours: "),
                                            hours_format % total_hours,
                                            href=link),
                                      class_='interval'))
                return iter(tag.p(*items, class_='legend'))


class TracUserHours(Component):

    implements(ITemplateProvider, IRequestHandler)

    # ITemplateProvider methods

    def get_htdocs_dirs(self):
        return []

    def get_templates_dirs(self):
        from pkg_resources import resource_filename
        return [resource_filename(__name__, 'templates')]

    # IRequestHandler methods

    def match_request(self, req):
        return req.path_info == '/hours/user' or \
               re.match(r'/hours/user/(?:tickets|dates)/(?:\w+)', req.path_info) is not None

    def process_request(self, req):
        req.perm.require('TICKET_VIEW_HOURS')
        if req.path_info.rstrip('/') == '/hours/user':
            return self.users(req)
        m = re.match(r'/hours/user/(?P<field>\w+)/(?P<user>\w+)',
                     req.path_info)
        field = m.group('field')
        user = m.group('user')

        if field == 'tickets':
            return self.user_by_ticket(req, user)
        elif field == 'dates':
            return self.user_by_date(req, user)

    # Internal methods

    def date_data(self, req, data):
        """data for the date"""
        now = datetime.now()
        data['days'] = range(1, 32)
        data['months'] = list(enumerate(calendar.month_name))
        data['years'] = range(now.year, now.year - 10, -1)
        if 'from_date' in req.args:
            from_date_raw = user_time(req, parse_date, req.args['from_date'])
        else:
            from_date_raw = datetime(now.year, now.month, now.day)
            from_date_raw = from_date_raw - timedelta(days=7)
        if 'to_date' in req.args:
            to_date_raw = user_time(req, parse_date, req.args['to_date'])
            to_date_raw = to_date_raw + timedelta(hours=23, minutes=59, seconds=59)
        else:
            to_date_raw = now

        data['from_date_raw'] = from_date_raw
        data['from_date'] = user_time(req, format_date, from_date_raw)
        data['to_date_raw'] = to_date_raw
        data['to_date'] = user_time(req, format_date, to_date_raw)
        data['prev_week'] = from_date_raw - timedelta(days=7)
        args = dict(req.args)
        args['from_date'] = user_time(req, format_date, data['prev_week'])
        args['to_date'] = user_time(req, format_date, from_date_raw)

        data['prev_url'] = req.href('/hours/user', **args)

    def users(self, req):
        """hours for all users"""

        data = {'hours_format': hours_format}

        # date data
        self.date_data(req, data)

        # milestone data
        milestone = req.args.get('milestone')
        milestones = Milestone.select(self.env)
        data['milestones'] = milestones

        # get the hours
        # trachours = TracHoursPlugin(self.env)
        # tickets = trachours.tickets_with_hours()
        hours = get_all_dict(self.env, """
            SELECT * FROM ticket_time
            WHERE time_started >= %s AND time_started < %s
            """, *[int(time.mktime(data[i].timetuple()))
                   for i in ('from_date_raw', 'to_date_raw')])
        details = req.args.get('details')
        worker_hours = {}
        if details != 'date':
            for entry in hours:
                worker = entry['worker']
                if worker not in worker_hours:
                    worker_hours[worker] = 0

                if milestone and milestone != \
                        Ticket(self.env, entry['ticket']).values.get('milestone'):
                    continue

                worker_hours[worker] += entry['seconds_worked']

            worker_hours = [(worker, seconds / 3600.)
                            for worker, seconds in sorted(worker_hours.items())]
        else:
            for entry in hours:
                date = user_time(req, format_date, entry['time_started'])
                worker = entry['worker']
                key = (date, worker)
                if key not in worker_hours:
                    worker_hours[key] = 0

                if milestone and milestone != \
                        Ticket(self.env, entry['ticket']).values.get('milestone'):
                    continue

                worker_hours[key] += entry['seconds_worked']

            worker_hours = [(key[0], key[1], seconds / 3600.)
                            for key, seconds in sorted(worker_hours.items())]
        data['details'] = details
        data['worker_hours'] = worker_hours
        data['total_hours'] = sum(hours[-1] for hours in worker_hours)

        if req.args.get('format') == 'csv':
            req.send(self.export_csv(req, data))

        add_stylesheet(req, 'common/css/report.css')
        if details == 'date':
            add_ctxtnav(req, _('Hours summary'),
                        req.href.hours('user',
                                       from_date=data['from_date'],
                                       to_date=data['to_date']))
        else:
            add_ctxtnav(req, _('Hours by date'),
                        req.href.hours('user',
                                       details='date',
                                       from_date=data['from_date'],
                                       to_date=data['to_date']))
        add_link(req, 'alternate', req.href(req.path_info, format='csv'),
                 'CSV', 'text/csv', 'csv')
        # add_link(req, 'prev', self.get_href(query, args, context.href),
        #         _('Prev Week'))
        # add_link(req, 'next', self.get_href(query, args, context.href),
        #         _('Next Week'))
        # prevnext_nav(req, _('Prev Week'), _('Next Week'))
        Chrome(self.env).add_jquery_ui(req)

        return 'hours_users.html', data, 'text/html'

    def user_by_ticket(self, req, user):
        """hours page for a single user"""
        data = {'hours_format': hours_format,
                'worker': user}
        self.date_data(req, data)
        args = [user]
        args += [int(time.mktime(data[i].timetuple()))
                 for i in ('from_date_raw', 'to_date_raw')]
        hours = get_all_dict(self.env, """
            SELECT * FROM ticket_time
            WHERE worker=%s AND time_started >= %s AND time_started < %s
            """, *args)
        worker_hours = {}
        for entry in hours:
            ticket = entry['ticket']
            if ticket not in worker_hours:
                worker_hours[ticket] = 0
            worker_hours[ticket] += entry['seconds_worked']

        data['tickets'] = dict([(i, Ticket(self.env, i))
                                for i in worker_hours.keys()])

        # sort by ticket number and convert to hours
        worker_hours = [(ticket_id, seconds / 3600.)
                        for ticket_id, seconds in
                        sorted(worker_hours.items())]

        data['worker_hours'] = worker_hours
        data['total_hours'] = sum(hours[1] for hours in worker_hours)

        if req.args.get('format') == 'csv':
            buffer = StringIO()
            writer = csv.writer(buffer)
            title = _("Hours for {user}").format(user=user)
            writer.writerow([title, req.abs_href()])
            writer.writerow([])
            writer.writerow(['From', 'To'])
            writer.writerow([data['from_date'], data['to_date']])
            writer.writerow([])
            writer.writerow(['Ticket', 'Hours'])
            for ticket, hours in worker_hours:
                writer.writerow([ticket, hours])

            req.send(buffer.getvalue(), 'text/csv')

        add_stylesheet(req, 'common/css/report.css')
        add_ctxtnav(req, _('Hours by Query'),
                    req.href.hours(from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_ctxtnav(req, _('Hours by User'),
                    req.href.hours('user',
                                   from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_ctxtnav(req, _('Hours by date'),
                    req.href.hours('user/dates/{}'.format(user),
                                   from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_link(req, 'alternate', req.href(req.path_info, format='csv'),
                 'CSV', 'text/csv', 'csv')
        Chrome(self.env).add_jquery_ui(req)

        return 'hours_user_by_ticket.html', data, 'text/html'

    def user_by_date(self, req, user):
        """hours page for a single user"""
        data = {'hours_format': hours_format,
                'worker': user}
        self.date_data(req, data)
        args = [user]
        args += [int(time.mktime(data[i].timetuple()))
                 for i in ('from_date_raw', 'to_date_raw')]
        hours = get_all_dict(self.env, """
            SELECT * FROM ticket_time
            WHERE worker=%s AND time_started >= %s AND time_started < %s
            """, *args)
        worker_hours = {}
        for entry in hours:
            date = user_time(req, format_date, entry['time_started'])
            ticket = entry['ticket']
            if date not in worker_hours:
                worker_hours[date] = {
                    'seconds': 0,
                    'tickets': [],
                }
            worker_hours[date]['seconds'] += entry['seconds_worked']
            if ticket not in worker_hours[date]['tickets']:
                worker_hours[date]['tickets'].append(ticket)

        data['tickets'] = dict([(entry['ticket'], Ticket(self.env, entry['ticket']))
                                for entry in hours])

        # sort by ticket number and convert to hours
        worker_hours = [(date, details['tickets'], details['seconds'] / 3600.)
                        for date, details in
                        sorted(worker_hours.items())]

        data['worker_hours'] = worker_hours
        data['total_hours'] = sum(hours[2] for hours in worker_hours)

        if req.args.get('format') == 'csv':
            buffer = StringIO()
            writer = csv.writer(buffer)
            title = _("Hours for {user}").format(user=user)
            writer.writerow([title, req.abs_href()])
            writer.writerow([])
            writer.writerow(['From', 'To'])
            writer.writerow([data['from_date'], data['to_date']])
            writer.writerow([])
            writer.writerow(['Ticket', 'Hours'])
            for date, tickets, hours in worker_hours:
                ids = ['#{}'.format(id) for id in tickets]
                writer.writerow([date, ','.join(ids), hours])

            req.send(buffer.getvalue(), 'text/csv')

        add_stylesheet(req, 'common/css/report.css')
        add_ctxtnav(req, _('Hours by Query'),
                    req.href.hours(from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_ctxtnav(req, _('Hours by User'),
                    req.href.hours('user',
                                   from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_ctxtnav(req, _('Hours by ticket'),
                    req.href.hours('user/tickets/{}'.format(user),
                                   from_date=data['from_date'],
                                   to_date=data['to_date']))
        add_link(req, 'alternate', req.href(req.path_info,
                                            format='csv',
                                            from_date=data['from_date'],
                                            to_date=data['to_date']),
                 'CSV', 'text/csv', 'csv')
        Chrome(self.env).add_jquery_ui(req)

        return 'hours_user_by_date.html', data, 'text/html'

    def export_csv(self, req, data, sep=',', mimetype='text/csv'):
        content = StringIO()
        content.write('\xef\xbb\xbf')  # BOM
        writer = csv.writer(content, delimiter=sep, quoting=csv.QUOTE_MINIMAL)

        title = _("Hours for {project}").format(project=self.env.project_name)
        writer.writerow([title, req.abs_href()])
        writer.writerow([])
        writer.writerow(['From', 'To'])
        writer.writerow([data['from_date'], data['to_date']])
        if data['milestone']:
            writer.writerow(['Milestone', data['milestone']])
        writer.writerow([])
        writer.writerow(['Worker', 'Hours'])
        for worker, hours in data['worker_hours']:
            writer.writerow([worker, hours])

        return content.getvalue(), '%s;text/csv' % mimetype
