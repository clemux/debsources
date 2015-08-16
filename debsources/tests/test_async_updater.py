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

import shutil
import tempfile
import unittest


# from nose.tools import istest
from nose.plugins.attrib import attr

from debsources import mainlib
from debsources.debmirror import SourceMirror
from debsources.new_updater.celery import app
from debsources.new_updater.tasks import extract_new

from debsources.tests.db_testing import DbTestFixture, DB_COMPARE_QUERIES
from debsources.tests.updater_testing import mk_conf
from debsources.tests.test_updater import db_mv_tables_to_schema


def assert_db_table_equal(test_subj, expected_schema, actual_schema, table):
    q = DB_COMPARE_QUERIES[table]

    expected = [dict(list(r.items())) for r in
                test_subj.session.execute(q % {'schema': expected_schema})]
    actual = [dict(list(r.items())) for r in
              test_subj.session.execute(q % {'schema': actual_schema})]

    test_subj.assertSequenceEqual(expected, actual,
                                  msg='table %s differs from reference'
                                  % table)


@attr('async')
class Updater(unittest.TestCase, DbTestFixture):
    @classmethod
    def setUpClass(cls):
        cls.db_setup_cls()
        cls.tmpdir = tempfile.mkdtemp(suffix='.debsources-test')
        cls.conf = mk_conf(cls.tmpdir)
        cls.conf['hooks'] = []
        obs, exts = mainlib.load_hooks(cls.conf)
        cls.conf['observers'], cls.conf['file_exts'] = obs, exts
        cls.mirror = SourceMirror(cls.conf['mirror_dir'])

        db_mv_tables_to_schema(cls.session, 'ref')
        cls.session.commit()

        app.conf['CELERY_ALWAYS_EAGER'] = True

    @classmethod
    def tearDownClass(cls):
        cls.db_teardown_cls()
        shutil.rmtree(cls.tmpdir)

    def testExtractNew(self):
        extract_new(self.conf, self.mirror)
        add_package = app.tasks[
            'debsources.new_updater.tasks.add_package'
        ]
        add_package.session.close()
        add_package.engine.dispose()

        assert_db_table_equal(self, 'ref', 'public', 'packages')
        assert_db_table_equal(self, 'ref', 'public', 'package_names')
        assert_db_table_equal(self, 'ref', 'public', 'files')

