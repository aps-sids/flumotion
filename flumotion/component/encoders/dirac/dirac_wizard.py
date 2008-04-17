# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

import gettext
import os

from zope.interface import implements

from flumotion.wizard.basesteps import VideoEncoderStep
from flumotion.wizard.interfaces import IEncoderPlugin
from flumotion.wizard.models import VideoEncoder

__version__ = "$Rev: 6443 $"
_ = gettext.gettext


class DiracVideoEncoder(VideoEncoder):
    component_type = 'dirac-encoder'

    def __init__(self):
        super(DiracVideoEncoder, self).__init__()

        self.properties.bitrate = 400

class DiracStep(VideoEncoderStep):
    name = _('Dirac encoder')
    sidebar_name = _('Dirac')
    glade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'dirac-wizard.glade')
    component_type = 'dirac'
    icon = 'xiphfish.png'

    # WizardStep

    def setup(self):
        self.bitrate.data_type = int
        self.add_proxy(self.model.properties,
                       ['bitrate'])

    def worker_changed(self, worker):
        self.model.worker = worker

        self.wizard.debug('running Dirac checks')
        # FIXME: what happens to this deferred ? Does it get fired into the
        # unknown ? Should we wait on it ?
        self.wizard.require_elements(worker, 'schroenc')

class DiracWizardPlugin(object):
    implements(IEncoderPlugin)
    def __init__(self, wizard):
        self.wizard = wizard
        self.model = DiracVideoEncoder()

    def getConversionStep(self):
        return DiracStep(self.wizard, self.model)