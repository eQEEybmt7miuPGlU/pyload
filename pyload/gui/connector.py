# -*- coding: utf-8 -*-
# @author: mkaay


from builtins import object
SERVER_VERSION = "0.5.0"

from uuid import uuid4 as uuid

from PyQt4.QtCore import *
from PyQt4.QtGui import *

import socket

from module.remote.thriftbackend.ThriftClient import ThriftClient, WrongLogin, NoSSL, NoConnection
from thrift.Thrift import TException


class Connector(QObject):
    """
        manages the connection to the pyload core via thrift
    """

    firstAttempt = True

    def __init__(self):
        QObject.__init__(self)
        self.mutex = QMutex()
        self.connectionID = None
        self.host = None
        self.port = None
        self.user = None
        self.password = None
        self.ssl = None
        self.running = True
        self.internal = False
        self.proxy = self.Dummy()

    def setConnectionData(self, host, port, user, password, ssl=False):
        """
            set connection data for connection attempt, called from slotConnect
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl = ssl

    def connectProxy(self):
        """
            initialize thrift rpc client,
            check for ssl, check auth,
            setup dispatcher,
            connect error signals,
            check server version
        """
        if self.internal:
            return True

        err = None
        try:
            client = ThriftClient(self.host, self.port, self.user, self.password)
        except WrongLogin:
            err = _("bad login credentials")
        except NoSSL:
            err = _("no ssl support")
        except NoConnection:
            err = _("can't connect to host")
        if err:
            if not Connector.firstAttempt:
                self.emit(SIGNAL("errorBox"), err)
            Connector.firstAttempt = False
            return False

        self.proxy = DispatchRPC(self.mutex, client)
        self.connect(
            self.proxy,
            SIGNAL("connectionLost"),
            self,
            SIGNAL("connectionLost"))

        server_version = self.proxy.getServerVersion()
        self.connectionID = uuid().hex

        if not server_version == SERVER_VERSION:
            self.emit(SIGNAL("errorBox"), _("server is version {new} client accepts version {current}").format(
                **{"new": server_version, "current": SERVER_VERSION}))
            return False

        return True

    def __getattr__(self, attr):
        """
            redirect rpc calls to dispatcher
        """
        return getattr(self.proxy, attr)

    class Dummy(object):
        """
            dummy rpc proxy, to prevent errors
        """

        def __bool__(self):
            return False

        def __getattr__(self, attr):
            def dummy(*args, **kwargs):
                return None
            return dummy


class DispatchRPC(QObject):
    """
        wraps the thrift client, to catch critical exceptions (connection lost)
        adds thread safety
    """

    def __init__(self, mutex, server):
        QObject.__init__(self)
        self.mutex = mutex
        self.server = server

    def __getattr__(self, attr):
        """
            redirect and wrap call in Wrapper instance, locks dispatcher
        """
        self.mutex.lock()
        self.fname = attr
        f = self.Wrapper(getattr(self.server, attr), self.mutex, self)
        return f

    class Wrapper(object):
        """
            represents a rpc call
        """

        def __init__(self, f, mutex, dispatcher):
            self.f = f
            self.mutex = mutex
            self.dispatcher = dispatcher

        def __call__(self, *args, **kwargs):
            """
                instance is called, rpc is executed
                exceptions are processed
                finally dispatcher is unlocked
            """
            lost = False
            try:
                return self.f(*args, **kwargs)
            except socket.error:  # necessary?
                lost = True
            except TException:
                lost = True
            finally:
                self.mutex.unlock()
            if lost:
                from traceback import print_exc
                print_exc()
                self.dispatcher.emit(SIGNAL("connectionLost"))