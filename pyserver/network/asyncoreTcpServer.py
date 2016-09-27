#!/usr/bin/python
"""
@file asyncoreTcpServer.py
@author Woong Gyu La a.k.a Chris. <juhgiyo@gmail.com>
        <http://github.com/juhgiyo/pyserver>
@date March 10, 2016
@brief AsyncoreTcpServer Interface
@version 0.1

@section LICENSE

The MIT License (MIT)

Copyright (c) 2016 Woong Gyu La <juhgiyo@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

@section DESCRIPTION

AsyncoreTcpServer Class.
"""
import asyncore
import socket
import threading
from collections import deque

from asyncoreController import AsyncoreController
from callbackInterface import *
from serverConf import *
# noinspection PyDeprecation
from sets import Set
from preamble import *
import traceback
import copy

'''
Interfaces
variable
- addr
- callback
function
- def send(data)
- def close() # close the socket
'''


class AsyncoreTcpSocket(asyncore.dispatcher):
    def __init__(self, server, sock, addr, callback):
        asyncore.dispatcher.__init__(self, sock)
        self.server = server
        self.isClosing = False
        self.callback = None
        if callback is not None and isinstance(callback, ITcpSocketCallback):
            self.callback = callback
        else:
            raise Exception('callback is None or not an instance of ITcpSocketCallback class')
        self.addr = addr
        self.transport = {'packet': None, 'type': PacketType.SIZE, 'size': SIZE_PACKET_LENGTH, 'offset': 0}
        self.sendQueue = deque()  # thread-safe queue
        if self.server.no_delay:
            self.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        AsyncoreController.Instance().add(self)
        if callback is not None:
            self.callback.on_newconnection(self, None)

    def handle_read(self):
        try:
            data = self.recv(self.transport['size'])
            if data is None or len(data) == 0:
                return
            if self.transport['packet'] is None:
                self.transport['packet'] = data
            else:
                self.transport['packet'] += data
            read_size = len(data)
            if read_size < self.transport['size']:
                self.transport['offset'] += read_size
                self.transport['size'] -= read_size
            else:
                if self.transport['type'] == PacketType.SIZE:
                    should_receive = Preamble.to_should_receive(self.transport['packet'])
                    if should_receive < 0:
                        preamble_offset = Preamble.check_preamble(self.transport['packet'])
                        self.transport['offset'] = len(self.transport['packet']) - preamble_offset
                        self.transport['size'] = preamble_offset
                        # self.transport['packet'] = self.transport['packet'][
                        #                           len(self.transport['packet']) - preamble_offset:]
                        self.transport['packet'] = self.transport['packet'][preamble_offset:]
                        return
                    self.transport = {'packet': None, 'type': PacketType.DATA, 'size': should_receive, 'offset': 0}
                else:
                    receive_packet = self.transport
                    self.transport = {'packet': None, 'type': PacketType.SIZE, 'size': SIZE_PACKET_LENGTH, 'offset': 0}
                    self.callback.on_received(self, receive_packet['packet'])
        except Exception as e:
            print e
            traceback.print_exc()

    def writable(self):
        return len(self.sendQueue) != 0

    def handle_write(self):
        if len(self.sendQueue) != 0:
            send_obj = self.sendQueue.popleft()
            state = State.SUCCESS
            try:
                sent = asyncore.dispatcher.send(self, send_obj['data'][send_obj['offset']:])
                if sent < len(send_obj['data']):
                    send_obj['offset'] = send_obj['offset'] + sent
                    self.sendQueue.appendLeft(send_obj)
                    return
            except Exception as e:
                print e
                traceback.print_exc()
                state = State.FAIL_SOCKET_ERROR
            try:
                if self.callback is not None:
                    self.callback.on_sent(self, state, send_obj['data'][SIZE_PACKET_LENGTH:])
            except Exception as e:
                print e
                traceback.print_exc()

    def close(self):
        if not self.isClosing:
            self.handle_close()

    def handle_error(self):
        if not self.isClosing:
            self.handle_close()

    def handle_close(self):
        try:
            print 'asyncoreTcpSocket close called'
            self.isClosing = True
            asyncore.dispatcher.close(self)
            self.server.discardSocket(self)
            AsyncoreController.Instance().discard(self)
            if self.callback is not None:
                self.callback.on_disconnect(self)
        except Exception as e:
            print e
            traceback.print_exc()

    def send(self, data):
        self.sendQueue.append({'data': Preamble.to_preamble_packet(len(data)) + data, 'offset': 0})

    def gethostbyname(self, arg):
        return self.socket.gethostbyname(arg)

    def gethostname(self):
        return self.socket.gethostname()


'''
Interfaces
variables
- callback
- acceptor
functions
- def close() # close the socket
- def getSockList()
- def shutdownAllClient()
'''


class AsyncoreTcpServer(asyncore.dispatcher):
    def __init__(self, port, callback, acceptor, bind_addr='', no_delay=True):
        asyncore.dispatcher.__init__(self)
        self.isClosing = False
        self.lock = threading.RLock()
        self.sockSet = Set([])

        self.acceptor = None
        if acceptor is not None and isinstance(acceptor, IAcceptor):
            self.acceptor = acceptor
        else:
            raise Exception('acceptor is None or not an instance of IAcceptor class')
        self.callback = None
        if callback is not None and isinstance(callback, ITcpServerCallback):
            self.callback = callback
        else:
            raise Exception('callback is None or not an instance of ITcpServerCallback class')
        self.port = port
        self.no_delay = no_delay
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind((bind_addr, port))
        self.listen(5)

        AsyncoreController.Instance().add(self)
        if self.callback is not None:
            self.callback.on_started(self)

    def handle_accept(self):
        try:
            sock_pair = self.accept()
            if sock_pair is not None:
                sock, addr = sock_pair
                if not self.acceptor.on_accept(self, addr):
                    sock.close()
                else:
                    sockcallback = self.acceptor.get_socket_callback()
                    sock_obj = AsyncoreTcpSocket(self, sock, addr, sockcallback)
                    with self.lock:
                        self.sockSet.add(sock_obj)
                    if self.callback is not None:
                        self.callback.on_accepted(self, sock_obj)
        except Exception as e:
            print e
            traceback.print_exc()

    def close(self):
        if not self.isClosing:
            self.handle_close()

    def handle_error(self):
        if not self.isClosing:
            self.handle_close()

    def handle_close(self):
        try:
            print 'asyncoreTcpServer close called'
            self.isClosing = True
            with self.lock:
                delete_set = copy.copy(self.sockSet)
                for item in delete_set:
                    item.close()
                self.sockSet = Set([])
            asyncore.dispatcher.close(self)
            AsyncoreController.Instance().discard(self)
            if self.callback is not None:
                self.callback.on_stopped(self)
        except Exception as e:
            print e
            traceback.print_exc()

    def discard_socket(self, sock):
        print 'asyncoreTcpServer discard socket called'
        with self.lock:
            self.sockSet.discard(sock)

    def shutdown_all(self):
        with self.lock:
            delete_set = copy.copy(self.sockSet)
            for item in delete_set:
                item.close()
            self.sockSet = Set([])

    def get_socket_list(self):
        with self.lock:
            return list(self.sockSet)
