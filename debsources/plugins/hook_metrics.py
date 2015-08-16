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

import logging
import os
import subprocess

from debsources import db_storage
from debsources.models import Metric

from debsources.new_updater.celery import app, DBTask


MY_NAME = 'metrics'
MY_EXT = '.stats'
metricsfile_path = lambda pkgdir: pkgdir + MY_EXT


def parse_metrics(path):
    metrics = {}
    with open(path) as metricsfile:
        for line in metricsfile:
            metric, value = line.split()
            metrics[metric] = int(value)
    return metrics


@app.task(base=DBTask, bind=True)
def add_package(self, conf, pkg, pkgdir, file_table):
    logging.debug('add-package %s' % pkg)
    self.conf = conf

    metric_type = 'size'
    metric_value = None
    metricsfile = metricsfile_path(pkgdir)
    metricsfile_tmp = metricsfile + '.new'

    if 'hooks.fs' in conf['backends']:
        if not os.path.exists(metricsfile):  # run du only if needed
            cmd = ['du', '--summarize', pkgdir]
            metric_value = int(subprocess.check_output(cmd).split()[0])
            with open(metricsfile_tmp, 'w') as out:
                out.write('%s\t%d\n' % (metric_type, metric_value))
            os.rename(metricsfile_tmp, metricsfile)

    if 'hooks.db' in conf['backends']:
        if metric_value is None:
            # hooks.db is enabled but hooks.fs is not, so we don't have a
            # metric_value handy. Parse it from metrics file, hoping it exists
            # from previous runs...
            metric_value = parse_metrics(metricsfile)[metric_type]

        db_package = db_storage.lookup_package(self.session, pkg['package'],
                                               pkg['version'])
        metric = self.session.query(Metric) \
                             .filter_by(package_id=db_package.id,
                                        metric=metric_type,
                                        value=metric_value) \
                             .first()
        if not metric:
            metric = Metric(db_package, metric_type, metric_value)
            self.session.add(metric)
            self.session.commit()


@app.task(base=DBTask, bind=True)
def rm_package(self, conf, pkg, pkgdir, file_table):
    logging.debug('rm-package %s' % pkg)

    if 'hooks.fs' in conf['backends']:
        metricsfile = metricsfile_path(pkgdir)
        if os.path.exists(metricsfile):
            os.unlink(metricsfile)

    if 'hooks.db' in conf['backends']:
        db_package = db_storage.lookup_package(self.session, pkg['package'],
                                               pkg['version'])
        self.session.query(Metric) \
                    .filter_by(package_id=db_package.id) \
                    .delete()


def init_plugin(debsources):
    debsources['subscribe']('add-package', add_package, title=MY_NAME)
    debsources['subscribe']('rm-package',  rm_package,  title=MY_NAME)
    debsources['declare_ext'](MY_EXT, MY_NAME)
