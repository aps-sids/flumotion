# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.L. (www.fluendo.com).
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

"""
Feed components, participating in the stream
"""

import gst
import gst.interfaces
import gobject

from twisted.internet import reactor, defer
from twisted.spread import pb
from zope.interface import implements

from flumotion.configure import configure
from flumotion.component import component as basecomponent
from flumotion.component import feed
from flumotion.common import common, interfaces, errors, log, pygobject, messages
from flumotion.common import gstreamer

from flumotion.common.planet import moods
from flumotion.common.pygobject import gsignal

from flumotion.common.messages import N_
T_ = messages.gettexter('flumotion')

class FeedComponentMedium(basecomponent.BaseComponentMedium):
    """
    I am a component-side medium for a FeedComponent to interface with
    the manager-side ComponentAvatar.
    """
    implements(interfaces.IComponentMedium)
    logCategory = 'feedcompmed'
    remoteLogName = 'feedserver'

    def __init__(self, component):
        """
        @param component: L{flumotion.component.feedcomponent.FeedComponent}
        """
        basecomponent.BaseComponentMedium.__init__(self, component)

        self._feederFeedServer = {} # eaterAlias -> (fullFeedId, host, port) tuple
                                    # for remote feeders
        self._feederPendingConnections = {} # eaterAlias -> cancel thunk
        self._eaterFeedServer = {}  # fullFeedId -> (host, port) tuple
                                    # for remote eaters (FIXME bitrotten)
        self._eaterClientFactory = {} # (componentId, feedId) -> client factory
        self._eaterTransport = {}     # (componentId, feedId) -> transport
        self.logName = component.name

        def on_component_error(component, element_path, message):
            self.callRemote('error', element_path, message)

        self.comp.connect('error', on_component_error)
        
        # override base Errback for callRemote to stop the pipeline
        #def callRemoteErrback(reason):
        #    self.warning('stopping pipeline because of %s' % reason)
        #    self.comp.pipeline_stop()

    ### Referenceable remote methods which can be called from manager
    def remote_getElementProperty(self, elementName, property):
        return self.comp.get_element_property(elementName, property)
        
    def remote_setElementProperty(self, elementName, property, value):
        self.comp.set_element_property(elementName, property, value)

    def remote_attachPadMonitorToFeeder(self, feederName):
        self.comp.attachPadMonitorToFeeder(feederName)

    def remote_setGstDebug(self, debug):
        """
        Sets the GStreamer debugging levels based on the passed debug string.

        @since: 0.4.2
        """
        self.debug('Setting GStreamer debug level to %s' % debug)
        if not debug:
            return

        for part in debug.split(','):
            glob = None
            value = None
            pair = part.split(':')
            if len(pair) == 1:
                # assume only the value
                value = int(pair[0])
            elif len(pair) == 2:
                glob, value = pair
                value = int(value)
            else:
                self.warning("Cannot parse GStreamer debug setting '%s'." %
                    part) 
                continue

            if glob:
                try:
                    # value has to be an integer
                    gst.debug_set_threshold_for_name(glob, value)
                except TypeError:
                    self.warning("Cannot set glob %s to value %s" % (
                        glob, value))
            else:
                gst.debug_set_default_threshold(value)

    def remote_eatFrom(self, eaterAlias, fullFeedId, host, port):
        """
        Tell the component the host and port for the FeedServer through which
        it can connect a local eater to a remote feeder to eat the given
        fullFeedId.

        Called on by the manager-side ComponentAvatar.
        """
        self._feederFeedServer[eaterAlias] = (fullFeedId, host, port)
        return self.connectEater(eaterAlias)

    def _getAuthenticatorForEater(self, eaterAlias):
        # The avatarId on the keycards issued by the authenticator will
        # identify us to the remote component. Attempt to use our
        # fullFeedId, for debugging porpoises.
        if hasattr(self.authenticator, 'copy'):
            tup = common.parseComponentId(self.authenticator.avatarId)
            flowName, componentName = tup
            fullFeedId = common.fullFeedId(flowName, componentName,
                                           eaterAlias)
            return self.authenticator.copy(fullFeedId)
        else:
            return self.authenticator

    def connectEater(self, eaterAlias):
        """
        Connect one of the medium's component's eaters to a remote feed.
        Called by the component, both on initial connection and for
        reconnecting.

        @returns: (deferred, cancel) pair, where cancel is a thunk that
        you can call to cancel any pending connection attempt.
        """
        def gotFeed((feedId, fd)):
            self._feederPendingConnections.pop(eaterAlias, None)
            self.comp.eatFromFD(eaterAlias, feedId, fd)

        (fullFeedId, host, port) = self._feederFeedServer[eaterAlias]

        cancel = self._feederPendingConnections.pop(eaterAlias, None)
        if cancel:
            self.debug('cancelling previous connection attempt on %s',
                       eaterAlias)
            cancel()

        client = feed.FeedMedium(logName=self.comp.name)

        d = client.requestFeed(host, port,
                               self._getAuthenticatorForEater(eaterAlias),
                               fullFeedId)
        self._feederPendingConnections[eaterAlias] = client.stopConnecting
        d.addCallback(gotFeed)
        return d

    def remote_feedTo(self, componentId, feedId, host, port):
        """
        Tell the component to feed the given feed to the receiving component
        accessible through the FeedServer on the given host and port.

        Called on by the manager-side ComponentAvatar.
        """
        # FIXME: bitrotten, should use the FeedMedium
        # FIXME: check if this overwrites current config, and adapt if it
        # does
        self._eaterFeedServer[(componentId, feedId)] = (host, port)
        client = feed.FeedMedium(logName=self.comp.name)
        factory = feed.FeedClientFactory(client)
        # FIXME: maybe copy keycard instead, so we can change requester ?
        self.debug('connecting to FeedServer on %s:%d' % (host, port))
        reactor.connectTCP(host, port, factory)
        d = factory.login(self.authenticator)
        self._eaterClientFactory[(componentId, feedId)] = factory
        def loginCb(remoteRef):
            self.debug('logged in to feedserver, remoteRef %r' % remoteRef)
            client.setRemoteReference(remoteRef)
            # now call on the remoteRef to eat
            self.debug(
                'COMPONENT --> feedserver: receiveFeed(%s, %s)' % (
                    componentId, feedId))
            d = remoteRef.callRemote('receiveFeed', componentId, feedId)

            def receiveFeedCb(result):
                self.debug(
                    'COMPONENT <-- feedserver: receiveFeed(%s, %s): %r' % (
                    componentId, feedId, result))
                componentName, feedName = common.parseFeedId(feedId)
                t = remoteRef.broker.transport
                t.stopReading()
                t.stopWriting()

                key = (componentId, feedId)
                self._eaterTransport[key] = t
                remoteRef.broker.transport = None
                fd = t.fileno()
                self.debug('Telling component to feed feedName %s to fd %d'% (
                    feedName, fd))
                self.comp.feedToFD(feedName, fd)
                
            d.addCallback(receiveFeedCb)
            return d

        d.addCallback(loginCb)
        return d

    def remote_provideMasterClock(self, port):
        """
        Tells the component to start providing a master clock on the given
        UDP port.
        Can only be called if setup() has been called on the component.

        The IP address returned is the local IP the clock is listening on.

        @returns: (ip, port, base_time)
        @rtype:   tuple of (str, int, long)
        """
        self.debug('remote_provideMasterClock(port=%r)' % port)
        return self.comp.provide_master_clock(port)

    def remote_getMasterClockInfo(self):
        """
        Return the clock master info created by a previous call to provideMasterClock.

        @returns: (ip, port, base_time)
        @rtype:   tuple of (str, int, long)
        """
        return self.comp.get_master_clock()

    # FIXME: completely unnecessary, remove me
    def remote_getEaterDetail(self, fullFeedId):
        """
        Returns the host and port that the eater, that is eating from the feed id
        specified, is using to connect to its upstream feeder.
        
        @param fullFeedId: full feed id
        @type fullFeedId: str
        
        @returns (host, port) or None if unknown
        @rtype: tuple of (str, int)
        """
        self.debug("fullFeedId is %s and our current "
                   "feederFeedServer is %r" % (
                   fullFeedId, self._feederFeedServer))
        flowName, componentName, feedName = common.parseFullFeedId(fullFeedId)
        feedId = common.feedId(componentName, feedName)
        fullFeedId, host, port = self._feederFeedServer.get(feedId, (None, None, None))
        if fullFeedId:
            return host, port
        else:
            return None
        
    def remote_effect(self, effectName, methodName, *args, **kwargs):
        """
        Invoke the given methodName on the given effectName in this component.
        The effect should implement effect_(methodName) to receive the call.
        """
        self.debug("calling %s on effect %s" % (methodName, effectName))
        if not effectName in self.comp.effects:
            raise errors.UnknownEffectError(effectName)
        effect = self.comp.effects[effectName]
        if not hasattr(effect, "effect_%s" % methodName):
            raise errors.NoMethodError("%s on effect %s" % (methodName,
                effectName))
        method = getattr(effect, "effect_%s" % methodName)
        try:
            result = method(*args, **kwargs)
        except TypeError:
            msg = "effect method %s did not accept %s and %s" % (
                methodName, args, kwargs)
            self.debug(msg)
            raise errors.RemoteRunError(msg)
        self.debug("effect: result: %r" % result)
        return result

