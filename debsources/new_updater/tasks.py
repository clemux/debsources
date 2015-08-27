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

from celery import chord, group, chain
from celery.result import TaskSetResult
from celery.task import subtask
from celery.task.sets import TaskSet
from celery.utils import worker_direct
from sqlalchemy import sql, not_

from debsources import fs_storage, db_storage
from debsources.consts import DEBIAN_RELEASES
from debsources.models import (SuiteInfo, Suite, SuiteAlias, Package)
from debsources.debmirror import SourcePackage


from .celery import app, DBTask

BULK_FLUSH_THRESHOLD = 50000


# utilitary classes

@app.task
def join_taskset(setid, subtasks, callback, interval=3, max_retries=None):
    result = TaskSetResult(setid, subtasks)
    if result.ready():
        result_joined = result.join()
        print(result_joined)
        return subtask(callback).delay(result_joined)
    join_taskset.retry(countdown=interval, max_retries=max_retries)


# hooks

@app.task
def run_shell_hooks(pkg, event):
    # print('running shell hook for {0}'.format(pkg['package']))
    pass


@app.task
def call_hooks(conf, pkg, pkgdir, file_table, event,
               callback=None, worker=None):
    # shell hooks
    run_shell_hooks.apply_async((pkg, event))

    tasks = [action.s(conf, pkg, pkgdir, file_table)
             for (_, action) in conf['observers'][event]]
    if callback is not None:
        chord(tasks, callback).delay()
    else:
        group(tasks).delay()

# main tasks

# extract new packages


@app.task
def extract_new(conf, mirror, callback=None):
    chains = []
    for pkg in mirror.ls():
        tasks = [add_package.s(conf, pkg.description(conf['sources_dir']))]
        tasks.extend([action.s()
                      for (_, action) in conf['observers']['add-package']])
        chains.append(chain(*tasks))

    return TaskSet(tasks=chains)

#    if callback is not None:
#        chord(tasks, callback).delay()
#    else:
#       group(tasks).delay()


@app.task(base=DBTask, bind=True)
def add_package(self, conf, pkg):
    # copying the conf into a instance member for
    # sqlalchemy session setup
    self.conf = conf

    pkgdir = pkg['extraction_dir']
    workdir = os.getcwd()
    try:
        fs_storage.extract_package(pkg, pkgdir)
    except subprocess.CalledProcessError as e:
        logging.error('extract error: {0} -- {1}'.format(e.returncode,
                                                         ' '.join(e.cmd)))
    else:
        os.chdir(pkgdir)

        file_table = db_storage.add_package(self.session, pkg, pkgdir, False)
        self.session.commit()

        # call_hooks.delay(conf, pkg, pkgdir,
        # file_table, 'add-package')

    finally:
        os.chdir(workdir)
        pkg_id = pkg['package'], pkg['version']
        dsc_rel = os.path.relpath(pkg['dsc_path'], conf['mirror_dir'])
        pkgdir_rel = os.path.relpath(pkg['extraction_dir'],
                                     conf['sources_dir'])
        return (conf, pkg, pkgdir, file_table,
                (pkg_id, pkg['archive_area'], dsc_rel, pkgdir_rel, []))


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


