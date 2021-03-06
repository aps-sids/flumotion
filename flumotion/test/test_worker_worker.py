# -*- Mode: Python; test-case-name:flumotion.test.test_worker_worker -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008,2009 Fluendo, S.L.
# Copyright (C) 2010,2011 Flumotion Services, S.A.
# All rights reserved.
#
# This file may be distributed and/or modified under the terms of
# the GNU Lesser General Public License version 2.1 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.LGPL" in the source distribution for more information.
#
# Headers in this file shall remain intact.

from flumotion.common import testsuite
from flumotion.worker import worker


class FakeOptions:

    def __init__(self):
        self.host = 'localhost'
        self.port = 9999
        self.transport = 'TCP'
        self.feederports = [9998]
        self.randomFeederports = False
        self.name = 'fakeworker'


class TestBrain(testsuite.TestCase):

    def testInit(self):
        brain = worker.WorkerBrain(FakeOptions())