from feedcomponent010 import FeedComponent

FeedComponent.componentMediumClass = FeedComponentMedium

class ParseLaunchComponent(FeedComponent):
    'A component using gst-launch syntax'

    DELIMITER = '@'

    ### FeedComponent interface implementations
    def create_pipeline(self):
        try:
            unparsed = self.get_pipeline_string(self.config['properties'])
        except errors.MissingElementError, e:
            m = messages.Error(T_(N_(
                "The worker does not have the '%s' element installed.\n"
                "Please install the necessary plug-in and restart "
                "the component.\n"), e.args[0]))
            self.state.append('messages', m)
            raise errors.ComponentSetupHandledError(e)
        
        self.pipeline_string = self.parse_pipeline(unparsed)

        try:
            pipeline = gst.parse_launch(self.pipeline_string)
        except gobject.GError, e:
            self.warning('Could not parse pipeline: %s' % e.message)
            m = messages.Error(T_(N_(
                "GStreamer error: could not parse component pipeline.")),
                debug=e.message)
            self.state.append('messages', m)
            raise errors.PipelineParseError(e.message)

        self.connect_feeders(pipeline)

        return pipeline

    def set_pipeline(self, pipeline):
        FeedComponent.set_pipeline(self, pipeline)
        self.configure_pipeline(self.pipeline, self.config['properties'])

    ### ParseLaunchComponent interface for subclasses
    def get_pipeline_string(self, properties):
        """
        Method that must be implemented by subclasses to produce the
        gstparse string for the component's pipeline. Subclasses should
        not chain up; this method raises a NotImplemented error.

        Returns: a new pipeline string representation.
        """
        raise NotImplementedError('subclasses should implement '
                                  'get_pipeline_string')
        
    def configure_pipeline(self, pipeline, properties):
        """
        Method that can be implemented by subclasses if they wish to
        interact with the pipeline after it has been created and set
        on the component.

        This could include attaching signals and bus handlers.
        """
        pass

    ### private methods
    def add_default_eater_feeder(self, pipeline):
        if len(self.eaters) == 1:
            eater = 'eater:' + self.eaters.keys()[0]
            if eater not in pipeline:
                pipeline = '@' + eater + '@ ! ' + pipeline
        if len(self.feeders) == 1:
            feeder = 'feeder:' + self.feeders.keys()[0]
            if feeder not in pipeline:
                pipeline = pipeline + ' ! @' + feeder + '@'
        return pipeline

    def parse_tmpl(self, pipeline, templatizers):
        """
        Expand the given pipeline string representation by substituting
        blocks between '@' with a filled-in template.

        @param pipeline: a pipeline string representation with variables
        @param templatizers: A dict of prefix => procedure. Template
                             blocks in the pipeline will be replaced
                             with the result of calling the procedure
                             with what is left of the template after
                             taking off the prefix.
        Returns: a new pipeline string representation.
        """
        assert pipeline != ''

        # verify the template has an even number of delimiters
        if pipeline.count(self.DELIMITER) % 2 != 0:
            raise TypeError("'%s' contains an odd number of '%s'"
                            % (pipeline, self.DELIMITER))
        
        out = []
        for i, block in enumerate(pipeline.split(self.DELIMITER)):
            # when splitting, the even-indexed members will remain, and
            # the odd-indexed members are the blocks to be substituted
            if i % 2 == 0:
                out.append(block)
            else:
                block = block.strip()
                try:
                    pos = block.index(':')
                except ValueError:
                    raise TypeError("Template %r has no colon" % (block,))
                prefix = block[:pos+1]
                if prefix not in templatizers:
                    raise TypeError("Template %r has invalid prefix %r"
                                    % (block, prefix))
                out.append(templatizers[prefix](block[pos+1:]))
        return ''.join(out)
        
    def parse_pipeline(self, pipeline):
        pipeline = " ".join(pipeline.split())
        self.debug('Creating pipeline, template is %s', pipeline)
        
        # we expand the pipeline based on the templates and eater/feeder names
        # elements are named eater:(source_component_name):(feed_name)
        # or feeder:(component_name):(feed_name)
        eater_element_names = [e.elementName for e in self.eaters.values()]
        feeder_element_names = [f.elementName for f in self.feeders.values()]
        self.debug('we eat with eater elements %s', eater_element_names)
        self.debug('we feed with feeder elements %s', feeder_element_names)

        if pipeline == '' and not eater_element_names:
            raise TypeError, "Need a pipeline or a eater"

        if pipeline == '':
            assert eater_element_names
            pipeline = 'fakesink signal-handoffs=1 silent=1 name=sink'
            
        pipeline = self.add_default_eater_feeder(pipeline)
        pipeline = self.parse_tmpl(pipeline,
                                   {'eater:': self.get_eater_template,
                                    'feeder:': self.get_feeder_template})
        
        self.debug('pipeline is %s', pipeline)
        assert self.DELIMITER not in pipeline
        
        return pipeline

    def get_eater_template(self, eaterAlias):
        queue = self.get_queue_string(eaterAlias)
        check = ""
        if self.checkTimestamp:
            check += " check-imperfect-timestamp=1"
        if self.checkOffset:
            check += " check-imperfect-offset=1"
        if check != "":
            check = " ! identity name=eater:%s-identity silent=TRUE %s" % (
                eaterAlias, check)
        depay = self.DEPAY_TMPL + check
        if not queue:
            ret = self.FDSRC_TMPL + ' ! ' + depay
        else:
            ret = self.FDSRC_TMPL + ' ! ' + queue  + ' ! ' + depay

        return ret % {'name': 'eater:' + eaterAlias}

    def get_feeder_template(self, feederName):
        return self.FEEDER_TMPL % {'name': 'feeder:' + feederName}

    def get_queue_string(self, eaterAlias):
        """
        Return a parse-launch description of a queue, if this component
        wants an input queue on this eater, or None if not
        """
        return None

    ### BaseComponent interface implementation
    def do_start(self, clocking):
        """
        Tell the component to start.
        Whatever is using the component is responsible for making sure all
        eaters have received their file descriptor to eat from.

        @param clocking: tuple of (ip, port, base_time) of a master clock,
                         or None not to slave the clock
        @type  clocking: tuple(str, int, long) or None.
        """
        self.debug('ParseLaunchComponent.start')
        if clocking:
            self.info('slaving to master clock on %s:%d with base time %d' %
                clocking)

        if clocking:
            self.set_master_clock(*clocking)

        return self.link()

