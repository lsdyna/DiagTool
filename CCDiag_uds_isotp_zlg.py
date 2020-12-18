# -*- coding:utf-8 -*-
#  zlgcan_CCDiag.py
#
#  ~~~~~~~~~~~~
#
#  Leapmotor development based on ZLGCAN USBCANFD Demo
#
#  ~~~~~~~~~~~~
#
#  ------------------------------------------------------------------
#  Author : CAO Cheng    
#  Last change: 22.10.2020
#
#  Language: Python 3.6
#  ------------------------------------------------------------------
#
# 

from tkinter.constants import N, NSEW, W


from zlgcan import *
import tkinter as tk
from tkinter import Message, ttk, Radiobutton, IntVar
from tkinter import messagebox, filedialog
import threading
import time
import datetime
import json
import queue
import struct
import re

from udsoncan.client import Client
from udsoncan.exceptions import TimeoutException
import udsoncan
from udsoncan.connections import BaseConnection
from udsoncan import services, Response, MemoryLocation

import isotp
from isotp import CanMessage
from functools import partial

GRPBOX_WIDTH    = 200

DIAG_HEIGHT = 470
DIAG_WIDTH = 500

WIDGHT_WIDTH    = GRPBOX_WIDTH + DIAG_WIDTH + 30
WIDGHT_HEIGHT   = DIAG_HEIGHT + 100

MAX_RCV_NUM     = 20

USBCANFD_TYPE    = (41, 42, 43)
USBCAN_XE_U_TYPE = (20, 21, 31)
USBCAN_I_II_TYPE = (3, 4)

ESC_TX_ID = 0x73E
ESC_RX_ID_PHYS = 0x736
ESC_RX_ID_FUNC = 0x7DF

EPS_TX_ID = 0x73D
EPS_RX_ID_PHYS = 0x735

EPS4wd_TX_ID = 0x7BD
EPS4wd_RX_ID_PHYS = 0x7B5

####################################################################################
class PeriodSendThread(object):
    def __init__(self, period_func, args=[], kwargs={}):
        self._thread       = threading.Thread(target=self._run)
        self._function     = period_func
        self._args         = args
        self._kwargs       = kwargs
        self._period       = 0
        self._event        = threading.Event()
        self._period_event = threading.Event() 
        self._terminated   = False 
    
    def start(self):
        self._thread.start()

    def stop(self):
        self._terminated = True
        self._event.set()
        self._thread.join()

    def send_start(self, period):
        self._period = period
        self._event.set()

    def send_stop(self):
        self._period_event.set()

    def _run(self):
        while True:
            self._event.wait()
            self._event.clear()
            if self._terminated:
                break
            self._function(*self._args, **self._kwargs) 
            while not self._period_event.wait(self._period):
                self._function(*self._args, **self._kwargs)
            self._period_event.clear()