@app.task(base=DBTask, bind=True)
def update_suites(self, sources, conf, mirror):
    """update stage: sweep and recreate suite mappings

    """
    self.conf = conf
    logging.debug('update suites mappings...')
    print('updating suites...')

    # FIXME: this is ugly

    # because add_package and hooks are chained together, the
    # 'sources' argument must be passed to the hooks tasks, along the
    # arguments needed by those tasks. As a result, update_suites
    # receives arguments that are useful only for hook tasks, and we
    # must filter them out.

    tmp = map(lambda x: x[-1], sources)
    sources = dict()
    for pkg in tmp:
        sources[pkg[0]] = pkg[1:]

    insert_q = sql.insert(Suite.__table__)
    insert_params = []

    # load suites aliases
    suites_aliases = mirror.ls_suites_with_aliases()
    if not conf['dry_run'] and 'db' in conf['backends']:
        self.session.query(SuiteAlias).delete()

    for (suite, pkgs) in six.iteritems(mirror.suites):
        if not conf['dry_run'] and 'db' in conf['backends']:
            self.session.query(Suite).filter_by(suite=suite).delete()
        for pkg_id in pkgs:
            (pkg, version) = pkg_id
            db_package = db_storage.lookup_package(self.session, pkg, version)
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
            self.session.execute(insert_q, insert_params)
            self.session.flush()
            insert_params = []

        if not conf['dry_run'] and 'db' in conf['backends']:
            self.session.query(SuiteInfo).filter_by(name=suite).delete()
            _add_suite(conf, self.session, suite,
                       aliases=suites_aliases[suite])

    if not conf['dry_run'] and 'db' in conf['backends'] \
       and insert_params:
        self.session.execute(insert_q, insert_params)
        self.session.commit()

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

@app.task(base=DBTask, bind=True)
def update_metadata(self, conf, mirror):
    self.conf = conf
    if not conf['dry_run'] and 'fs' in conf['backends']:
        prefix_path = os.path.join(conf['cache_dir'], 'pkg-prefixes')
        with open(prefix_path + '.new', 'w') as out:
            for prefix in db_storage.pkg_prefixes(self.session):
                out.write('%s\n' % prefix)
        os.rename(prefix_path + '.new', prefix_path)

    # update timestamp
    if not conf['dry_run'] and 'fs' in conf['backends']:
        timestamp_file = os.path.join(conf['cache_dir'], 'last-update')
        with open(timestamp_file + '.new', 'w') as out:
            out.write('%s\n' % formatdate())
        os.rename(timestamp_file + '.new', timestamp_file)


# collect garbage

@app.task(base=DBTask, bind=True)
def rm_package(self, conf, pkg):
    self.conf = conf
    logging.info("remove %s/%s..." % (pkg['package'], pkg['version']))
    worker = worker_direct(self.request.hostname).name
    pkgdir = pkg['extraction_dir']
    try:
        if not conf['dry_run'] and 'hooks' in conf['backends']:
            s = call_hooks.s(conf, pkg, pkgdir,
                             None, 'rm-package', worker)
            s.delay()

        if not conf['dry_run'] and 'fs' in conf['backends']:
            fs_storage.remove_package(pkg, pkgdir)
        if not conf['dry_run'] and 'db' in conf['backends']:
            db_storage.rm_package(self.session, pkg)
    except:
        logging.exception('failed to remove %s' % pkg['package'])


@app.task(base=DBTask, bind=True)
def garbage_collect(self, conf, mirror):
    self.conf = conf
    logging.info('garbage collection...')

    # tasks that will be run in a group
    tasks = list()

    for version in self.session.query(Package).filter(not_(Package.sticky)):
        pkg = SourcePackage.from_db_model(version)

        pkg_id = (pkg['package'], pkg['version'])
        pkgdir = pkg.extraction_dir(conf['sources_dir'])
        if pkg_id not in mirror.packages:
            # package is in in Debsources db, but gone from mirror: we
            # might have to garbage collect it (depending on expiry)
            expire_days = conf['expire_days']
            age = None
            if os.path.exists(pkgdir):
                age = datetime.now() - \
                    datetime.fromtimestamp(os.path.getmtime(pkgdir))

            if not age or age.days >= expire_days:
                tasks.append(rm_package.s(
                    conf,
                    pkg.description(conf['sources_dir'])))
            else:
                logging.debug('not removing %s as it is too young' % pkg)

        if conf['force_triggers']:
            try:
                call_hooks.delay(conf,
                                 pkg.description(conf['sources_dir']),
                                 None,
                                 pkgdir,
                                 'rm-package')
            except:
                logging.exception('trigger failure on %s' % pkg)

    group(tasks).delay()
