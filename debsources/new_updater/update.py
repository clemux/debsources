#!/usr/bin/env python

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

from debsources.debmirror import SourceMirror
from .tasks import extract_new, garbage_collect, update_suites, join_taskset


def do_update(conf):
    """
    Starts the update

    :param: conf debsources configuration object
    """

    mirror = SourceMirror(conf['mirror_dir'])
    task_set = extract_new(conf, mirror)
    job = task_set.apply_async()

    chain = (update_suites.s(conf, mirror) | garbage_collect.si(conf, mirror))

    join_taskset.delay(job.id, job.subtasks, chain)
