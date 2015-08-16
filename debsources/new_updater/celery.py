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

from celery import Celery, Task
from celery.signals import celeryd_init

from debsources import mainlib
from debsources.new_updater import celeryconfig
from debsources.sqla_session import _get_engine_session


app = Celery('new_updater',
             broker='amqp://',
             backend='amqp://',
             include=['debsources.new_updater.tasks'])


app.config_from_object(celeryconfig)


# Base class for tasks accessing the database
class DBTask(Task):
    """Abstract class for tasks accessing the database.

    Sets up the SQLAlchemy session according the debsources
    configuration dictionary.

    """
    abstract = True
    _session = None

    def after_return(self, *args, **kwargs):
        if self._session is not None:
            self._session.remove()

    @property
    def session(self):
        if self._session is None:
            # TODO: test for the self.conf existence
            self.engine, self._session = _get_engine_session(
                self.conf['db_uri'],
                verbose=False)

        return self._session


@celeryd_init.connect
def configure_workers(sender=None, conf=None, **kwargs):
    debsources_conf = mainlib.load_conf(mainlib.guess_conffile())
    debsources_conf['observers'], debsources_conf['file_exts'] = \
        mainlib.load_hooks(debsources_conf)


if __name__ == '__main__':
    app.start()