###############################################################################
class ZCAN_CCDiag(tk.Tk):

    class IsoTpConnection(BaseConnection):

        mtu = 4095

        def __init__(self, isotp_layer, name=None):
            BaseConnection.__init__(self, name)
            self.toIsoTPQueue = queue.Queue()
            self.fromIsoTPQueue = queue.Queue()
            self._read_thread = None
            self.exit_requested = False
            self.opened = False
            self.isotp_layer = isotp_layer

            assert isinstance(self.isotp_layer, isotp.TransportLayer) , 'isotp_layer must be a valid isotp.TransportLayer '

        def open(self):
            self.exit_requested = False
            self._read_thread = threading.Thread(None, target=self.rxthread_task)
            self._read_thread.start()
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
            self._read_thread.join()
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
                    #self.logger.debug("toIsoTPQueue queue size is now %d" % (self.toIsoTPQueue.qsize()))
                    while not self.toIsoTPQueue.empty():
                        self.isotp_layer.send(self.toIsoTPQueue.get())

                    self.isotp_layer.process()

                    while self.isotp_layer.available():
                        self.fromIsoTPQueue.put(self.isotp_layer.recv())
                    #self.logger.debug("fromIsoTPQueue queue size is now %d" % (self.fromIsoTPQueue.qsize()))

                    #time.sleep(self.isotp_layer.sleep_time())
                    time.sleep(0.0001)

                except Exception as e:
                    self.exit_requested = True
                    self.logger.error(str(e))
                    print("Error occurred while read CAN(FD) data!")

    def __init__(self):
        super().__init__()
        self.title("CCDiag")
        self.resizable(False, False)
        self.geometry(str(WIDGHT_WIDTH) + "x" + str(WIDGHT_HEIGHT) + '+200+100')
        self.protocol("WM_DELETE_WINDOW", self.Form_OnClosing)

        self.DeviceInit()
        self.WidgetsInit()

        self._dev_info = None
        with open("./dev_info.json", "r") as fd:
            self._dev_info = json.load(fd)
        if self._dev_info == None:
            print("device info no exist!")
            return 

        self._DTCList = None
        with open("./DTCList.json", "r") as fd:
            self._DTCList = json.load(fd)
        if self._DTCList == None:
            print("DTCList no exist!")
            return 

        self.DeviceInfoInit()
        self.ChnInfoUpdate(self._isOpen)


    def DeviceInit(self):
        self._zcan       = ZCAN()
        self._dev_handle = INVALID_DEVICE_HANDLE 
        self._can_handle = INVALID_CHANNEL_HANDLE 

        self._isOpen = False
        self._isChnOpen = False

        #current device info
        self._is_canfd = False
        self._res_support = False

        #read can/canfd message thread
        #self._read_thread = None
        self._terminated = False
        self._lock = threading.RLock()

        self.isotp_params = {
            'stmin' : 32,                          # Will request the sender to wait 32ms between consecutive frame. 0-127ms or 100-900ns with values from 0xF1-0xF9
            'blocksize' : 8,                       # Request the sender to send 8 consecutives frames before sending a new flow control message
            'wftmax' : 0,                          # Number of wait frame allowed before triggering an error
            'tx_data_length' : 8,                  # Link layer (CAN layer) works with 8 byte payload (CAN 2.0)
            'tx_padding' : 0,                      # Will pad all transmitted CAN messages with byte 0x00. None means no padding
            'rx_flowcontrol_timeout' : 1000,        # Triggers a timeout if a flow control is awaited for more than 1000 milliseconds
            'rx_consecutive_frame_timeout' : 1000,  # Triggers a timeout if a consecutive frame is awaited for more than 1000 milliseconds
            'squash_stmin_requirement' : False     # When sending, respect the stmin requirement of the receiver. If set to True, go as fast as possible.
            }
        self._isotpaddr_PHYS = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=ESC_RX_ID_PHYS, rxid = ESC_TX_ID)
        self._isotpaddr_FUNC = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=ESC_RX_ID_FUNC, rxid = ESC_TX_ID)
        self._isotpaddr_EPS = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=EPS_RX_ID_PHYS, rxid = EPS_TX_ID)
        self._isotpaddr_EPS4wd = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=EPS4wd_RX_ID_PHYS, rxid = EPS4wd_TX_ID)
        self.isotp_layer = isotp.TransportLayer(rxfn=self.isotp_rcv, txfn=self.isotp_send, address=self._isotpaddr_PHYS, params=self.isotp_params)
        self.conn = ZCAN_CCDiag.IsoTpConnection(isotp_layer = self.isotp_layer)
        self.udsclient = Client(self.conn, request_timeout= 2)
        self.udsclient.config['security_algo'] = self.SecAlgo
        self.udsclient.config['security_algo_params'] = [0x4FE87269, 0x6BC361D8, 0x9B127D51, 0x5BA41903]
        self.udsclient.config['data_identifiers'] = {
            0xF1A8 : udsoncan.DidCodec('B'),
            0xF190 : udsoncan.DidCodec('BBBBBBBBBBBBBBBB'),       # Codec that read ASCII string. We must tell the length of the string
            0xF195 : udsoncan.DidCodec('B'),
            0xF199 : udsoncan.DidCodec('BBBBBBB')
            }
        self.udsclient.config['server_address_format'] = 32
        self.udsclient.config['server_memorysize_format'] = 32


    def WidgetsInit(self):
        self._dev_frame = tk.Frame(self)
        self._dev_frame.grid(row=0, column=0, padx=2, pady=2, sticky=tk.NW)

        # Device connect group
        self.gbDevConnect = tk.LabelFrame(self._dev_frame, height=100, width=GRPBOX_WIDTH, text="设备选择")
        self.gbDevConnect.grid_propagate(0)
        self.gbDevConnect.grid(row=0, column=0, padx=2, pady=2, sticky=tk.NE)
        self.DevConnectWidgetsInit()

        self.gbCANCfg = tk.LabelFrame(self._dev_frame, height=190, width=GRPBOX_WIDTH, text="通道配置")
        self.gbCANCfg.grid(row=1, column=0, padx=2, pady=2, sticky=tk.NSEW)
        self.gbCANCfg.grid_propagate(0)
        self.CANChnWidgetsInit()

        self.gbDevInfo = tk.LabelFrame(self._dev_frame, height=230, width=GRPBOX_WIDTH, text="设备信息")
        self.gbDevInfo.grid(row=2, column=0, padx=2, pady=2, sticky=tk.NSEW)
        self.gbDevInfo.grid_propagate(0)
        self.DevInfoWidgetsInit()

        self.gbDiag = ttk.Notebook(height=DIAG_HEIGHT, width=DIAG_WIDTH + 6)
        self.gbDiag.grid(row=0, column=1, padx=2, pady=2, sticky=tk.NSEW)
        self.gbDiag.grid_propagate(0)
        self.frameesc = tk.Frame(self.gbDiag)
        self.tabesc = self.gbDiag.add(self.frameesc, text = "   ESC   ")
        self.frameeps = tk.Frame(self.gbDiag)
        self.tabeps = self.gbDiag.add(self.frameeps, text = "   EPS   ")
        self.frameepb = tk.Frame(self.gbDiag)
        self.tabepb = self.gbDiag.add(self.frameepb, text = "   EPB   ")
        self.frametest = tk.Frame(self.gbDiag)
        self.tabdiagtest = self.gbDiag.add(self.frametest, text = "   DIAG_TEST   ")
        self.gbDiag.bind('<<NotebookTabChanged>>', self.onTabChange)
        self.DiagWidgetsInit()


    def DeviceInfoInit(self):
        self.cmbDevType["value"] = tuple([dev_name for dev_name in self._dev_info])
        self.cmbDevType.current(3)

    def DevConnectWidgetsInit(self):
        #Device Type
        tk.Label(self.gbDevConnect, text="设备类型:").grid(row=0, column=0, sticky=tk.E)
        self.cmbDevType = ttk.Combobox(self.gbDevConnect, width=16, state="readonly")
        self.cmbDevType.grid(row=0, column=1, sticky=tk.E)

        #Device Index
        tk.Label(self.gbDevConnect, text="设备索引:").grid(row=1, column=0, sticky=tk.E)
        self.cmbDevIdx = ttk.Combobox(self.gbDevConnect, width=16, state="readonly")
        self.cmbDevIdx.grid(row=1, column=1, sticky=tk.E)
        self.cmbDevIdx["value"] = tuple([i for i in range(4)])
        self.cmbDevIdx.current(0)

        #Open/Close Device
        self.strvDevCtrl = tk.StringVar()
        self.strvDevCtrl.set("打开")
        self.btnDevCtrl = ttk.Button(self.gbDevConnect, textvariable=self.strvDevCtrl, command=self.BtnOpenDev_Click)
        self.btnDevCtrl.grid(row=2, column=0, columnspan=2, pady=2)

    def CANChnWidgetsInit(self):
        #CAN Channel
        tk.Label(self.gbCANCfg, anchor=tk.W, text="CAN通道:").grid(row=0, column=0, sticky=tk.W)
        self.cmbCANChn = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbCANChn.grid(row=0, column=1, sticky=tk.E)

        #Work Mode
        tk.Label(self.gbCANCfg, anchor=tk.W, text="工作模式:").grid(row=1, column=0, sticky=tk.W)
        self.cmbCANMode = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbCANMode.grid(row=1, column=1, sticky=tk.E)

        #CAN Baudrate 
        tk.Label(self.gbCANCfg, anchor=tk.W, text="波特率:").grid(row=2, column=0, sticky=tk.W)
        self.cmbBaudrate = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbBaudrate.grid(row=2, column=1, sticky=tk.W)
        
        #CAN Data Baudrate 
        tk.Label(self.gbCANCfg, anchor=tk.W, text="数据域波特率:").grid(row=3, column=0, sticky=tk.W)
        self.cmbDataBaudrate = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbDataBaudrate.grid(row=3, column=1, sticky=tk.W)

        #resistance enable
        tk.Label(self.gbCANCfg, anchor=tk.W, text="终端电阻:").grid(row=4, column=0, sticky=tk.W)
        self.cmbResEnable = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbResEnable.grid(row=4, column=1, sticky=tk.W)

        #UDS protocol used
        tk.Label(self.gbCANCfg, anchor=tk.W, text="UDS protocol:").grid(row=5, column=0, sticky=tk.W)
        self.cmbUDSEnable = ttk.Combobox(self.gbCANCfg, width=12, state="readonly")
        self.cmbUDSEnable.grid(row=5, column=1, sticky=tk.W)

        #CAN Control
        self.strvCANCtrl = tk.StringVar()
        self.strvCANCtrl.set("打开")
        self.btnCANCtrl = ttk.Button(self.gbCANCfg, textvariable=self.strvCANCtrl, command=self.BtnOpenCAN_Click) 
        self.btnCANCtrl.grid(row=6, column=0, columnspan=2, padx=2, pady=2)

    def DevInfoWidgetsInit(self):
        #Hardware Version
        tk.Label(self.gbDevInfo, anchor=tk.W, text="硬件版本:").grid(row=0, column=0, sticky=tk.W)
        self.strvHwVer = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvHwVer).grid(row=0, column=1, sticky=tk.W)

        #Firmware Version
        tk.Label(self.gbDevInfo, anchor=tk.W, text="固件版本:").grid(row=1, column=0, sticky=tk.W)
        self.strvFwVer = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvFwVer).grid(row=1, column=1, sticky=tk.W)

        #Driver Version
        tk.Label(self.gbDevInfo, anchor=tk.W, text="驱动版本:").grid(row=2, column=0, sticky=tk.W)
        self.strvDrVer = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvDrVer).grid(row=2, column=1, sticky=tk.W)

        #Interface Version
        tk.Label(self.gbDevInfo, anchor=tk.W, text="动态库版本:").grid(row=3, column=0, sticky=tk.W)
        self.strvInVer = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvInVer).grid(row=3, column=1, sticky=tk.W)

        #CAN num
        tk.Label(self.gbDevInfo, anchor=tk.W, text="CAN通道数:").grid(row=4, column=0, sticky=tk.W)
        self.strvCANNum = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvCANNum).grid(row=4, column=1, sticky=tk.W)

        #Device Serial
        tk.Label(self.gbDevInfo, anchor=tk.W, text="设备序列号:").grid(row=5, column=0, sticky=tk.W)
        self.strvSerial = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvSerial).grid(row=6, column=0, columnspan=2, sticky=tk.W)
    
        #Hardware type
        tk.Label(self.gbDevInfo, anchor=tk.W, text="硬件类型:").grid(row=7, column=0, sticky=tk.W)
        self.strvHwType = tk.StringVar(value='')
        tk.Label(self.gbDevInfo, anchor=tk.W, textvariable=self.strvHwType).grid(row=8, column=0, columnspan=2, sticky=tk.W)
    
    
    def DiagWidgetsInit(self):
        #ESC part############################################################################################
        self.strvReadDTC = tk.StringVar()
        self.strvReadDTC.set("读取故障码")
        self.btnReadDTC = ttk.Button(self.frameesc, textvariable=self.strvReadDTC, command=self.BtnReadDTC_Click)
        self.btnReadDTC.grid(row=0, column=0, padx=3, pady=3)
        self.btnReadDTC["state"] = tk.DISABLED

        self.strvClrDTC = tk.StringVar()
        self.strvClrDTC.set("清除故障码")
        self.btnClrDTC_1 = ttk.Button(self.frameesc, textvariable=self.strvClrDTC, command=self.BtnClearDTC_Click)
        self.btnClrDTC_1.grid(row=0, column=1, padx=3, pady=3)
        self.btnClrDTC_1["state"] = tk.DISABLED

        self.strvReadSwVer = tk.StringVar()
        self.strvReadSwVer.set("读取软件版本")
        self.btnReadSwVer = ttk.Button(self.frameesc, textvariable=self.strvReadSwVer, command=self.BtnReadSwVer_Click)
        self.btnReadSwVer.grid(row=0, column=2, padx=3, pady=3)
        self.btnReadSwVer["state"] = tk.DISABLED

        self.strvINSCali = tk.StringVar()
        self.strvINSCali.set("INS标定")
        self.btnINSCali = ttk.Button(self.frameesc, textvariable=self.strvINSCali, command=self.BtnINSCali_Click)
        self.btnINSCali.grid(row=1, column=0, padx=3, pady=3)
        self.btnINSCali["state"] = tk.DISABLED

        self.strvC11Config = tk.StringVar()
        self.strvC11Config.set("配置C11高配")
        self.btnC11Config = ttk.Button(self.frameesc, textvariable=self.strvC11Config, command=self.BtnC11Config_Click)
        self.btnC11Config.grid(row=1, column=1, padx=3, pady=3)
        self.btnC11Config["state"] = tk.DISABLED

        self.strvReleaseEPB = tk.StringVar()
        self.strvReleaseEPB.set("释放电子卡钳")
        self.btnReleaseEPB = ttk.Button(self.frameesc, textvariable=self.strvReleaseEPB, command=self.BtnReleaseEPB_Click)
        self.btnReleaseEPB.grid(row=2, column=0, padx=3, pady=3)
        self.btnReleaseEPB["state"] = tk.DISABLED

        self.strvApplyEPB = tk.StringVar()
        self.strvApplyEPB.set("夹紧电子卡钳")
        self.btnApplyEPB = ttk.Button(self.frameesc, textvariable=self.strvApplyEPB, command=self.BtnApplyEPB_Click)
        self.btnApplyEPB.grid(row=2, column=1, padx=3, pady=3)
        self.btnApplyEPB["state"] = tk.DISABLED


        self.swpath4show = tk.StringVar()
        tk.Label(self.frameesc, text = "ESC软件路径:").grid(row = 8, column = 0)
        tk.Entry(self.frameesc, textvariable = self.swpath4show).grid(row = 8, column = 1)
        self.btnFilePath = ttk.Button(self.frameesc, text = "sw路径选择", command = self.BtnSelectSwPath_Click)
        self.btnFilePath.grid(row = 8, column = 2, padx=5, pady=5)

        self.bootpath4show = tk.StringVar()
        tk.Label(self.frameesc, text = "boot软件路径:").grid(row = 9, column = 0)
        tk.Entry(self.frameesc, textvariable = self.bootpath4show).grid(row = 9, column = 1)
        self.btnFilePath = ttk.Button(self.frameesc, text = "boot路径选择", command = self.BtnSelectBootPath_Click)
        self.btnFilePath.grid(row = 9, column = 2, padx=5, pady=5)

        self.btnSwFlash = ttk.Button(self.frameesc, text = "开始刷写", command = self.BtnSwFlash_Click)
        self.btnSwFlash.grid(row = 10, column = 0, columnspan=2,  padx=10, pady=10)
        self.btnSwFlash["state"] = tk.DISABLED





     
        self.strvResetECU = tk.StringVar()
        self.strvResetECU.set("重启ECU")
        self.btnResetECU = ttk.Button(self.frameesc, textvariable=self.strvResetECU, command=self.BtnResetECU_Click)
        self.btnResetECU.grid(row=7, column=0, padx=3, pady=3)
        self.btnResetECU["state"] = tk.DISABLED




        #EPS part#######################################################################
        self.strvCaliEPS2wd = tk.StringVar()
        self.strvCaliEPS2wd.set("C11两驱SAS中位标定")
        self.btnCaliEPS2wd = ttk.Button(self.frameeps, textvariable=self.strvCaliEPS2wd, command=self.BtnCaliEPS2wd_Click)
        self.btnCaliEPS2wd.grid(row=0, column=0, padx=3, pady=3)
        self.btnCaliEPS2wd["state"] = tk.DISABLED

        self.strvCaliEPS4wd = tk.StringVar()
        self.strvCaliEPS4wd.set("C11四驱SAS中位标定")
        self.btnCaliEPS4wd = ttk.Button(self.frameeps, textvariable=self.strvCaliEPS4wd, command=self.BtnCaliEPS4wd_Click)
        self.btnCaliEPS4wd.grid(row=1, column=0, padx=3, pady=3)
        self.btnCaliEPS4wd["state"] = tk.DISABLED

        self.strvDeCaliEPS4wd = tk.StringVar()
        self.strvDeCaliEPS4wd.set("C11四驱SAS中位解标")
        self.btnDeCaliEPS4wd = ttk.Button(self.frameeps, textvariable=self.strvDeCaliEPS4wd, command=self.BtnDeCaliEPS4wd_Click)
        self.btnDeCaliEPS4wd.grid(row=1, column=1, padx=3, pady=3)
        self.btnDeCaliEPS4wd["state"] = tk.DISABLED

        
        #diag test part################################################################
        self._v = IntVar()
        self.Radioesc = Radiobutton(self.frametest,text='ESC',variable=self._v,value=1) #TODO. need to add function for switching addressing
        self.Radioesc.grid(row=0, column=0, padx=3, pady=3)
        self.Radioeps = Radiobutton(self.frametest,text='EPS',variable=self._v,value=2)
        self.Radioeps.grid(row=0, column=1, padx=3, pady=3)
        self.Radioepb = Radiobutton(self.frametest,text='EPB',variable=self._v,value=3)
        self.Radioepb.grid(row=0, column=2, padx=3, pady=3)

        self.testlog = tk.Text(self.frametest, width=70, height=30) 
        self.testlog.grid(row=2, column=0, rowspan=10, columnspan=10)

        self.btnAutoDiagTest = ttk.Button(self.frametest, text="开始测试", command=self.BtnAutoDiagTest_Click)
        self.btnAutoDiagTest.grid(row=13, column=0, padx=3, pady=3)
        self.btnAutoDiagTest["state"] = tk.DISABLED
        self.btnExportReport = ttk.Button(self.frametest, text="输出测试报告", command=self.BtnExportReport_Click)
        self.btnExportReport.grid(row=13, column=1, padx=3, pady=3)
        self.btnExportReport["state"] = tk.DISABLED
        self.btnClrReport = ttk.Button(self.frametest, text="清空屏幕", command=self.BtnClrReport_Click)
        self.btnClrReport.grid(row=13, column=2, padx=3, pady=3)
        self.btnClrReport["state"] = tk.DISABLED








