# Copyright (C) 2013-2014  The Debsources developers <info@sources.debian.net>.
# See the AUTHORS file at the top-level directory of this distribution and at
# https://anonscm.debian.org/gitweb/?p=qa/debsources.git;a=blob;f=AUTHORS;hb=HEAD
#
# This file is part of Debsources. Debsources is free software: you can
# redistribute it and/or modify it under the terms of the GNU Affero General
# Public License as published by the Free Software Foundation, either version 3
# of the License, or (at your option) any later version.  For more information
# see the COPYING file at the top-level directory of this distribution and at
# https://anonscm.debian.org/gitweb/?p=qa/debsources.git;a=blob;f=COPYING;hb=HEAD

from __future__ import absolute_import

from debsources.new_updater.celery import app

import logging

conf = None


@app.task
def add_package(pkg, pkgdir, file_table):
    global conf
    logging.debug('add-package %s %s' % (pkg, pkgdir))
    print('Hello %s %s %s.' % (pkg, pkgdir, file_table))


@app.task
def rm_package(pkg, pkgdir, file_table):
    global conf
    logging.debug('rm-package %s %s' % (pkg, pkgdir))


def init_plugin(debsources):
    global conf
    conf = debsources['config']
    debsources['subscribe']('add-package', add_package, title='hello')
    debsources['subscribe']('rm-package', rm_package, title='hello')
