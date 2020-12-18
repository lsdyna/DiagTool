# -*- coding:utf-8 -*-
#  zlgcan_CCDiag.py
#
#  ~~~~~~~~~~~~
#
#  UDS application base on ZLG CAN interface
#
#  ~~~~~~~~~~~~
#
#  ------------------------------------------------------------------
#  Author : CAO Cheng    
#  Last change: 23.10.2020
#
#  Language: Python 3.6
#  ------------------------------------------------------------------

import udsoncan
from udsoncan.connections import BaseConnection
import isotp
from functools import partial

class CCDiagIsotp(TransportLayer):


class ZLGCanConnection(BaseConnection):

    mtu = 4095

    def __init__(self, isotp_layer, name=None):
        BaseConnection.__init__(self, name)
        self.toIsoTPQueue = queue.Queue()
        self.fromIsoTPQueue = queue.Queue()	
        self.rxthread = None
        self.exit_requested = False
        self.opened = False
        self.isotp_layer = isotp_layer

        assert isinstance(self.isotp_layer, isotp.TransportLayer) , 'isotp_layer must be a valid isotp.TransportLayer '

    def open(self, bus=None):
        if bus is not None:
            self.isotp_layer.set_bus(bus)

        self.exit_requested = False
        self.rxthread = threading.Thread(target=self.rxthread_task)
        self.rxthread.start()
        self.opened = True
        self.logger.info('Connection opened')
        return self

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def is_open(self):
        return self.opened 

    def close(self):
        self.empty_rxqueue()
        self.empty_txqueue()
        self.exit_requested=True
        self.rxthread.join()
        self.isotp_layer.reset()
        self.opened = False
        self.logger.info('Connection closed')	

    def specific_send(self, payload):
        if self.mtu is not None:
            if len(payload) > self.mtu:
                self.logger.warning("Truncating payload to be set to a length of %d" % (self.mtu))
                payload = payload[0:self.mtu]

        self.toIsoTPQueue.put(bytearray(payload)) # isotp.protocol.TransportLayer uses byte array. udsoncan is strict on bytes format

    def specific_wait_frame(self, timeout=2):
        if not self.opened:
            raise RuntimeError("Connection is not open")

        timedout = False
        frame = None
        try:
            frame = self.fromIsoTPQueue.get(block=True, timeout=timeout)
        except queue.Empty:
            timedout = True

        if timedout:
            raise TimeoutException("Did not receive frame IsoTP Transport layer in time (timeout=%s sec)" % timeout)

        if self.mtu is not None:
            if frame is not None and len(frame) > self.mtu:
                self.logger.warning("Truncating received payload to a length of %d" % (self.mtu))
                frame = frame[0:self.mtu]

        return bytes(frame)	# isotp.protocol.TransportLayer uses bytearray. udsoncan is strict on bytes format

    def empty_rxqueue(self):
        while not self.fromIsoTPQueue.empty():
            self.fromIsoTPQueue.get()

    def empty_txqueue(self):
        while not self.toIsoTPQueue.empty():
            self.toIsoTPQueue.get()			

    def rxthread_task(self):
        while not self.exit_requested:
            try:
                while not self.toIsoTPQueue.empty():
                    self.isotp_layer.send(self.toIsoTPQueue.get())

                self.isotp_layer.process()

                while self.isotp_layer.available():
                    self.fromIsoTPQueue.put(self.isotp_layer.recv())

                time.sleep(self.isotp_layer.sleep_time())

            except Exception as e:
                self.exit_requested = True
                self.logger.error(str(e))
