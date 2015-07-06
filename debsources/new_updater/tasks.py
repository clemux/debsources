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

from .celery import app

from debsources import fs_storage, db_storage
from debsources.sqla_session import _get_engine_session

from celery import chord, group, Task
from celery.utils import worker_direct

import os
import six
import subprocess


engine, session = _get_engine_session('postgresql:///debsources',
                                      verbose=False)


# Base class for tasks accessing the database
class DBTask(Task):
    """Abstract class for tasks accessing the database. Ensures that the
session is returned to the session pool at the end of the execution

    From
    http://prschmid.blogspot.fr/2013/04/using-sqlalchemy-with-celery-tasks.html

    """
    abstract = True

    def after_return(self, *args, **kwargs):
        session.remove()


# hooks

@app.task
def run_shell_hooks(pkg, event):
    print('running hook for {0}'.format(pkg['package']))


@app.task
def call_hooks(observers, pkg, pkgdir, file_table, event, worker):
    # shell hooks
    run_shell_hooks.apply_async((pkg, event), queue=worker)

    for (title, action) in observers[event]:
        s = action.s(pkg, pkgdir, file_table)
        s.delay()


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

        s = call_hooks.s(conf['observers'], pkg, pkgdir,
                         file_table, 'add-package', worker)
        s.delay()

    finally:
        os.chdir(workdir)


# update suites

@app.task(base=DBTask)
def add_suite_package(suite, pkg_id):
    pass


@app.task
def update_suites(mirror):
    for (suite, pkgs) in six.iteritems(mirror.suites):
        for pkg_id in pkgs:
            s = add_suite_package.delay(suite, pkg_id)
            s.delay()
    pass


# update metadata

@app.task
def update_metadata(mirror):
    pass


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