###############################################################################
### Function 
###############################################################################


    def ChnInfoUpdate(self, is_open):
        #通道信息获取
        cur_dev_info = self._dev_info[self.cmbDevType.get()]
        cur_chn_info = cur_dev_info["chn_info"]
        
        if is_open:
            # 通道 
            self.cmbCANChn["value"] = tuple([i for i in range(cur_dev_info["chn_num"])])
            self.cmbCANChn.current(0)

            # 工作模式
            self.cmbCANMode["value"] = ("正常模式", "只听模式")
            self.cmbCANMode.current(0)

            # 波特率
            self.cmbBaudrate["value"] = tuple([brt for brt in cur_chn_info["baudrate"].keys()])
            self.cmbBaudrate.current(len(self.cmbBaudrate["value"]) - 3)

            if cur_chn_info["is_canfd"] == True:
                # 数据域波特率 
                self.cmbDataBaudrate["value"] = tuple([brt for brt in cur_chn_info["data_baudrate"].keys()])
                self.cmbDataBaudrate.current(0)
                self.cmbDataBaudrate["state"] = "readonly"

            if cur_chn_info["sf_res"] == True:
                self.cmbResEnable["value"] = ("使能", "失能")
                self.cmbResEnable.current(0)
                self.cmbResEnable["state"] = "readonly"
            
            # 是否启用UDS协议
            self.cmbUDSEnable["value"] = ("是", "否")
            self.cmbUDSEnable.current(0)
            self.cmbUDSEnable["state"] = "readonly"

            self.btnCANCtrl["state"] = tk.NORMAL
        else:
            self.cmbCANChn["state"] = tk.DISABLED
            self.cmbCANMode["state"] = tk.DISABLED
            self.cmbBaudrate["state"] = tk.DISABLED
            self.cmbDataBaudrate["state"] = tk.DISABLED
            self.cmbResEnable["state"] = tk.DISABLED
            self.cmbUDSEnable["state"] = tk.DISABLED
            
            self.cmbCANChn["value"] = ()
            self.cmbCANMode["value"] = ()
            self.cmbBaudrate["value"] = ()
            self.cmbDataBaudrate["value"] = ()
            self.cmbResEnable["value"] = ()
            self.cmbUDSEnable["value"] = ()

            self.btnCANCtrl["state"] = tk.DISABLED

    def ChnInfoDisplay(self, enable):
        if enable:
            self.cmbCANChn["state"] = "readonly"
            self.cmbCANMode["state"] = "readonly"
            self.cmbBaudrate["state"] = "readonly" 
            if self._is_canfd: 
                self.cmbDataBaudrate["state"] = "readonly" 
            if self._res_support: 
                self.cmbResEnable["state"] = "readonly"
            self.cmbUDSEnable["state"] = "readonly"
        else:
            self.cmbCANChn["state"] = tk.DISABLED
            self.cmbCANMode["state"] = tk.DISABLED
            self.cmbBaudrate["state"] = tk.DISABLED
            self.cmbDataBaudrate["state"] = tk.DISABLED
            self.cmbResEnable["state"] = tk.DISABLED
            self.cmbUDSEnable["state"] = tk.DISABLED


    def DevInfoRead(self):
        info = self._zcan.GetDeviceInf(self._dev_handle)
        if info != None:
            self.strvHwVer.set(info.hw_version)
            self.strvFwVer.set(info.fw_version)
            self.strvDrVer.set(info.dr_version)
            self.strvInVer.set(info.in_version)
            self.strvCANNum.set(str(info.can_num))
            self.strvSerial.set(info.serial)
            self.strvHwType.set(info.hw_type)

    def DevInfoClear(self):
        self.strvHwVer.set('')
        self.strvFwVer.set('')
        self.strvDrVer.set('')
        self.strvInVer.set('')
        self.strvCANNum.set('')
        self.strvSerial.set('')
        self.strvHwType.set('')

    def MsgBox4Dtc(self, dtcs):
        self.DTCBox = tk.Toplevel()
        self.DTCBox.geometry('320x400')
        self.DTCBox.title('故障列表')
        if dtcs is not None:

            for i in range(min(len(dtcs),16)):
                tk.Label(self.DTCBox, anchor= tk.W, text=i).grid(row=i, column=0,sticky=tk.W)
                tk.Label(self.DTCBox, anchor=tk.W, text=hex(dtcs[i].id)).grid(row=i, column=1,sticky=tk.W)
                if hex(dtcs[i].id) in self._DTCList.keys():
                    tk.Label(self.DTCBox, anchor=tk.W, text=self._DTCList[hex(dtcs[i].id)]).grid(row=i, column=2,sticky=tk.W)
                else:
                    tk.Label(self.DTCBox, anchor=tk.W, text="unknow DTC").grid(row=i, column=2,sticky=tk.W)
            #Clear DTC
            self.btnClrDTC = ttk.Button(self.DTCBox, textvariable=self.strvClrDTC, command=self.BtnClearDTC_Click)
            self.btnClrDTC.grid(row= min(len(dtcs),16)+1, column=0, columnspan=3, pady=2)
        else:
            tk.Label(self.DTCBox, anchor= tk.N, text="无故障！ :)")
        

        self.btnReadDTC["state"] = tk.DISABLED #disable reading DTC to avoid multi DTC windows
        self.DTCBox.protocol('WM_DELETE_WINDOW', self.CloseDTCBox)  #enable reading DTC again

    def CloseDTCBox(self):
        self.btnReadDTC["state"] = tk.NORMAL
        self.DTCBox.destroy()
        



    def SecAlgo(self, level, seed, params):
        """
    Builds the security key to unlock a security level.
    
        temp_key = bytearray(seed)
        self.output_key = bytearray(seed)
        xorkey = bytearray(params['xorkey'])

        for i in range(len(temp_key)):
            temp_key[i] = temp_key[i] ^ xorkey[i]

        self.output_key[0] = (temp_key[3] & 0x0F) | (temp_key[2] & 0xF0)
        self.output_key[1] = ((temp_key[2] & 0x1F) << 3) | ((temp_key[1] & 0xF8) >> 3)
        self.output_key[2] = ((temp_key[1] & 0xFC) >> 2) | (temp_key[0] & 0xC0)
        self.output_key[3] = ((temp_key[0] & 0x0F) << 4) | (temp_key[3] & 0x0F)
        """
        temp_key = (seed[0]<<24) | (seed[1] << 16) | (seed[2] << 8) | (seed[3])
        if level == 0x01:
            output_key_temp = ((((temp_key >> 4) ^ temp_key) << 3) ^ temp_key) & 0xFFFFFFFF
        elif level == 0x11:
            _temp_y = ((temp_key<<24) & 0xFF000000) + ((temp_key<<8) & 0xFF0000) + ((temp_key>>8) & 0xFF00) + ((temp_key>>24) & 0xFF)
            _temp_z = 0
            _temp_sum = 0
            for i in range(64):
                _temp_y += ((((_temp_z<<4) ^ (_temp_z>>5)) + _temp_z) ^ (_temp_sum + params[_temp_sum&0x3])) & 0xFFFFFFFF
                _temp_y = _temp_y & 0xFFFFFFFF
                _temp_sum += 0x8F750A1D
                _temp_sum = _temp_sum & 0xFFFFFFFF 
                _temp_z += ((((_temp_y<<4) ^ (_temp_y>>5)) + _temp_y) ^ (_temp_sum + params[(_temp_sum>>11)&0x3])) & 0xFFFFFFFF
                _temp_z = _temp_z & 0xFFFFFFFF
            output_key_temp = (((_temp_z<<24) & 0xFF000000) | ((_temp_z<<8) & 0xFF0000) | ((_temp_z>>8) & 0xFF00) | ((_temp_z>>24) & 0xFF))
        else:
            output_key_temp = temp_key

        output_key = struct.pack('BBBB', (output_key_temp>>24)&0xFF, (output_key_temp>>16)&0xFF, (output_key_temp>>8)&0xFF, output_key_temp&0xFF)

        return output_key

    def getDateTimeBytes(self):
        """
        get year/month/day and convert into bytes
        """
        _year_high = int(str(datetime.datetime.now().year), 16) >> 8
        _year_low = int(str(datetime.datetime.now().year), 16) & 0xFF
        _month = int(str(datetime.datetime.now().month), 16)
        _day = int(str(datetime.datetime.now().day), 16)
        _hour = int(str(datetime.datetime.now().hour), 16)
        _minute = int(str(datetime.datetime.now().minute), 16)
        _second = int(str(datetime.datetime.now().second), 16)

        return (_year_high, _year_low, _month, _day, _hour, _minute, _second)



        
