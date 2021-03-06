#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Jeff Hammel <jhammel@openplans.org>
# Copyright (C) 2017 Emerson Castaneda <emecas@gmail.com>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#

from setuptools import find_packages, setup

version = '0.7.3'

extra = {}

try:
    from trac.dist import get_l10n_cmdclass
    from trac.dist import extract_python
except ImportError:
    pass
else:
    cmdclass = get_l10n_cmdclass()
    if cmdclass:
        extra['cmdclass'] = cmdclass
        extractors = [
            ('**/templates/**.html', 'genshi', None),
            ('**.py', 'trac.dist:extract_python', None),
        ]
        extra['message_extractors'] = {
            'trachours': extractors,
        }


setup(name='TracHours',
      version=version,
      description="Trac the estimated and actual hours spent on tickets",
      author="David Turner and Jeff Hammel",
      author_email="jhammel@openplans.org",
      maintainer="Emerson Castaneda",
      maintainer_email="emecas@gmail.com",
      url='https://trac-hacks.org/wiki/TracHoursPlugin',
      keywords='trac plugin',
      license='3-Clause BSD',
      packages=find_packages(exclude=['*.tests']),
      include_package_data=True,
      package_data={
          'trachours': [
              'locale/*/LC_MESSAGES/*.mo',
              'templates/*',
          ]
      },
      zip_safe=False,
      install_requires=[
          'Trac',
          'FeedParser',
      ],
      extras_require=dict(lxml=['lxml']),
      entry_points={
          'trac.plugins': [
              'trachours.trachours = trachours.hours',
              'trachours.multiproject = trachours.multiproject',
              'trachours.setup = trachours.db',
              'trachours.ticket = trachours.ticket',
              'trachours.web_ui = trachours.web_ui',
          ],
      },
      test_suite='trachours.tests.test_suite',
      tests_require=[],
      **extra
      )
