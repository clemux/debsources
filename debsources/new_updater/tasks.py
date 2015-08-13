# Copyright (C) 2015  The Debsources developers <info@sources.debian.net>.
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

import six

from datetime import datetime
from email.utils import formatdate
import logging
import os
import subprocess
import string

from celery import chord, group
from celery.utils import worker_direct
from sqlalchemy import sql, not_


from debsources import fs_storage, db_storage
from debsources.consts import DEBIAN_RELEASES
from debsources.models import (SuiteInfo, Suite, SuiteAlias, Package,
                               HistorySize, HistorySlocCount)

from .celery import app, session, DBTask

BULK_FLUSH_THRESHOLD = 50000


# hooks

@app.task
def run_shell_hooks(pkg, event):
    print('running shell hook for {0}'.format(pkg['package']))


@app.task
def call_hooks(conf, pkg, pkgdir, file_table, event, worker):
    # shell hooks
    run_shell_hooks.apply_async((pkg, event))

    for (title, action) in conf['observers'][event]:
        print('running {0} on {1}...'.format(title, pkg['package']))
        s = action.s(conf, pkg, pkgdir, file_table)
        s.apply_async()


# main tasks

# extract new packages

@app.task
def extract_new(conf, mirror):
    tasks = [add_package.s(conf, pkg.description(conf['sources_dir']))
             for pkg in mirror.ls()]
    chord(tasks, finish.s()).delay()
    return (conf, mirror)


@app.task(base=DBTask, bind=True)
def add_package(self, conf, pkg):
    worker = worker_direct(self.request.hostname).name

    pkgdir = pkg['extraction_dir']
    workdir = os.getcwd()
    try:
        fs_storage.extract_package(pkg, pkgdir)
    except subprocess.CalledProcessError as e:
        print('extract error: {0} -- {1}'.format(e.returncode,
                                                 ' '.join(e.cmd)))
    else:
        print('adding {0}'.format(pkg['package']))
        os.chdir(pkgdir)
        file_table = db_storage.add_package(session, pkg, pkgdir, False)
        session.commit()

        s = call_hooks.s(conf, pkg, pkgdir,
                         file_table, 'add-package', worker)
        s.delay()

    finally:
        os.chdir(workdir)


# update suites

def _add_suite(conf, session, suite, sticky=False, aliases=[]):
    """add suite to the table of static suite info

    """
    suite_version = None
    suite_reldate = None
    if suite in DEBIAN_RELEASES:
        suite_info = DEBIAN_RELEASES[suite]
        suite_version = suite_info['version']
        suite_reldate = suite_info['date']
        if sticky:
            assert suite_info['archived']
    db_aliases = [SuiteAlias(alias=alias, suite=suite) for alias in aliases]
    db_suite = SuiteInfo(suite, sticky=sticky,
                         version=suite_version,
                         release_date=suite_reldate,
                         aliases=db_aliases)
    if not conf['dry_run'] and 'db' in conf['backends']:
        session.add(db_suite)


@app.task(base=DBTask)
def update_suites(sources, conf, mirror):
    """update stage: sweep and recreate suite mappings

    """
    logging.info('update suites mappings...')
    print('updating suites...')

    # FIXME: this is ugly
    tmp = sources
    sources = dict()
    for pkg in tmp:
        sources[pkg[0]] = pkg[1:]


    insert_q = sql.insert(Suite.__table__)
    insert_params = []

    # load suites aliases
    suites_aliases = mirror.ls_suites_with_aliases()
    if not conf['dry_run'] and 'db' in conf['backends']:
        session.query(SuiteAlias).delete()

    for (suite, pkgs) in six.iteritems(mirror.suites):
        if not conf['dry_run'] and 'db' in conf['backends']:
            session.query(Suite).filter_by(suite=suite).delete()
        for pkg_id in pkgs:
            (pkg, version) = pkg_id
            db_package = db_storage.lookup_package(session, pkg, version)
            if not db_package:
                logging.warn('package %s/%s not found in suite %s, skipping'
                             % (pkg, version, suite))
            else:
                logging.debug('add suite mapping: %s/%s -> %s'
                              % (pkg, version, suite))
                params = {'package_id': db_package.id,
                          'suite': suite}
                insert_params.append(params)
                if pkg_id in sources:
                    # fill-in incomplete suite information in status
                    sources[pkg_id][-1].append(suite)
                else:
                    # defensive measure to make update_suites() more reusable
                    logging.warn('cannot find %s/%s during suite update'
                                 % (pkg, version))
        if not conf['dry_run'] and 'db' in conf['backends'] \
           and len(insert_params) >= BULK_FLUSH_THRESHOLD:
            session.execute(insert_q, insert_params)
            session.flush()
            insert_params = []

        if not conf['dry_run'] and 'db' in conf['backends']:
            session.query(SuiteInfo).filter_by(name=suite).delete()
            _add_suite(conf, session, suite, aliases=suites_aliases[suite])

    if not conf['dry_run'] and 'db' in conf['backends'] \
       and insert_params:
        session.execute(insert_q, insert_params)
        session.commit()

    # update sources.txt, now that we know the suite mappings
    src_list_path = os.path.join(conf['cache_dir'], 'sources.txt')
    with open(src_list_path + '.new', 'w') as src_list:
        for pkg_id, src_entry in six.iteritems(sources):
            fields = list(pkg_id)
            fields.extend(src_entry[:-1])  # all except suites
            fields.append(string.join(src_entry[-1], ','))
            src_list.write(string.join(fields, '\t') + '\n')
    os.rename(src_list_path + '.new', src_list_path)

    return (conf, mirror)


# update metadata

@app.task(base=DBTask)
def update_metadata(conf, mirror):
    if not conf['dry_run'] and 'fs' in conf['backends']:
        prefix_path = os.path.join(conf['cache_dir'], 'pkg-prefixes')
        with open(prefix_path + '.new', 'w') as out:
            for prefix in db_storage.pkg_prefixes(session):
                out.write('%s\n' % prefix)
        os.rename(prefix_path + '.new', prefix_path)

    # update timestamp
    if not conf['dry_run'] and 'fs' in conf['backends']:
        timestamp_file = os.path.join(conf['cache_dir'], 'last-update')
        with open(timestamp_file + '.new', 'w') as out:
            out.write('%s\n' % formatdate())
        os.rename(timestamp_file + '.new', timestamp_file)


# collect garbage

@app.task
def garbage_collect(conf, mirror):
    tasks = [rm_package.s(pkg.description(conf['sources_dir']))
             for pkg in mirror.ls()]
    group(tasks)()


@app.task(base=DBTask)
def rm_package(pkg):
    print('deleting: {0}-{1}'.format(pkg['package'], pkg['version']))


# end callback

@app.task
def finish(res):
    print('Update finished.')