###############################################################################
### Event handers
###############################################################################
    def Form_OnClosing(self):
        if self._isOpen:
            self.btnDevCtrl.invoke()

        self.destroy()

    def BtnOpenDev_Click(self):
        if self._isOpen:
            #Close Channel 
            if self._isChnOpen:
                self.btnCANCtrl.invoke()

            #Close Device
            self._zcan.CloseDevice(self._dev_handle)

            self.DevInfoClear()
            self.strvDevCtrl.set("打开")
            self.cmbDevType["state"] = "readonly"
            self.cmbDevIdx["state"] = "readonly"
            self._isOpen = False
        else:
            self._cur_dev_info = self._dev_info[self.cmbDevType.get()]

            #Open Device
            self._dev_handle = self._zcan.OpenDevice(self._cur_dev_info["dev_type"], 
                                                     self.cmbDevIdx.current(), 0)
            if self._dev_handle == INVALID_DEVICE_HANDLE:
                #Open failed
                messagebox.showerror(title="打开设备", message="打开设备失败！")
                return 
            
            #Update Device Info Display
            self.DevInfoRead()

            self._is_canfd = self._cur_dev_info["chn_info"]["is_canfd"]
            self._res_support = self._cur_dev_info["chn_info"]["sf_res"]
            self.strvDevCtrl.set("关闭")
            self.cmbDevType["state"] = tk.DISABLED
            self.cmbDevIdx["state"] = tk.DISABLED
            self._isOpen = True 
        self.ChnInfoUpdate(self._isOpen)
        self.ChnInfoDisplay(self._isOpen)

    def BtnOpenCAN_Click(self):
        if self._isChnOpen:
            #wait read_thread exit
            self._terminated = True

            #Close channel
            self._zcan.ResetCAN(self._can_handle)
            self.strvCANCtrl.set("打开")
            self._isChnOpen = False
            self.udsclient.close()
        else:
            #Initial channel
            if self._res_support: #resistance enable
                ip = self._zcan.GetIProperty(self._dev_handle)
                self._zcan.SetValue(ip, 
                                    str(self.cmbCANChn.current()) + "/initenal_resistance", 
                                    '1' if self.cmbResEnable.current() == 0 else '0')
                self._zcan.ReleaseIProperty(ip)

            #set usbcan-e-u baudrate
            if self._cur_dev_info["dev_type"] in USBCAN_XE_U_TYPE:
                ip = self._zcan.GetIProperty(self._dev_handle)
                self._zcan.SetValue(ip, 
                                    str(self.cmbCANChn.current()) + "/baud_rate", 
                                    self._cur_dev_info["chn_info"]["baudrate"][self.cmbBaudrate.get()])
                self._zcan.ReleaseIProperty(ip)

            #set usbcanfd clock 
            if self._cur_dev_info["dev_type"] in USBCANFD_TYPE:
                ip = self._zcan.GetIProperty(self._dev_handle)
                self._zcan.SetValue(ip, str(self.cmbCANChn.current()) + "/clock", "60000000")
                self._zcan.ReleaseIProperty(ip)
            
            chn_cfg = ZCAN_CHANNEL_INIT_CONFIG()
            chn_cfg.can_type = ZCAN_TYPE_CANFD if self._is_canfd else ZCAN_TYPE_CAN
            if self._is_canfd:
                chn_cfg.config.canfd.mode = self.cmbCANMode.current()
                chn_cfg.config.canfd.abit_timing = self._cur_dev_info["chn_info"]["baudrate"][self.cmbBaudrate.get()]
                chn_cfg.config.canfd.dbit_timing = self._cur_dev_info["chn_info"]["data_baudrate"][self.cmbDataBaudrate.get()]
            else:
                chn_cfg.config.can.mode = self.cmbCANMode.current()
                if self._cur_dev_info["dev_type"] in USBCAN_I_II_TYPE:
                    brt = self._cur_dev_info["chn_info"]["baudrate"][self.cmbBaudrate.get()]
                    chn_cfg.config.can.timing0 = brt["timing0"]
                    chn_cfg.config.can.timing1 = brt["timing1"]
                    chn_cfg.config.can.acc_code = 0
                    chn_cfg.config.can.acc_mask = 0xFFFFFFFF

            self._can_handle = self._zcan.InitCAN(self._dev_handle, self.cmbCANChn.current(), chn_cfg)
            if self._can_handle == INVALID_CHANNEL_HANDLE:
                messagebox.showerror(title="打开通道", message="初始化通道失败!")
                return 

            ret = self._zcan.StartCAN(self._can_handle)
            if ret != ZCAN_STATUS_OK: 
                messagebox.showerror(title="打开通道", message="打开通道失败!")
                return 

            #start receive thread
            if self.cmbUDSEnable.get() == '是':
                self.udsclient.open()
                self._terminated = False
            else:
                pass

           
            self.strvCANCtrl.set("关闭")
            self._isChnOpen = True 
            self.btnReadDTC["state"] = tk.NORMAL
            self.btnResetECU["state"] = tk.NORMAL
            self.btnClrDTC_1["state"] = tk.NORMAL
            self.btnReadSwVer["state"] = tk.NORMAL
            self.btnINSCali["state"] = tk.NORMAL
            self.btnC11Config["state"] = tk.NORMAL
            self.btnReleaseEPB["state"] = tk.NORMAL
            self.btnApplyEPB["state"] = tk.NORMAL
            self.btnAutoDiagTest["state"] = tk.NORMAL
            self.btnExportReport["state"] = tk.NORMAL
            self.btnClrReport["state"] = tk.NORMAL
            #self.btnSwFlash["state"] = tk.NORMAL
            self.btnCaliEPS2wd["state"] = tk.NORMAL
            self.btnCaliEPS4wd["state"] = tk.NORMAL
            self.btnDeCaliEPS4wd["state"] = tk.NORMAL


        self.ChnInfoDisplay(not self._isChnOpen)

    def onTabChange(self, event):
        #if self.gbDiag == self.tabesc:
        if event.widget.index("current") == 0: 
            self.isotp_layer.set_address(self._isotpaddr_PHYS)
            print("INFO:Setting address ESP")
        #elif self.gbDiag == self.tabeps:
        elif event.widget.index("current") == 1: 
            self.isotp_layer.set_address(self._isotpaddr_EPS)
            print("INFO:Setting address EPS")
        elif self.gbDiag == self.tabepb:
            pass
            



    def BtnReadDTC_Click(self):
        #self.udsclient.change_session(1)
        #try:
        response = self.udsclient.get_dtc_by_status_mask(9)
        self.MsgBox4Dtc(response.service_data.dtcs)
        #except:
            #messagebox.showerror(title="读取故障码", message="读取故障码失败！")


    def BtnClearDTC_Click(self):
        #self.udsclient.change_session(1)
        response = self.udsclient.clear_dtc(0xFFFFFF)
        if response.positive and self.DTCBox is not None:
            self.DTCBox.destroy()
            self.BtnReadDTC_Click()

    def BtnReadSwVer_Click(self):
        self.udsclient.change_session(3)
        self.udsclient.unlock_security_access(1)
        resp = self.udsclient.read_data_by_identifier(0xF195)
        print(resp)
        
        


    def BtnResetECU_Click(self):
        self.udsclient.ecu_reset(1)

    def BtnINSCali_Click(self):
        self.udsclient.change_session(1)
        try:
            self.udsclient.change_session(3)
            self.udsclient.unlock_security_access(1)
            resp_1 = self.udsclient.start_routine(routine_id = 0xF001)
            resp_2 = self.udsclient.start_routine(routine_id = 0xF002)
            if resp_1.positive & resp_2.positive:
                messagebox.showinfo(title='INS Calibration', message='INS标定成功！')
        except:
            messagebox.showerror(title="INS Calibration", message="INS标定失败！")

    def BtnC11Config_Click(self):
        self.udsclient.change_session(1)
        try:
            self.udsclient.change_session(3)
            self.udsclient.unlock_security_access(1)
            #resp_2 = self.udsclient.write_data_by_identifier(did = 0xF190, value = 0x0F)
            resp_1 = self.udsclient.write_data_by_identifier(did = 0xF1A8, value = 0x0F)
            if resp_1.positive :
                messagebox.showinfo(title='Variant Confiuration', message='Confiure Success！')
        except:
            messagebox.showerror(title="Variant Confiuration", message="Confiure Failed！")

    def BtnReleaseEPB_Click(self):
        self.udsclient.change_session(3)
        self.udsclient.unlock_security_access(1)
        resp_1 = self.udsclient.start_routine(routine_id = 0xF102)
        print(resp_1)

    def BtnApplyEPB_Click(self):
        self.udsclient.change_session(3)
        self.udsclient.unlock_security_access(1)
        resp_1 = self.udsclient.start_routine(routine_id = 0xF105)
        print(resp_1)




    def BtnSelectSwPath_Click(self):
        self.swpath = filedialog.askopenfilename()
        self.swpath4show.set(self.swpath)

    def BtnSelectBootPath_Click(self):
        self.bootpath = filedialog.askopenfilename()
        self.bootpath4show.set(self.bootpath)

    def BtnSwFlash_Click(self):
        """
        read .s19 file and flash according to leap flash spec, this function is leap and conti esc specific.
        """
        #esp sw part #################################3###########
        self._espsw = None
        with open(self.swpath, "r") as fd:
            self._espsw = fd.readlines()

        _espsw = self._espsw[1:-1]
        _espSwPartsNum = 0
        _espSwParts = []
        _cutPoint = 0
        _espDataBatch = {}
        for i in range(1,len(_espsw)):
            if (int(_espsw[i][4:10],16) - int(_espsw[i-1][4:10],16)) != (int(_espsw[i-1][2:4],16) - 4):
                _espSwParts.append(_espsw[_cutPoint:i])
                _cutPoint = i 
                _espSwPartsNum += 1
        _espSwParts.append([_espsw[len(_espsw)-1]])
        _espSwPartsNum += 1

        for part in _espSwParts:
            _espswmemaddr = int(part[0][4:10],16)
            _espswmemsize = int(part[-1][4:10],16) - int(part[0][4:10],16) + int(part[-1][2:4],16) - 4
            _batch = []
            for line in part:
                _L =  list(range(len(line)))
                for i in _L[10:((int(line[2:4],16)-4)*2+10):2]:
                    _batch.append(int(line[i:i+2],16))
            _espDataBatch[MemoryLocation(address=_espswmemaddr, memorysize=_espswmemsize)] = _batch        

        ## boot sw part################################################3
        self._bootsw = None
        with open(self.bootpath, "r") as fd:
            self._bootsw = fd.readlines()

        _bootsw = self._bootsw[1:-1]
        _bootSwPartsNum = 0
        _bootSwParts = []
        _cutPoint = 0
        _bootDataBatch = {}
        for i in range(1,len(_bootsw)):
            if (int(_bootsw[i][4:10],16) - int(_bootsw[i-1][4:10],16)) != (int(_bootsw[i-1][2:4],16) - 4):
                _bootSwParts.append(_bootsw[_cutPoint:i])
                _cutPoint = i 
                _bootSwPartsNum += 1
        _bootSwParts.append([_bootsw[len(_bootsw)-1]])
        _bootSwPartsNum += 1

        try:
            for part in _bootSwParts:
                _bootswmemaddr = int(part[0][4:10],16)
                _bootswmemsize = int(part[-1][4:10],16) - int(part[0][4:10],16) + int(part[-1][2:4],16) - 4
                _batch = []
                for line in part:
                    _L =  list(range(len(line)))
                    for i in _L[10:((int(line[2:4],16)-4)*2+10):2]:
                        _batch.append(int(line[i:i+2],16))
                _bootDataBatch[MemoryLocation(address=_bootswmemaddr, memorysize=_bootswmemsize)] = _batch
            _bootswmemaddr = int(_bootSwParts[0][0][4:10],16)
            _bootswmemsize = int(_bootSwParts[0][-1][4:10],16) - int(_bootSwParts[0][0][4:10],16) + int(_bootSwParts[0][-1][2:4],16) - 4
            _activeFPMem = MemoryLocation(address=_bootswmemaddr, memorysize=_bootswmemsize)

            ####pre programming step
            self.udsclient.change_session(3)
            self.udsclient.unlock_security_access(1)
            #resp_1 = self.udsclient.read_data_by_identifier(0xF195)
            self.udsclient.start_routine(routine_id = 0x0203)
            self.udsclient.control_dtc_setting(services.ControlDTCSetting.SettingType.off)
            self.udsclient.communication_control(0x3, 0x3)
            self.udsclient.change_session(2)
            self.udsclient.unlock_security_access(0x11)
            self.udsclient.write_data_by_identifier(did = 0xF199, value = self.getDateTimeBytes())

            #server programming step
            for k in _bootDataBatch.keys():
                v = _bootDataBatch[k]
                resp_2 = self.udsclient.request_download(memory_location= k)
                _maxNumOfBlockLen = ((resp_2.data[1]<<8) | (resp_2.data[2] & 0xFF))####################################################################################to be double check for ECU specific
                _blockSequenceCounter = 1
                while len(v) > _maxNumOfBlockLen:
                    self.udsclient.transfer_data(sequence_number = _blockSequenceCounter, data = v[0:(_maxNumOfBlockLen-2)])
                    _blockSequenceCounter += 1
                    _blockSequenceCounter & 0xFF
                    v = v[(_maxNumOfBlockLen-2):]
                self.udsclient.transfer_data(sequence_number=_blockSequenceCounter, data= v)
                self.udsclient.request_transfer_exit()

            self.udsclient.start_routine(routine_id = 0x0202, data=_bootswmemaddr)

            for k in _espDataBatch.keys():
                _data = struct.pack('BBBBBBBB', 0x00, k.adrress >> 16, (k.address >> 8) & 0xFF, (k.adrress & 0xFF), 0x00, k.memorysize >> 16, (k.memorysize >> 8) & 0xFF, (k.memorysize & 0xFF))
                self.udsclient.start_routine(routine_id = 0xFF00, data = _data)
                v = _espDataBatch[k]
                resp_3 = self.udsclient.request_download(memory_location=k)
                _maxNumOfBlockLen = ((resp_3.data[1]<<8) | (resp_3.data[2] & 0xFF))
                _blockSequenceCounter = 1
                while len(v) > _maxNumOfBlockLen:
                    self.udsclient.transfer_data(sequence_number= _blockSequenceCounter, data = v[0:(_maxNumOfBlockLen-2)])
                    _blockSequenceCounter += 1
                    _blockSequenceCounter & 0xFF
                    v = v[(_maxNumOfBlockLen-2):]
                self.udsclient.transfer_data(sequence_number=_blockSequenceCounter, data= v)
                self.udsclient.request_transfer_exit()
            
            self.udsclient.start_routine(routine_id=0xFF00)

            #Post programming step
            self.udsclient.ecu_reset(3)
            self.udsclient.change_session(3)
            self.udsclient.communication_control(0x0, 0x3)
            self.udsclient.control_dtc_setting(services.ControlDTCSetting.SettingType.on)
            self.udsclient.change_session(1)
            print("INFO: UDS Client Flash success!")
        except:
            print("INFO: UDS Client Flash fail!")





    def BtnCaliEPS2wd_Click(self):
        """
        docstring
        """
        pass

    def BtnCaliEPS4wd_Click(self):
        """
        docstring
        """
        self.isotp_layer.set_address(self._isotpaddr_EPS4wd)
        try:
            payload_send = struct.pack("BBBBBBBB", 0x01, 0x01, 0x39, 0x00, 0x00, 0x00, 0x00, 0x00)
            payload_send = CanMessage(arbitration_id = 0x7B5, dlc=8, data=payload_send, extended_id=False)
            self.conn.send(payload_send)
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x01\x00\x00\x00\x00\x00':
                print('CCP连接成功!')
            else:
                print('CCP连接失败!')
            self.conn.send(b'\x02\x02\x00\x00\x22\xC3\x00\x00')
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x02\x00\x00\x00\x00\x00':
                print('CCP设定标定参数地址成功!')
            else:
                print('CCP设定标定参数地址失败!')
            self.conn.send(b'\x03\x03\x01\x01\x22\xC3\x00\x00')
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x03\x00\x00\x00\x00\x00':
                print('CCP标定写参数成功!')
                print('标定成功!')
            else:
                print('CCP标定写参数失败!')
            
        except:
            print('标定失败!')
        self.isotp_layer.set_address(self._isotpaddr_EPS)
            
    def BtnDeCaliEPS4wd_Click(self):
        """
        docstring
        """
        self.isotp_layer.set_address(self._isotpaddr_EPS4wd)
        try:
            payload_send = struct.pack("BBBBBBBB", 0x01, 0x01, 0x39, 0x00, 0x00, 0x00, 0x00, 0x00)
            #payload_send = CanMessage(arbitration_id = arbitration_id, dlc=self.get_dlc(data, validate_tx=True), data=data, extended_id=self.address.is_29bits, is_fd=self.params.can_fd)
            self.conn.send(payload_send)
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x01\x00\x00\x00\x00\x00':
                print('CCP连接成功!')
            else:
                print('CCP连接失败!')
            self.conn.send(b'\x02\x02\x00\x00\x88\x0B\xE0\x00')
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x02\x00\x00\x00\x00\x00':
                print('CCP设定解标参数地址成功!')
            else:
                print('CCP设定解标参数地址失败!')
            self.conn.send(b'\x03\x03\x01\x00\x00\x00\x00\x00')
            payload_rcv = self.conn.wait_frame(timeout=1)
            if payload_rcv == b'\xFF\x00\x03\x00\x00\x00\x00\x00':
                print('CCP解标写参数成功!')
            else:
                print('CCP解标写参数失败!')
            print('标定成功!')
        except:
            print('标定失败!')
        self.isotp_layer.set_address(self._isotpaddr_EPS)
    


    def BtnAutoDiagTest_Click(self):
        """
        docstring
        """
        self.isotp_layer.set_address(self._isotpaddr_PHYS)
        self.DiagTestServ10(mode=1)
        self.isotp_layer.set_address(self._isotpaddr_FUNC)
        self.DiagTestServ10(mode=0)


    def DiagTestServ10(self, mode):
        if mode == 1:
            _modetext = "物理寻址"
        else:
            _modetext = "功能寻址"

        resp = self.udsclient.change_session(1)
        _pass = "Pass" if resp.positive else "Fail"
        self.testlog.insert("insert", "%s下，10服务正响应测试，得到正响应。测试结果：%s\n" % (_modetext,_pass))

        try:
            req = services.DiagnosticSessionControl.make_request(0x07)
            self.conn.send(req.get_payload())
            payload = self.conn.wait_frame(timeout=1)
            resp = Response.from_payload(payload)
            _pass = "Pass" if resp.code == Response.Code.SubFunctionNotSupported else "Fail"
        except:
            _pass = "Fail"
        #print(resp.code)
        self.testlog.insert("insert", "%s下，10服务子功能不支持测试，得到NRC12返信。测试结果：%s\n" % (_modetext,_pass))

        try:
            self.conn.send(b'\x10\x01\x77\x88\x99')
            payload = self.conn.wait_frame(timeout=1)
            resp = Response.from_payload(payload)
            _pass = "Pass" if resp.code == Response.Code.IncorrectMessageLengthOrInvalidFormat else "Fail"
        except:
            _pass = "Fail"
        #print(resp.code)
        self.testlog.insert("insert", "%s下，10服务格式或长度不正确测试，得到NRC13返信。测试结果：%s\n" % (_modetext,_pass))

        self.testlog.insert("insert", "%s下，10服务前置条件不满足测试，得到NRC22返信。测试结果： No Test\n"% (_modetext))

        #resp = self.udsclient.change_session(3)
        try:
            self.conn.send(b'\x10\x02')
            payload = self.conn.wait_frame(timeout=1)
            resp = Response.from_payload(payload)
            _pass = "Pass" if resp.code == Response.Code.SubFunctionNotSupportedInActiveSession else "Fail"
        except:
            _pass = "Fail"
        #print(hex(resp.code))
        self.testlog.insert("insert", "%s下，10服务当前会话下子功能不支持测试，得到NRC$7E返信。测试结果：%s\n" % (_modetext,_pass))

        try:
            self.conn.send(b'\x10\x02')
            payload = self.conn.wait_frame(timeout=1)
            resp = Response.from_payload(payload)
            _pass = "Pass" if resp.code == Response.Code.SubFunctionNotSupportedInActiveSession else "Fail"
        except:
            _pass = "Fail"
        #print(hex(resp.code))
        self.testlog.insert("insert", "%s下，10服务NRC优先级测试。测试结果：No Test\n"% (_modetext))

        try:
            resp1 = self.udsclient.change_session(3)
            resp2 = self.udsclient.change_session(1)
            _pass = "Pass" if resp1.positive and resp2.positive else "Fail"
        except:
            _pass = "Fail"
        self.testlog.insert("insert", "%s下，10服务会话切换测试。测试结果：%s\n" % (_modetext,_pass))

        self.testlog.insert("insert", "%s下，时间超时后会话维持情况测试。测试结果： No Test\n"% (_modetext))

        self.testlog.insert("insert", "%s下，KL15on-off-on会话维持情况测试。测试结果： No Test\n"% (_modetext))

        self.testlog.insert("insert", "%s下，硬件复位后会话维持情况测试。测试结果： No Test\n"% (_modetext))


    def BtnExportReport_Click(self):
        """
        docstring
        """
        pass
    
    
    def BtnClrReport_Click(self):
        self.testlog.delete('1.0','end')


