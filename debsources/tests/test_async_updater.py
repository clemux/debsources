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
from __future__ import print_function

import os
import shutil
import tempfile
import unittest


# from nose.tools import istest
from nose.plugins.attrib import attr
from nose.tools import istest

from debsources import db_storage
from debsources import mainlib
from debsources.debmirror import SourceMirror
from debsources.new_updater.celery import app
from debsources.new_updater.tasks import (extract_new, update_suites,
                                          garbage_collect)
from debsources.new_updater.update import get_hook

from debsources.tests.db_testing import DbTestFixture, DB_COMPARE_QUERIES
from debsources.tests.testdata import *  # NOQA
from debsources.tests.test_updater import (db_mv_tables_to_schema,
                                           assert_dir_equal,)
from debsources.tests.updater_testing import mk_conf


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

        # list of tasks instances whose session and engine we need to
        # cleanup at the end in tearDownClass
        cls.tasks_cleanup = []

    @classmethod
    def tearDownClass(cls):
        for task in cls.tasks_cleanup:
            # if the tasks have not been run at least once, session and
            # engine will be empty
            if task.session is not None:
                task.session.close()
                task.engine.dispose()
        cls.db_teardown_cls()
        shutil.rmtree(cls.tmpdir)

    @istest
    def extractNewUpdateSuites(self):
        # update_suites needs data returned by extract_new, so we're
        # testing it here

        # a possible solution to avoid that would be to store
        # extract_new's return value as a class attribute

        task_set = extract_new(self.conf, self.mirror)
        res = task_set.apply()

        update_suites.delay(res.get(), self.conf, self.mirror)

        self.tasks_cleanup.extend([
            app.tasks['debsources.new_updater.tasks.add_package'],
            update_suites,
        ])

        assert_db_table_equal(self, 'ref', 'public', 'packages')
        assert_db_table_equal(self, 'ref', 'public', 'package_names')
        assert_db_table_equal(self, 'ref', 'public', 'files')
        assert_db_table_equal(self, 'ref', 'public', 'suites')
        assert_db_table_equal(self, 'ref', 'public', 'suites_aliases')
        assert_db_table_equal(self, 'ref', 'public', 'suites_info')

        # sources/ dir comparison. Ignored patterns:
        # - plugin result caches -> because most of them are in os.walk()
        #   order, which is not stable
        # - dpkg-source log stored in *.log
        # - all hook result files
        # - stats files
        exclude_pat = ['*' + ext for ext in self.conf['file_exts']] \
            + ['*.log'] + ['*.stats'] + ['*.checksums'] + ['*.ctags'] \
            + ['*.sloccount']

        assert_dir_equal(self,
                         os.path.join(self.tmpdir, 'sources'),
                         os.path.join(TEST_DATA_DIR, 'sources'),
                         exclude=exclude_pat)

    @istest
    def addChecksums(self):
        self.conf['hooks'] = ['checksums']
        obs, exts = mainlib.load_hooks(self.conf)
        task = get_hook(self.conf, 'checksums', 'add-package')
        for pkg in self.mirror.ls():
            pkg_desc = pkg.description(self.conf['sources_dir'])
            task.delay((self.conf,
                        pkg_desc,
                        pkg_desc['extraction_dir'],
                        None,
                        None))

        self.tasks_cleanup.append(task)

        assert_db_table_equal(self, 'ref', 'public', 'checksums')

    @istest
    def addCtags(self):
        self.conf['hooks'] = ['ctags']
        obs, exts = mainlib.load_hooks(self.conf)
        task = get_hook(self.conf, 'ctags', 'add-package')
        for pkg in self.mirror.ls():
            pkg_desc = pkg.description(self.conf['sources_dir'])
            task.delay((self.conf,
                        pkg_desc,
                        pkg_desc['extraction_dir'],
                        None,
                        None))

        self.tasks_cleanup.append(task)

        assert_db_table_equal(self, 'ref', 'public', 'ctags')

    def testGarbageCollect(self):
        GC_PACKAGE = ('ocaml-curses', '1.0.3-1')
        PKG_SUITE = 'squeeze'
        PKG_AREA = 'main'

        # make fresh copies of sources/ and mirror dir
        orig_sources = os.path.join(TEST_DATA_DIR, 'sources')
        orig_mirror = os.path.join(TEST_DATA_DIR, 'mirror')
        new_sources = os.path.join(self.tmpdir, 'sources2')
        new_mirror = os.path.join(self.tmpdir, 'mirror2')
        shutil.copytree(orig_sources, new_sources)
        shutil.copytree(orig_mirror, new_mirror)
        self.conf['mirror_dir'] = new_mirror
        self.conf['sources_dir'] = new_sources

        pkgdir = os.path.join(new_sources, PKG_AREA, GC_PACKAGE[0][0],
                              GC_PACKAGE[0], GC_PACKAGE[1])
        src_index = os.path.join(new_mirror, 'dists', PKG_SUITE, PKG_AREA,
                                 'source', 'Sources.gz')

        # rm package to be GC'd from mirror (actually, remove everything...)
        with open(src_index, 'w') as f:
            f.truncate()

        # update run that should not GC, due to timestamp
        os.utime(pkgdir, None)
        self.conf['expire_days'] = 0
        garbage_collect(self.conf, self.mirror)
        self.assertTrue(os.path.exists(pkgdir),
                        'young gone package %s/%s disappeared from FS storage'
                        % GC_PACKAGE)
        self.assertTrue(db_storage.lookup_package(self.session, *GC_PACKAGE),
                        'young gone package %s/%s disappeared from DB storage'
                        % GC_PACKAGE)

        self.conf['expire_days'] = 0
        # load the mirror again, since we removed files
        self.mirror = SourceMirror(self.conf['mirror_dir'])
        garbage_collect(self.conf, self.mirror)

        self.tasks_cleanup.extend([
            garbage_collect,
            app.tasks['debsources.new_updater.tasks.rm_package']
        ])

        self.assertFalse(os.path.exists(pkgdir),
                         'gone package %s/%s persisted in FS storage' %
                         GC_PACKAGE)
        self.assertFalse(db_storage.lookup_package(self.session, *GC_PACKAGE),
                         'gone package %s/%s persisted in DB storage' %
                         GC_PACKAGE)

