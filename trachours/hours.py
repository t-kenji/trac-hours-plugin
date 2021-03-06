# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

import calendar
import csv
import re
import time
from StringIO import StringIO
from datetime import datetime, timedelta
from urllib import urlencode

from genshi.filters import Transformer
from trac.core import *
from trac.perm import IPermissionRequestor
from trac.ticket.api import ITicketManipulator, TicketSystem
from trac.ticket.model import Ticket
from trac.ticket.query import Query
from trac.util.datefmt import (
    format_date, parse_date, user_time, to_timestamp, utc
)
from trac.util.html import html as tag
from trac.util.translation import domain_functions
from trac.web.api import IRequestHandler, ITemplateStreamFilter
from trac.web.chrome import (
    Chrome, INavigationContributor, ITemplateProvider, add_ctxtnav,
    add_link, add_script, add_stylesheet, add_warning, prevnext_nav,
    web_context
)

from sqlhelper import *
from utils import get_all_users

_, tag_, N_, ngettext, add_domain = \
    domain_functions('trachours', '_', 'tag_', 'N_', 'ngettext', 'add_domain')


class TracHoursPlugin(Component):
    implements(INavigationContributor,
               IPermissionRequestor,
               IRequestHandler,
               ITemplateProvider,
               ITemplateStreamFilter,
               ITicketManipulator)

    date_format = '%B %d, %Y'  # XXX should go to api ?
    fields = [dict(name='id', label=_('Ticket')),
              # note that ticket_time id is clobbered by ticket id
              dict(name='seconds_worked', label=_('Hours Worked')),
              dict(name='worker', label=_('Worker')),
              dict(name='submitter', label=_('Work submitted by')),
              dict(name='time_started', label=_('Work done on')),
              dict(name='time_submitted', label=_('Work recorded on'))]

    def __init__(self):
        from pkg_resources import resource_filename

        add_domain(self.env.path, resource_filename(__name__, 'locale'))

    def tickets_with_hours(self):
        """return all ticket.ids with hours"""
        return set(get_column(self.env, 'ticket_time', 'ticket'))

    def update_ticket_hours(self, ids):
        """
        update the totalhours ticket field from the tracked hours information
        * ids: ticket ids (list)
        """
        results = get_all_dict(self.env, """
            SELECT SUM(seconds_worked) AS t, ticket
            FROM ticket_time WHERE ticket IN (%s) GROUP BY ticket
            """ % ",".join(map(str, ids)))

        # If no work has been logged for a ticket id, nothing will be
        # returned for that id, but we want it to return 0
        for id in ids:
            id_ismember = False
            for result in results:
                if id == result['ticket']:
                    id_ismember = True
            if not id_ismember:
                results.append({'ticket': id, 't': 0})

        for result in results:
            formatted = '%8.2f' % (float(result['t']) / 3600.0)
            execute_non_query(self.env, """
                UPDATE ticket_custom SET value=%s
                WHERE name='totalhours' AND ticket=%s
                """, formatted, result['ticket'])

    def get_ticket_hours(self, ticket_id, from_date=None, to_date=None,
                         worker_filter=None):

        if not ticket_id:
            return []

        args = []
        if isinstance(ticket_id, int):
            where = "ticket = %s"
            args.append(ticket_id)
        else:
            # Note the lack of args.  This is because there's no way to do
            # a placeholder for a list that I can see.
            where = "ticket IN (%s)" % ",".join(map(str, ticket_id))

        if from_date:
            where += " AND time_started >= %s"
            args.append(int(time.mktime(from_date.timetuple())))

        if to_date:
            where += " AND time_started < %s"
            args.append(int(time.mktime(to_date.timetuple())))

        if worker_filter and worker_filter != '*any':
            where += " AND worker = %s"
            args.append(worker_filter)

        return get_all_dict(self.env, """
            SELECT * FROM ticket_time WHERE %s
            """ % where, *args)

    def get_total_hours(self, ticket_id):
        """return total SECONDS associated with ticket_id"""
        return sum([hour['seconds_worked'] for hour in
                    self.get_ticket_hours(int(ticket_id))])

    def add_ticket_hours(self, tid, worker, seconds_worked, submitter=None,
                         time_started=None, comments=''):
        """
        add hours to a ticket:
        * tid : id of the ticket
        * worker : who did the work on the ticket
        * seconds_worked : how much work was done, in seconds
        * submitter : who recorded the work, if different from the worker
        * time_started : when the work was begun (a Datetime object) if other
                         than now
        * comments : comments to record
        """

        # prepare the data
        if submitter is None:
            submitter = worker
        if time_started is None:
            time_started = datetime.now()
            # FIXME: timestamps should be in UTC
            # time_started = datetime.now(utc)
        # time_started = to_utimestamp(time_started)
        time_started = int(time.mktime(time_started.timetuple()))
        comments = comments.strip()

        # execute the SQL
        sql = """INSERT INTO ticket_time(ticket,
                                         time_submitted,
                                         worker,
                                         submitter,
                                         time_started,
                                         seconds_worked,
                                         comments) VALUES
(%s, %s, %s, %s, %s, %s, %s)"""
        execute_non_query(self.env, sql, tid, int(time.time()),
                          worker, submitter, time_started,
                          seconds_worked, comments)

        # update the hours on the ticket
        self.update_ticket_hours([tid])

    def delete_ticket_hours(self, tid):
        """Delete hours for a ticket.

        :param tid: id of the ticket
        """
        execute_non_query(self.env, """
            DELETE FROM ticket_time WHERE ticket=%s""", tid)

    # IPermissionRequestor methods
    def get_permission_actions(self):
        return ['TICKET_ADD_HOURS', 'TICKET_VIEW_HOURS']

    # IRequestHandler methods

    def match_request(self, req):
        path = req.path_info.rstrip('/')
        if not path.startswith('/hours'):
            return False
        if path == '/hours':
            return True
        if path.startswith('/hours/query'):
            return True
        ticket_id = path.split('/hours/', 1)[-1]
        try:
            int(ticket_id)
            return True
        except ValueError:
            return False

    def process_request(self, req):
        req.perm.require('TICKET_VIEW_HOURS')
        path = req.path_info.rstrip('/')

        if path == '/hours':
            return self.process_timeline(req)

        if path.startswith('/hours/query'):
            return self.save_query(req)

        # assume a ticket if the other handlers don't work
        return self.process_ticket(req)

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'hours'

    def get_navigation_items(self, req):
        if 'TICKET_VIEW_HOURS' in req.perm:
            yield ('mainnav', 'hours',
                   tag.a(_("Hours"), href=req.href.hours(), accesskey='H'))

    # ITemplateProvider methods

    def get_htdocs_dirs(self):
        return []

    def get_templates_dirs(self):
        from pkg_resources import resource_filename
        return [resource_filename(__name__, 'templates')]

    # ITicketManipulator methods

    def prepare_ticket(self, req, ticket, fields, actions):
        """Not currently called, but should be provided for future
        compatibility.
        """

    def validate_ticket(self, req, ticket):
        """Validate a ticket after it's been populated from user input.

        Must return a list of `(field, message)` tuples, one for each problem
        detected. `field` can be `None` to indicate an overall problem with
        the ticket. Therefore, a return value of `[]` means everything
        is OK.
        """

        # Check that 'estimatedhours' is a custom-field
        if ticket.get_value_or_default('estimatedhours') is None:
            msg = _("The field is not defined. Please check your "
                    "configuration.")
            return [('estimatedhours', msg)]

        # Check that user entered a positive number
        if ticket['estimatedhours']:
            try:
                float(ticket['estimatedhours'])
            except ValueError:
                msg = _("Please enter a number for Estimated Hours")
                return [('estimatedhours', msg)]
            if float(ticket['estimatedhours']) < 0:
                msg = _("Please enter a positive value for Estimated Hours")
                return [('estimatedhours', msg)]
        else:
            ticket['estimatedhours'] = '0'

        return []

    # ITemplateStreamFilter methods

    def filter_stream(self, req, method, filename, stream, data):
        """
        filter hours and estimated hours fields to have them
        correctly display on the ticket.html
        """

        if filename == 'ticket.html' and 'TICKET_VIEW_HOURS' in req.perm:
            field = [field for field in data['fields']
                     if field['name'] == 'totalhours']
            if field:
                total_hours = field[0]
                ticket_id = data['ticket'].id
                if ticket_id is None:  # new ticket
                    field = '0'
                else:
                    sum_total_hours = self.get_total_hours(ticket_id)
                    own_hours = '%.2f' % (sum_total_hours / 3600.0)
                    field = tag.a(own_hours,
                                  href=req.href('hours', ticket_id),
                                  title=_("hours for ticket {id}").format(id=ticket_id))
                    if self.env.is_component_enabled('ticketrels.api.TicketRelationsSystem'):
                        def _get_children_hours(parent_id):
                            hours = 0
                            children = []
                            for parent, child in self.env.db_query("""
                                    SELECT oneself, ticket from ticketrels
                                    WHERE oneself=%s AND relations='child'
                                    """, (parent_id, )):
                                children.append(child)
                                hours += self.get_total_hours(child)

                            for _id in children:
                                hours += _get_children_hours(_id)

                            return hours

                        sum_total_hours += _get_children_hours(ticket_id)
                        sum_hours = ' (%.2f h)' % (sum_total_hours / 3600.0)
                        field = tag.span(field,
                                         tag.span(sum_hours,
                                                  title=_('sum hours of ticket {id} and children').format(id=ticket_id)))
                total_hours['rendered'] = field
                stream |= Transformer(
                    "//input[@id='field-totalhours']").replace(field)

        return stream

    # Internal methods

    # Methods for date format

    def format_hours(self, seconds):
        """returns a formatted string of the number of hours"""
        precision = 2
        return str(round(seconds / 3600., precision))

    def format_hours_and_minutes(self, seconds):
        """returns a formatted string of the number of hours"""
        return '{hours:02}:{minutes:02}'.format(hours=seconds / 3600,
                                                minutes=(seconds % 3600) / 60)

    # Methods for the query interface

    def get_query(self, query_id):
        results = get_all_dict(self.env, """
            SELECT title, description, query FROM ticket_time_query WHERE id=%s
            """, query_id)
        if not results:
            raise KeyError(_("No such query {id}").format(id=query_id))
        return results[0]

    def get_columns(self):
        return ['seconds_worked', 'worker', 'submitter',
                'time_started', 'time_submitted']

    def get_default_columns(self):
        return ['time_started', 'seconds_worked', 'worker', ]

    def save_query(self, req):
        data = {}
        if req.method == 'POST':
            assert req.perm.has_permission('TICKET_ADD_HOURS')
            id_ = int(req.args['id'])
            if id_:
                # save over an existing query
                execute_non_query(self.env, """
                    UPDATE ticket_time_query SET title = %s, description = %s,
                    QUERY = %s WHERE id = %s
                    """, req.args['title'], req.args['description'],
                                  req.args['query'], id_)

            else:
                # create a new query
                execute_non_query(self.env, """
                    INSERT INTO ticket_time_query(title, description, query)
                    VALUES (%s, %s, %s)
                    """, req.args['title'], req.args['description'],
                                  req.args['query'])
                # fixme: duplicate title?
                id_ = get_scalar(self.env, """
                    SELECT id FROM ticket_time_query WHERE title = %s
                    """, 0, req.args['title'])

            req.redirect(req.href('hours') + '?query_id=%s&%s'
                         % (id_, req.args['query']))

        action = req.args.get('action')
        if action == 'new':
            data['query'] = dict(id='0',
                                 description='',
                                 query=req.args['query'])
        elif action == "edit":
            data['query'] = self.get_query(int(req.args['query_id']))
            data['query']['id'] = int(req.args['query_id'])

        else:
            # list
            data['queries'] = get_all_dict(self.env, """
                SELECT id, title, description, query FROM ticket_time_query
                """)
            return 'hours_listqueries.html', data, 'text/html'
        return 'hours_savequery.html', data, 'text/html'

    def process_query(self, req):
        """redict to save, edit or delete a query based on arguments"""
        if req.args.get('save_query'):
            del req.args['save_query']
            if 'query_id' in req.args:
                del req.args['query_id']
            args = urlencode(req.args)
            req.redirect(
                req.href(req.path_info) + "/query?action=new&" + args)
            return True
        elif req.args.get('edit_query'):
            del req.args['edit_query']
            args = urlencode(req.args)
            req.redirect(
                req.href(req.path_info) + "/query?action=edit&" + args)
            return True
        elif req.args.get('delete_query'):
            assert req.perm.has_permission('TICKET_ADD_HOURS')
            query_id = req.args['query_id']
            sql = "DELETE FROM ticket_time_query WHERE id=%s"
            execute_non_query(self.env, sql, query_id)
            if 'query_id' in req.args:
                del req.args['query_id']
            return False

    def process_timeline(self, req):
        """/hours view"""

        if 'update' in req.args:
            # Reset session vars
            for var in ('query_constraints', 'query_time', 'query_tickets'):
                if var in req.session:
                    del req.session[var]

        if self.process_query(req):
            # The user has clicked on the 'Save Query' button; redirect them
            return

        # Lifted from trac.ticket.query.QueryModule.process_request

        req.perm.require('TICKET_VIEW')

        constraints = self._get_constraints(req)
        if not constraints and 'order' not in req.args:
            # If no constraints are given in the URL, use the default ones.
            if req.authname and req.authname != 'anonymous':
                qstring = 'status!=bogus'
                user = req.authname
            else:
                email = req.session.get('email')
                name = req.session.get('name')
                qstring = 'status!=bogus'
                user = email or name or None

            if user:
                qstring = qstring.replace('$USER', user)
            self.log.debug('QueryModule: Using default query: %s',
                           str(qstring))

            constraints = Query.from_string(self.env, qstring).constraints
            # Ensure no field constraints that depend on $USER are used
            # if we have no username.

            for constraint_set in constraints:
                for field, vals in constraint_set.items():
                    for val in vals:
                        if val.endswith('$USER'):
                            del constraint_set[field]

        cols = req.args.getlist('col')
        if not cols:
            cols = ['id', 'summary'] + self.get_default_columns()

        # Since we don't show 'id' as an option to the user,
        # we need to re-insert it here.
        if cols and 'id' not in cols:
            cols.insert(0, 'id')

        rows = req.args.getlist('row')
        max = 0  # unlimited number of tickets

        # compute estimated hours even if not selected for columns
        rm_est_hours = False
        if 'estimatedhours' not in cols:
            cols.append('estimatedhours')
            rm_est_hours = True
        query = Query(self.env, req.args.get('report'),
                      constraints, cols, req.args.get('order'),
                      'desc' in req.args, req.args.get('group'),
                      'groupdesc' in req.args, 'verbose' in req.args,
                      rows,
                      req.args.get('page'),
                      max)
        if rm_est_hours:  # if not in the columns, remove estimatedhours
            cols.pop()

        return self.display_html(req, query)

    # Methods lifted from trac.ticket.query

    def _get_constraints(self, req):
        constraints = {}

        ticket_fields = [f['name'] for f in
                         TicketSystem(self.env).get_ticket_fields()]
        ticket_fields.append('id')

        # For clients without JavaScript, we remove constraints here if
        # requested
        remove_constraints = {}
        to_remove = [k[10:] for k in req.args.keys()
                     if k.startswith('rm_filter_')]
        if to_remove:  # either empty or containing a single element
            match = re.match(r'(\w+?)_(\d+)$', to_remove[0])
            if match:
                remove_constraints[match.group(1)] = int(match.group(2))
            else:
                remove_constraints[to_remove[0]] = -1

        for field in [k for k in req.args.keys() if k in ticket_fields]:
            vals = req.args.getlist(field)
            if vals:
                mode = req.args.get(field + '_mode')
                if mode:
                    vals = [mode + x for x in vals]
                if field in remove_constraints:
                    idx = remove_constraints[field]
                    if idx >= 0:
                        del vals[idx]
                        if not vals:
                            continue
                    else:
                        continue
                constraints[field] = vals

        return constraints

    def get_href(self, req, query, args, *a, **kw):
        base = query.get_href(*a, **kw)
        cols = args.get('col')
        if cols:
            if isinstance(cols, basestring):
                cols = [cols]
            base += '&' + "&".join("col=%s" % col
                                   for col in cols if col not in query.cols)

        if 'worker_filter' in args:
            base += '&worker_filter={}'.format(args.get('worker_filter'))

        now = datetime.now()
        if 'from_date' in args:
            base += '&{}'.format(urlencode({
                    'from_date': args['from_date'],
                    'to_date': args.get('to_date', user_time(req, format_date, now))
                }))
        return base.replace('/query', '/hours')

    def display_html(self, req, query):
        """returns the HTML according to a query for /hours view"""

        # The most recent query is stored in the user session;
        orig_list = None
        orig_time = datetime.now(utc)
        query_time = int(req.session.get('query_time', 0))
        query_time = datetime.fromtimestamp(query_time, utc)
        query_constraints = unicode(query.constraints)
        if query_constraints != req.session.get('query_constraints') \
                or query_time < orig_time - timedelta(hours=1):
            tickets = query.execute(req)
            # New or outdated query, (re-)initialize session vars
            req.session['query_constraints'] = query_constraints
            req.session['query_tickets'] = ' '.join(str(t['id'])
                                                    for t in tickets)
        else:
            orig_list = [int(id_)
                         for id_
                         in req.session.get('query_tickets', '').split()]
            tickets = query.execute(req, cached_ids=orig_list)
            orig_time = query_time

        context = web_context(req, 'query')
        ticket_data = query.template_data(context, tickets, orig_list,
                                          orig_time, req)

        # For clients without JavaScript, we add a new constraint here if
        # requested
        constraints = ticket_data['clauses'][0]
        if 'add' in req.args:
            field = req.args.get('add_filter')
            if field:
                constraint = constraints.setdefault(field, {})
                constraint.setdefault('values', []).append('')
                # FIXME: '' not always correct (e.g. checkboxes)

        req.session['query_href'] = query.get_href(context.href)
        req.session['query_time'] = to_timestamp(orig_time)
        req.session['query_tickets'] = ' '.join([str(t['id'])
                                                 for t in tickets])

        # data dictionary for genshi
        data = {}

        # get data for saved queries
        query_id = req.args.get('query_id')
        if query_id:
            try:
                query_id = int(query_id)
            except ValueError:
                add_warning(req,
                    _("query_id should be an integer, you put '{id}'").format(
                        id=query_id))
                query_id = None
        if query_id:
            data['query_id'] = query_id
            query_data = self.get_query(query_id)

            data['query_title'] = query_data['title']
            data['query_description'] = query_data['description']

        data.setdefault('report', None)
        data.setdefault('description', None)

        data['all_columns'] = query.get_all_columns() + self.get_columns()
        # Don't allow the user to remove the id column
        data['all_columns'].remove('id')
        data['all_textareas'] = query.get_all_textareas()

        # need to re-get the cols because query will remove our fields
        cols = req.args.getlist('col')
        if not cols:
            cols = query.get_columns() + self.get_default_columns()
        data['col'] = cols

        now = datetime.now()
        # get the date range for the query
        if 'from_date' in req.args:
            from_date = user_time(req, parse_date, req.args['from_date'])
        else:
            from_date = datetime(now.year, now.month, now.day) # today, by default

        if 'to_date' in req.args:
            to_date = user_time(req, parse_date, req.args['to_date'])
            to_date = to_date + timedelta(hours=23, minutes=59, seconds=59)
        else:
            to_date = now

        data['prev_week'] = from_date - timedelta(days=7)
        data['months'] = list(enumerate(calendar.month_name))
        data['years'] = range(now.year, now.year - 10, -1)
        data['days'] = range(1, 32)
        data['users'] = get_all_users(self.env)
        data['cur_worker_filter'] = req.args.get('worker_filter', req.authname)

        data['from_date'] = from_date
        data['to_date'] = to_date

        ticket_ids = [t['id'] for t in tickets]

        # generate data for ticket_times
        time_records = self.get_ticket_hours(ticket_ids, from_date=from_date,
                                             to_date=to_date,
                                             worker_filter=data[
                                                 'cur_worker_filter'])

        data['query'] = ticket_data['query']
        data['context'] = ticket_data['context']
        data['row'] = ticket_data['row']
        if 'comments' in req.args.get('row', []):
            data['row'].append('comments')
        data['constraints'] = ticket_data['clauses']

        our_labels = dict([(f['name'], f['label']) for f in self.fields])
        labels = TicketSystem(self.env).get_ticket_field_labels()
        labels.update(our_labels)
        data['labels'] = labels

        order = req.args.get('order')
        desc = bool(req.args.get('desc'))
        data['order'] = order
        data['desc'] = desc

        args = dict(req.args)
        args['col'] = cols
        if data['cur_worker_filter'] != '*any':
            args['worker_filter'] = data['cur_worker_filter']
        headers = [{'name': col,
                    'label': labels.get(col),
                    'href': self.get_href(req, query, args,
                                          context.href,
                                          order=col,
                                          desc=(col == order and not desc)
                                          )
                    } for col in cols]

        data['headers'] = headers

        data['fields'] = ticket_data['fields']
        data['modes'] = ticket_data['modes']

        # group time records
        time_records_by_ticket = {}
        for record in time_records:
            id_ = record['ticket']
            if id_ not in time_records_by_ticket:
                time_records_by_ticket[id_] = []

            time_records_by_ticket[id_].append(record)

        data['extra_group_fields'] = dict(
            ticket=dict(name='ticket', type='select', label='Ticket'),
            worker=dict(name='worker', type='select', label='Worker'))

        num_items = 0
        data['groups'] = []

        # merge ticket data into ticket_time records
        for key, tickets in ticket_data['groups']:
            ticket_times = []
            for ticket in tickets:
                records = time_records_by_ticket.get(ticket['id'], [])
                [rec.update(ticket) for rec in records]
                ticket_times += records

            # sort ticket_times, if needed
            if order in our_labels:
                ticket_times.sort(key=lambda x: x[order], reverse=desc)
            if ticket_times:
                data['groups'].append((key, ticket_times))
                num_items += len(ticket_times)

        data['double_count_warning'] = ''

        # group by ticket id or other time_ticket fields if necessary
        if req.args.get('group') in data['extra_group_fields']:
            query.group = req.args.get('group')
            if not query.group == "id":
                data['double_count_warning'] = \
                    _("Warning: estimated hours may be counted more than " \
                    "once if a ticket appears in multiple groups")

            tickets = data['groups'][0][1]
            groups = {}
            for time_rec in tickets:
                key = time_rec[query.group]
                if key not in groups:
                    groups[key] = []
                groups[key].append(time_rec)
            data['groups'] = sorted(groups.items())

        total_times = dict(
            (k, self.format_hours(sum(rec['seconds_worked'] for rec in v)))
            for k, v in data['groups'])
        total_estimated_times = {}
        for key, records in data['groups']:
            seen_tickets = set()
            est = 0
            for record in records:
                # do not double-count tickets
                id_ = record['ticket']
                if id_ in seen_tickets:
                    continue
                seen_tickets.add(id_)
                estimatedhours = record.get('estimatedhours') or 0
                try:
                    estimatedhours = float(estimatedhours)
                except ValueError:
                    estimatedhours = 0
                est += estimatedhours * 3600
            total_estimated_times[key] = self.format_hours(est)

        data['total_times'] = total_times
        data['total_estimated_times'] = total_estimated_times

        # format records
        for record in time_records:
            if 'seconds_worked' in record:
                record['seconds_worked'] = self.format_hours(
                    record['seconds_worked'])  # XXX misleading name
            if 'time_started' in record:
                record['time_started'] = user_time(req,
                                                   format_date,
                                                   record['time_started'])
            if 'time_submitted' in record:
                record['time_submitted'] = user_time(req,
                                                     format_date,
                                                     record['time_submitted'])

        data['query'].num_items = num_items
        data['labels'] = TicketSystem(self.env).get_ticket_field_labels()
        data['labels'].update(labels)
        data['can_add_hours'] = req.perm.has_permission('TICKET_ADD_HOURS')

        from multiproject import MultiprojectHours
        data['multiproject'] = self.env.is_component_enabled(MultiprojectHours)

        from web_ui import TracUserHours
        data['user_hours'] = self.env.is_component_enabled(TracUserHours)

        # return the rss, if requested
        if req.args.get('format') == 'rss':
            return self.queryhours2rss(req, data)

        # return the csv, if requested
        if req.args.get('format') == 'csv':
            self.queryhours2csv(req, data)

        # add rss link
        rss_href = req.href(req.path_info, format='rss')
        add_link(req, 'alternate', rss_href, _('RSS Feed'),
                 'application/rss+xml', 'rss')

        # add csv link
        add_link(req, 'alternate',
                 req.href(req.path_info, format='csv', **req.args), 'CSV',
                 'text/csv', 'csv')

        # add navigation of weeks
        prev_args = dict(req.args)
        next_args = dict(req.args)

        prev_args['col'] = cols
        prev_args['from_date'] = user_time(req, format_date, from_date - timedelta(days=7))
        prev_args['to_date'] = user_time(req, format_date, from_date)

        next_args['col'] = cols
        next_args['from_date'] = user_time(req, format_date, to_date)
        next_args['to_date'] = user_time(req, format_date, to_date + timedelta(days=7))

        add_link(req, 'prev', self.get_href(req, query, prev_args, context.href),
                 _("Prev Week"))
        add_link(req, 'next', self.get_href(req, query, next_args, context.href),
                 _("Next Week"))
        prevnext_nav(req, _("Prev Week"), _("Next Week"))

        if data['multiproject']:
            add_ctxtnav(req, _('Cross-Project Hours'),
                        req.href.hours('multiproject'))
        if data['user_hours']:
            add_ctxtnav(req, _('Hours by User'),
                        req.href.hours('user',
                                       from_date=user_time(req, format_date, from_date),
                                       to_date=user_time(req, format_date, to_date)))
        add_ctxtnav(req, _('Saved Queries'), req.href.hours('query/list'))

        add_stylesheet(req, 'common/css/report.css')
        add_script(req, 'common/js/query.js')
        Chrome(self.env).add_jquery_ui(req)

        return 'hours_timeline.html', data, 'text/html'

    def process_ticket(self, req):
        """process a request to /hours/<ticket number>"""

        # get the ticket
        path = req.path_info.rstrip('/')
        ticket_id = int(path.split('/')[-1])  # matches a ticket number
        ticket = Ticket(self.env, ticket_id)

        if req.method == 'POST':
            if 'addhours' in req.args:
                return self.do_ticket_change(req, ticket)
            if 'edithours' in req.args:
                return self.edit_ticket_hours(req, ticket)

        # XXX abstract date stuff as this is used multiple places
        now = datetime.now()
        months = [(i, calendar.month_name[i], i == now.month) for i in
                  range(1, 13)]
        years = range(now.year, now.year - 10, -1)
        days = [(i, i == now.day) for i in range(1, 32)]

        time_records = self.get_ticket_hours(ticket.id)
        time_records.sort(key=lambda x: x['time_started'], reverse=True)

        # add additional data for the template
        total = 0
        for record in time_records:
            record['date_started'] = user_time(req,
                                               format_date,
                                               record['time_started'])
            record['hours_worked'] = self.format_hours_and_minutes(
                record['seconds_worked'])
            total += record['seconds_worked']
        total = self.format_hours(total)

        data = {
            'can_add_hours': req.perm.has_permission('TICKET_ADD_HOURS'),
            'can_add_others_hours': req.perm.has_permission('TRAC_ADMIN'),
            'now': now,
            'users': get_all_users(self.env),
            'total': total,
            'ticket': ticket,
            'time_records': time_records
        }

        # return the rss, if requested
        if req.args.get('format') == 'rss':
            return self.tickethours2rss(req, data)

        # add rss link
        rss_href = req.href(req.path_info, format='rss')
        add_link(req, 'alternate', rss_href, _('RSS Feed'),
                 'application/rss+xml', 'rss')
        add_ctxtnav(req, _('Back to Ticket #{id}').format(id=ticket_id),
                    req.href.ticket(ticket_id))
        Chrome(self.env).add_jquery_ui(req)

        return 'hours_ticket.html', data, 'text/html'

    # Methods for transforming data to rss

    def queryhours2rss(self, req, data):
        """adapt data for /hours to RSS"""
        title = 'Hours worked on %s from %s to %s' \
                % (self.env.project_name,
                   data['from_date'].strftime(self.date_format),
                   data['to_date'].strftime(self.date_format))
        adapted = {'title': title}
        adapted['description'] = data['description'] or adapted['title']
        adapted['url'] = req.abs_href(req.path_info)
        items = []
        for group in data['groups']:
            for entry in group[1]:
                item = {}
                hours = float(entry['seconds_worked'])
                minutes = int(60 * (hours - int(hours)))
                hours = int(hours)
                title = _('{hours}:{mins:02} hours worked by {worker}').format(
                    hours=hours, mins=minutes, worker=entry['worker'])
                item['title'] = title
                item['description'] = title
                comments = entry.get('comments')
                if comments:
                    item['description'] += ': %s' % comments

                # the 'GMT' business is wrong
                # maybe use py2rssgen a la bitsyblog?
                time_started = datetime.strptime(entry['time_started'], '%B %d %Y')
                item['date'] = time_started.strftime('%a, %d %b %Y %T GMT')

                link = req.abs_href(req.path_info, entry['ticket'])
                item['guid'] = '%s#%s' % (link, entry['id'])
                item['url'] = item['guid']
                item['comments'] = req.abs_href('ticket', entry['ticket'])
                items.append(item)

        adapted['items'] = items
        return 'hours.rss', adapted, 'application/rss+xml'

    def tickethours2rss(self, req, data):
        """adapt data for /hours/<ticket number> to RSS"""
        adapted = {
            'title': _('Hours worked for ticket {id}').format(id=data['ticket']),
            'description': data['ticket']['summary']
        }

        # could put more information in the description

        link = req.abs_href(req.path_info)
        adapted['url'] = link
        items = []
        for record in data['time_records']:
            item = {}
            title = _('{hours} worked by {worker}').format(
                hours=record['hours_worked'], worker=record['worker'])
            item['title'] = title
            item['description'] = \
                '%s%s' % (title, (': %s' % record['comments']) or '')

            # the 'GMT' business is wrong
            # maybe use py2rssgen a la bitsyblog?
            item['date'] = datetime.fromtimestamp(
                float('%s' % record['time_started'])).strftime(
                '%a, %d %b %Y %T GMT')

            # could add these links to the template
            item['guid'] = '%s#%s' % (link, record['id'])
            item['url'] = item['guid']
            item['comments'] = req.abs_href('ticket', data['ticket'])

            items.append(item)
        adapted['items'] = items
        return 'hours.rss', adapted, 'text/xml'

    def queryhours2csv(self, req, data):
        """Transform hours to CSV"""
        buffer_ = StringIO()
        writer = csv.writer(buffer_)

        if data['cur_worker_filter'] != '*any':
            title = _('Hours for {cur_worker_filter}').format(**data)
        else:
            title = _('Hours')
        writer.writerow([title, req.abs_href()])

        constraint = data['constraints'][0]
        for key in constraint:
            if key == 'status' and constraint[key]['values'] == ['bogus']:
                continue  # XXX I actually have no idea why this is here
            writer.writerow([key] + constraint[key]['values'])
        writer.writerow([])

        writer.writerow(['From', 'To'])
        writer.writerow([data[i].strftime(self.date_format)
                         for i in 'from_date', 'to_date'])
        writer.writerow([])

        for groupname, results in data['groups']:
            if groupname:
                writer.writerow(unicode(groupname))
            writer.writerow([unicode(header['label']).encode('utf-8')
                             for header in data['headers']])
            for result in results:
                row = []
                for header in data['headers']:
                    value = result[header['name']]
                    row.append(unicode(value).encode('utf-8'))
                writer.writerow(row)
            writer.writerow([])

        req.send(buffer_.getvalue(), "text/csv")

    # Methods for adding and editing hours associated with tickets

    def do_ticket_change(self, req, ticket):
        """respond to a request to add hours to a ticket"""

        # permission check
        req.perm.require('TICKET_ADD_HOURS')

        #
        now = datetime.now()
        logged_in_user = req.authname
        worker = req.args.get('worker', logged_in_user)
        if not worker == logged_in_user:
            assert req.perm.has_permission('TICKET_ADMIN')

        # when the work was done
        if 'date' in req.args:
            started = user_time(req, parse_date, req.args['date'])
            if started == datetime(now.year, now.month, now.day, tzinfo=req.tz):
                # assumes entries made for today should be ordered
                # as they are entered
                started = now
        else:
            started = now

        # how much work was done
        match = re.match(r'([0-9]+:[0-5][0-9])', req.args.get('hours', '0:00'))
        try:
            hours, minutes = match.groups()[0].split(':')
            seconds_worked = int(float(hours) * 3600 + float(minutes) * 60)
        except:
            add_warning(req, _("Please enter a valid number of hours"))
        else:
            comments = req.args.get('comments', '').strip()

            self.add_ticket_hours(ticket.id, worker, seconds_worked,
                                  submitter=logged_in_user,
                                  time_started=started,
                                  comments=comments)
            if comments:
                comment = _("[{url} {hours}\thours] logged for {worker}: ''{comments}''").format(
                    url='/hours/{}'.format(ticket.id), hours=self.format_hours(seconds_worked),
                    worker=worker, comments=comments)

                # avoid adding hours that are (erroneously) noted in comments
                # see #4791
                comment = comment.replace(' ', '\t')

                ticket.save_changes(logged_in_user, comment)
                # XXX can/should this be used?:
                # index = len(ticket.get_changelog()) - 1

        location = req.environ.get('HTTP_REFERER', req.href(req.path_info))
        req.redirect(location)

    def edit_ticket_hours(self, req, ticket):
        """respond to a request to edithours for a ticket"""

        # permission check
        req.perm.require('TICKET_ADD_HOURS')

        # set hours
        new_hours = {}
        for field, newval in req.args.items():
            if field.startswith("hours_"):
                id_ = int(field[len("hours_"):])
                h, m = newval.split(':')
                new_hours[id_] = (int(float(h) * 3600 + float(m) * 60))

        # remove checked hours
        for field, newval in req.args.items():
            if field.startswith('rm_'):
                id_ = int(field[len('rm_'):])
                new_hours[id_] = 0

        hours = self.get_ticket_hours(ticket.id)
        tickets = set()

        # check permission if you're editing another's hours
        for hour in hours:
            tickets.add(hour['ticket'])

            id_ = hour['id']
            if id_ not in new_hours:
                continue

            if not hour['worker'] == req.authname:
                req.perm.require('TRAC_ADMIN')

        # perform the edits
        for hour in hours:
            tickets.add(hour['ticket'])

            id_ = hour['id']
            if id_ not in new_hours:
                continue

            if new_hours[id_]:
                execute_non_query(self.env, """
                    UPDATE ticket_time SET seconds_worked=%s WHERE id=%s
                    """, new_hours[id_], id_)
            else:
                execute_non_query(self.env, """
                    DELETE FROM ticket_time WHERE id=%s
                    """, id_)

        self.update_ticket_hours(tickets)

        req.redirect(req.href(req.path_info))