###############################################################################
### isotp interface
###############################################################################
        """
        isotp interface
        """

    def isotp_rcv(self):

        can_num = self._zcan.GetReceiveNum(self._can_handle, ZCAN_TYPE_CAN)
        if can_num and not self._terminated:
            read_cnt = MAX_RCV_NUM if can_num >= MAX_RCV_NUM else can_num
            can_msgs, act_num = self._zcan.Receive(self._can_handle, read_cnt, MAX_RCV_NUM)
        else:
            can_msgs = None
        return can_msgs


    def isotp_send(self, isotp_msg):
        #isotp_msg.data.extend(bytearray([0xCC] * (8-len(isotp_msg.data))))
        msg = ZCAN_Transmit_Data()
        msg.transmit_type = 0 #正常发送
        msg.frame.can_id = isotp_msg.arbitration_id
        msg.frame.can_dlc = isotp_msg.dlc
        #msg.frame.can_dlc = 8

        for i in range(len(isotp_msg.data)):
            msg.frame.data[i] = isotp_msg.data[i]

        ret = self._zcan.Transmit(self._can_handle, msg, 1)
        if ret != 1:
            messagebox.showerror(title="发送报文", message="发送失败！")
        return


if __name__ == "__main__":
    demo = ZCAN_CCDiag()
    demo.mainloop()