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

from celery import Celery
from celery.signals import celeryd_init

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from debsources import mainlib
from debsources.new_updater import celeryconfig

session = scoped_session(sessionmaker())

app = Celery('new_updater',
             broker='amqp://',
             backend='amqp://',
             include=['debsources.new_updater.tasks'])


app.config_from_object(celeryconfig)


@celeryd_init.connect
def configure_workers(sender=None, conf=None, **kwargs):
    debsources_conf = mainlib.load_conf(mainlib.guess_conffile())
    debsources_conf['observers'], debsources_conf['file_exts'] = \
        mainlib.load_hooks(debsources_conf)
    if debsources_conf.get('update_sync'):
        conf.CELERY_ALWAYS_EAGER = True

    engine = create_engine(debsources_conf['db_uri'],
                           echo=False)
    session.configure(bind=engine)


if __name__ == '__main__':
    app.start()