class Effect(log.Loggable):
    """
    I am a part of a feed component for a specific group
    of functionality.

    @ivar name:      name of the effect
    @type name:      string
    @ivar component: component owning the effect
    @type component: L{FeedComponent}
    """
    logCategory = "effect"

    def __init__(self, name):
        """
        @param name: the name of the effect
        """
        self.name = name
        self.setComponent(None)

    def setComponent(self, component):
        """
        Set the given component as the effect's owner.
        
        @param component: the component to set as an owner of this effect
        @type  component: L{FeedComponent}
        """                               
        self.component = component
        self.setUIState(component and component.uiState or None)

    def setUIState(self, state):
        """
        Set the given UI state on the effect. This method is ideal for
        adding keys to the UI state.
        
        @param state: the UI state for the component to use.
        @type  state: L{flumotion.common.componentui.WorkerComponentUIState}
        """                               
        self.uiState = state

    def getComponent(self):
        """
        Get the component owning this effect.
        
        @rtype:  L{FeedComponent}
        """                               
        return self.component

class MultiInputParseLaunchComponent(ParseLaunchComponent):
    """
    This class provides for multi-input ParseLaunchComponents, such as muxers,
    with a queue attached to each input.
    """
    QUEUE_SIZE_BUFFERS = 16

    def get_muxer_string(self, properties):
        """
        Return a gst-parse description of the muxer, which must be named 'muxer'
        """
        raise errors.NotImplementedError("Implement in a subclass")

    def get_queue_string(self, eaterAlias):
        return ("queue name=eater:%s-queue max-size-buffers=%d"
                % (eaterAlias, self.QUEUE_SIZE_BUFFERS))

    def get_pipeline_string(self, properties):
        eaters = self.config.get('eater', {})
        sources = self.config.get('source', [])
        if eaters == {} and sources != []:
            # for upgrade without manager restart
            feeds = []
            for feed in sources:
                if not ':' in feed:
                    feed = '%s:default' % feed
                feeds.append(feed)
            eaters = { 'default': [(x, 'default') for x in feeds] }

        pipeline = self.get_muxer_string(properties) + ' '
        for e in eaters:
            for feed, alias in eaters[e]:
                pipeline += '@ eater:%s @ ! muxer. ' % alias

        pipeline += 'muxer.'

        return pipeline

    def unblock_eater(self, eaterAlias):
        # Firstly, ensure that any push in progress is guaranteed to return,
        # by temporarily enlarging the queue
        queuename = "eater:%s-queue" % eaterAlias
        queue = self.pipeline.get_by_name(queuename)

        size = queue.get_property("max-size-buffers")
        queue.set_property("max-size-buffers", size + 1)

        # So, now it's guaranteed to return. However, we want to return the 
        # queue size to its original value. Doing this in a thread-safe manner
        # is rather tricky...
        def _block_cb(pad, blocked):
            # This is called from streaming threads, but we don't do anything
            # here so it's safe.
            pass
        def _underrun_cb(element):
            # Called from a streaming thread. The queue element does not hold
            # the queue lock when this is called, so we block our sinkpad, 
            # then re-check the current level.
            pad = element.get_pad("sink")
            pad.set_blocked_async(True, _block_cb)
            level = element.get_property("current-level-buffers")
            if level < self.QUEUE_SIZE_BUFFERS:
                element.set_property('max-size-buffers', 
                    self.QUEUE_SIZE_BUFFERS)
                element.disconnect(signalid)
            pad.set_blocked_async(False, _block_cb)

        signalid = queue.connect("underrun", _underrun_cb)


