# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

import calendar
import datetime

hours_format = '%.2f'


def get_all_users(env):
    """return the names of all known users in the trac environment"""
    return [i[0] for i in env.get_known_users()]


def truncate_to_month(day, month, year):
    """
    return the day given if its valid for the month + year,
    or the end of the month if the day exceeds the month's number of days
    """
    # enable passing of strings
    year = int(year)
    month = int(month)
    maxday = calendar.monthrange(year, month)[-1]
    if maxday < int(day):
        return maxday
    return day


def urljoin(*args):
    return '/'.join(arg.strip('/') for arg in args)
