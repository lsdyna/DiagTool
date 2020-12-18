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
import struct
from udsoncan.exceptions import TimeoutException
from zlgcan import *
import tkinter as tk
from tkinter import Frame, ttk
from tkinter import messagebox
import threading
import time
import json
import queue
import ctypes

import udsoncan
from udsoncan.connections import BaseConnection
from udsoncan import services, Response

import isotp
from functools import partial

GRPBOX_WIDTH    = 200
MSGCNT_WIDTH    = 50
MSGID_WIDTH     = 80
MSGDIR_WIDTH    = 60
MSGINFO_WIDTH   = 100
MSGLEN_WIDTH    = 60
MSGDATA_WIDTH   = 200
MSGVIEW_WIDTH   = MSGCNT_WIDTH + MSGID_WIDTH + MSGDIR_WIDTH + MSGINFO_WIDTH + MSGLEN_WIDTH + MSGDATA_WIDTH
MSGVIEW_HEIGHT  = 500
SENDVIEW_HEIGHT = 125

DIAG_HEIGHT = 500
DIAG_WIDTH = 500

WIDGHT_WIDTH    = GRPBOX_WIDTH + MSGVIEW_WIDTH + DIAG_WIDTH + 60
WIDGHT_HEIGHT   = MSGVIEW_HEIGHT + SENDVIEW_HEIGHT + 20


MAX_DISPLAY     = 1000
MAX_RCV_NUM     = 150

USBCANFD_TYPE    = (41, 42, 43)
USBCAN_XE_U_TYPE = (20, 21, 31)
USBCAN_I_II_TYPE = (3, 4)

ESC_TX_ID = 0x73D   #73E
ESC_RX_ID = 0x735   #736






###############################################################################
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

        #Transmit and receive count display
        self._tx_cnt = 0
        self._rx_cnt = 0
        self._view_cnt = 0

        #read can/canfd message thread
        self._read_thread = None
        self._terminated = False
        self._lock = threading.RLock()

        #period send var
        self._is_sending   = False
        self._id_increase  = False 
        self._send_num     = 1
        self._send_cnt     = 1
        self._is_canfd_msg = False
        self._send_msgs    = None
        self._send_thread  = None

        #cyclic received msgs and msgs num
        self._rcvd_msgs = []
        self._rcvd_msgs_num = 0
        self._udsqueue_rx = queue.Queue()
        self._udsbuffer_rx = bytearray()
        self._isotpaddr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=ESC_RX_ID, rxid = ESC_TX_ID)


    def WidgetsInit(self):
        self._dev_frame = tk.Frame(self)
        self._dev_frame.grid(row=0, column=0, padx=2, pady=2, sticky=tk.NW)

        # Device connect group
        self.gbDevConnect = tk.LabelFrame(self._dev_frame, height=100, width=GRPBOX_WIDTH, text="设备选择")
        self.gbDevConnect.grid_propagate(0)
        self.gbDevConnect.grid(row=0, column=0, padx=2, pady=2, sticky=tk.NE)
        self.DevConnectWidgetsInit()

        self.gbCANCfg = tk.LabelFrame(self._dev_frame, height=170, width=GRPBOX_WIDTH, text="通道配置")
        self.gbCANCfg.grid(row=1, column=0, padx=2, pady=2, sticky=tk.NSEW)
        self.gbCANCfg.grid_propagate(0)
        self.CANChnWidgetsInit()

        self.gbDevInfo = tk.LabelFrame(self._dev_frame, height=230, width=GRPBOX_WIDTH, text="设备信息")
        self.gbDevInfo.grid(row=2, column=0, padx=2, pady=2, sticky=tk.NSEW)
        self.gbDevInfo.grid_propagate(0)
        self.DevInfoWidgetsInit()

        self.gbMsgDisplay = tk.LabelFrame(height=MSGVIEW_HEIGHT, width=MSGVIEW_WIDTH + 12, text="报文显示")
        self.gbMsgDisplay.grid(row=0, column=1, padx=2, pady=2, sticky=tk.NSEW)
        self.gbMsgDisplay.grid_propagate(0)
        self.MsgDisplayWidgetsInit()

        self.gbMsgSend = tk.LabelFrame(heigh=SENDVIEW_HEIGHT, width=MSGVIEW_WIDTH + 12, text="报文发送")
        self.gbMsgSend.grid(row=2, column=1, padx=2, pady=2, sticky=tk.NSEW)
        self.gbMsgSend.grid_propagate(0)
        self.MsgSendWidgetsInit()

        self.gbDiag = tk.LabelFrame(height=DIAG_HEIGHT, width=DIAG_WIDTH + 12, text="诊断功能")
        self.gbDiag.grid(row=0, column=2, padx=2, pady=2, sticky=tk.NSEW)
        self.gbDiag.grid_propagate(0)
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

        #CAN Control
        self.strvCANCtrl = tk.StringVar()
        self.strvCANCtrl.set("打开")
        self.btnCANCtrl = ttk.Button(self.gbCANCfg, textvariable=self.strvCANCtrl, command=self.BtnOpenCAN_Click) 
        self.btnCANCtrl.grid(row=5, column=0, columnspan=2, padx=2, pady=2)

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
    
    def MsgDisplayWidgetsInit(self):
        self._msg_frame = tk.Frame(self.gbMsgDisplay, height=MSGVIEW_HEIGHT, width=WIDGHT_WIDTH-GRPBOX_WIDTH+10)
        self._msg_frame.pack(side=tk.TOP)
        
        self.treeMsg = ttk.Treeview(self._msg_frame, height=20, show="headings")
        self.treeMsg["columns"] = ("cnt", "id", "direction", "info", "len", "data")

        self.treeMsg.column("cnt",       anchor = tk.CENTER, width=MSGCNT_WIDTH)
        self.treeMsg.column("id",        anchor = tk.CENTER, width=MSGID_WIDTH)
        self.treeMsg.column("direction", anchor = tk.CENTER, width=MSGDIR_WIDTH)
        self.treeMsg.column("info",      anchor = tk.CENTER, width=MSGINFO_WIDTH)
        self.treeMsg.column("len",       anchor = tk.CENTER, width=MSGLEN_WIDTH)
        self.treeMsg.column("data", width=MSGDATA_WIDTH)

        self.treeMsg.heading("cnt", text="序号")
        self.treeMsg.heading("id", text="帧ID")
        self.treeMsg.heading("direction", text="方向")
        self.treeMsg.heading("info", text="帧信息")
        self.treeMsg.heading("len", text="长度")
        self.treeMsg.heading("data", text="数据")
        
        self.hbar = ttk.Scrollbar(self._msg_frame, orient=tk.HORIZONTAL, command=self.treeMsg.xview)
        self.hbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.vbar = ttk.Scrollbar(self._msg_frame, orient=tk.VERTICAL, command=self.treeMsg.yview)
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.treeMsg.configure(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)
        
        self.treeMsg.pack(side=tk.LEFT)
        self.treeMsg.selection_set()
        self.btnClrCnt = ttk.Button(self.gbMsgDisplay, width=10, text="清空", command=self.BtnClrCnt_Click) 
        self.btnClrCnt.pack(side=tk.RIGHT)

        self.strvRxCnt = tk.StringVar()
        self.strvRxCnt.set("0")
        tk.Label(self.gbMsgDisplay, anchor=tk.W, width=5, textvariable=self.strvRxCnt).pack(side=tk.RIGHT)
        tk.Label(self.gbMsgDisplay, width=10, text="接收帧数:").pack(side=tk.RIGHT)

        self.strvTxCnt = tk.StringVar()
        self.strvTxCnt.set("0")
        tk.Label(self.gbMsgDisplay, anchor=tk.W, width=5, textvariable=self.strvTxCnt).pack(side=tk.RIGHT)
        tk.Label(self.gbMsgDisplay, width=10, text="发送帧数:").pack(side=tk.RIGHT)

    def MsgSendWidgetsInit(self):
        #Send Type
        tk.Label(self.gbMsgSend, anchor=tk.W, text="发送方式:").grid(row=0, column=0, sticky=tk.W)
        self.cmbSendType = ttk.Combobox(self.gbMsgSend, width=8, state="readonly")
        self.cmbSendType.grid(row = 0, column=1, sticky=tk.W) 
        self.cmbSendType["value"] = ("正常发送", "单次发送", "自发自收")
        self.cmbSendType.current(0)

        #CAN Type
        tk.Label(self.gbMsgSend, anchor=tk.W, text="帧类型:").grid(row=0, column=2, sticky=tk.W)
        self.cmbMsgType = ttk.Combobox(self.gbMsgSend, width=6, state="readonly")
        self.cmbMsgType.grid(row = 0, column=3, sticky=tk.W) 
        self.cmbMsgType["value"] = ("标准帧", "扩展帧")
        self.cmbMsgType.current(0)

        #CAN Format 
        tk.Label(self.gbMsgSend, anchor=tk.W, text="帧格式:").grid(row=0, column=4, sticky=tk.W)
        self.cmbMsgFormat = ttk.Combobox(self.gbMsgSend, width=6, state="readonly") 
        self.cmbMsgFormat.grid(row = 0, column=5, sticky=tk.W) 
        self.cmbMsgFormat["value"] = ("数据帧", "远程帧")
        self.cmbMsgFormat.bind("<<ComboboxSelected>>", self.CmbMsgFormatUpdate)
        self.cmbMsgFormat.current(0)

        #CANFD 
        tk.Label(self.gbMsgSend, anchor=tk.W, text="CAN类型:").grid(row=0, column=6, sticky=tk.W)
        self.cmbMsgCANFD = ttk.Combobox(self.gbMsgSend, width=10, state="readonly")
        self.cmbMsgCANFD.grid(row=0, column=7, sticky=tk.W) 
        self.cmbMsgCANFD["value"] = ("CAN", "CANFD", "CANFD BRS")
        self.cmbMsgCANFD.bind("<<ComboboxSelected>>", self.CmbMsgCANFDUpdate)
        self.cmbMsgCANFD.current(0)

        #CAN ID
        tk.Label(self.gbMsgSend, anchor=tk.W, text="帧ID(hex):").grid(row=1, column=0, sticky=tk.W)
        self.entryMsgID = tk.Entry(self.gbMsgSend, width=10, text="100")
        self.entryMsgID.grid(row=1, column=1, sticky=tk.W) 
        self.entryMsgID.insert(0, "100")

        #CAN Length 
        tk.Label(self.gbMsgSend, anchor=tk.W, text="长度:").grid(row=1, column=2, sticky=tk.W)
        self.cmbMsgLen = ttk.Combobox(self.gbMsgSend, width=6, state="readonly")
        self.cmbMsgLen["value"] = tuple([i for i in range(9)])
        self.cmbMsgLen.current(8) 
        self.cmbMsgLen.grid(row=1, column=3, sticky=tk.W) 

        #Data
        tk.Label(self.gbMsgSend, anchor=tk.W, text="数据(hex):").grid(row=1, column=4, sticky=tk.W)
        self.entryMsgData = tk.Entry(self.gbMsgSend, width=30)
        self.entryMsgData.grid(row = 1, column=5, columnspan=4, sticky=tk.W) 
        self.entryMsgData.insert(0, "00 01 02 03 04 05 06 07")

        #send frame number
        tk.Label(self.gbMsgSend, anchor=tk.W, text="发送帧数:").grid(row=2, column=0, sticky=tk.W)
        self.entryMsgNum = tk.Entry(self.gbMsgSend, width=10)
        self.entryMsgNum.grid(row=2, column=1, sticky=tk.W) 
        self.entryMsgNum.insert(0, "1")

        #send frame cnt 
        tk.Label(self.gbMsgSend, anchor=tk.W, text="发送次数:").grid(row=2, column=2, sticky=tk.W)
        self.entryMsgCnt = tk.Entry(self.gbMsgSend, width=8)
        self.entryMsgCnt.grid(row=2, column=3, sticky=tk.W) 
        self.entryMsgCnt.insert(0, "1")

        #send frame period
        tk.Label(self.gbMsgSend, anchor=tk.W, text="发送间隔(ms):").grid(row=2, column=4, sticky=tk.W)
        self.entryMsgPeriod = tk.Entry(self.gbMsgSend, width=8)
        self.entryMsgPeriod.grid(row=2, column=5, sticky=tk.W) 
        self.entryMsgPeriod.insert(0, "0")

        #msg id add
        self.varIDInc = tk.IntVar()
        self.chkbtnIDInc = tk.Checkbutton(self.gbMsgSend, text="ID递增", variable=self.varIDInc)
        self.chkbtnIDInc.grid(row=2, column=6, columnspan=2, sticky=tk.W)

        #Send Butten
        self.strvSend = tk.StringVar()
        self.strvSend.set("发送")
        self.btnMsgSend = ttk.Button(self.gbMsgSend, textvariable=self.strvSend, command=self.BtnSendMsg_Click) 
        self.btnMsgSend.grid(row=3, column=7, padx=2, pady=2)
        self.btnMsgSend["state"] = tk.DISABLED

    def DiagWidgetsInit(self):
        #Read DTC
        self.strvReadDTC = tk.StringVar()
        self.strvReadDTC.set("读取故障码")
        self.btnReadDTC = ttk.Button(self.gbDiag, textvariable=self.strvReadDTC, command=self.BtnReadDTC_Click) 
        self.btnReadDTC.grid(row=0, column=0, padx=3, pady=3)
        self.btnReadDTC["state"] = tk.DISABLED






        #Reset ECU
        self.strvResetECU = tk.StringVar()
        self.strvResetECU.set("重启ECU")
        self.btnResetECU = ttk.Button(self.gbDiag, textvariable=self.strvResetECU, command=self.BtnResetECU_Click) 
        self.btnResetECU.grid(row=9, column=0, padx=3, pady=3)
        self.btnResetECU["state"] = tk.DISABLED



###############################################################################
### Function 
###############################################################################
    def __dlc2len(self, dlc):
        if dlc <= 8:
            return dlc
        elif dlc == 9:
            return 12
        elif dlc == 10:
            return 16
        elif dlc == 11:
            return 20
        elif dlc == 12:
            return 24
        elif dlc == 13:
            return 32
        elif dlc == 14:
            return 48
        else:
            return 64

    def CANMsg2View(self, msg, is_transmit=True):
        view = []
        view.append(str(self._view_cnt))
        self._view_cnt += 1 
        view.append(hex(msg.can_id)[2:])
        view.append("发送" if is_transmit else "接收")

        str_info = ''
        str_info += 'EXT' if msg.eff else 'STD'
        if msg.rtr:
            str_info += ' RTR'
        view.append(str_info)
        view.append(str(msg.can_dlc))
        if msg.rtr:
            view.append('')
        else:
            view.append(''.join(hex(msg.data[i])[2:] + ' ' for i in range(msg.can_dlc)))
        return view

    def CANFDMsg2View(self, msg, is_transmit=True):
        view = [] 
        view.append(str(self._view_cnt))
        self._view_cnt += 1 
        
        view.append(hex(msg.can_id)[2:])
        view.append("发送" if is_transmit else "接收")

        str_info = ''
        str_info += 'EXT' if msg.eff else 'STD'
        if msg.rtr:
            str_info += ' RTR'
        else:
            str_info += ' FD'
            if msg.brs:
                str_info += ' BRS'
            if msg.esi:
                str_info += ' ESI' 
        view.append(str_info)
        view.append(str(msg.len))
        if msg.rtr:
            view.append('')
        else:
            view.append(''.join(hex(msg.data[i])[2:] + ' ' for i in range(msg.len)))
        return view

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

            self.btnCANCtrl["state"] = tk.NORMAL
        else:
            self.cmbCANChn["state"] = tk.DISABLED
            self.cmbCANMode["state"] = tk.DISABLED
            self.cmbBaudrate["state"] = tk.DISABLED
            self.cmbDataBaudrate["state"] = tk.DISABLED
            self.cmbResEnable["state"] = tk.DISABLED
            
            self.cmbCANChn["value"] = ()
            self.cmbCANMode["value"] = ()
            self.cmbBaudrate["value"] = ()
            self.cmbDataBaudrate["value"] = ()
            self.cmbResEnable["value"] = ()

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
        else:
            self.cmbCANChn["state"] = tk.DISABLED
            self.cmbCANMode["state"] = tk.DISABLED
            self.cmbBaudrate["state"] = tk.DISABLED
            self.cmbDataBaudrate["state"] = tk.DISABLED
            self.cmbResEnable["state"] = tk.DISABLED
    
    def MsgReadThreadFunc(self):
        try:
            while not self._terminated:
                can_num = self._zcan.GetReceiveNum(self._can_handle, ZCAN_TYPE_CAN)
                canfd_num = self._zcan.GetReceiveNum(self._can_handle, ZCAN_TYPE_CANFD)
                if not can_num and not canfd_num:
                    time.sleep(0.005) #wait 5ms  
                    continue

                if can_num:
                    while can_num and not self._terminated:
                        read_cnt = MAX_RCV_NUM if can_num >= MAX_RCV_NUM else can_num
                        can_msgs, act_num = self._zcan.Receive(self._can_handle, read_cnt, MAX_RCV_NUM)
                        #print(type(can_msgs))
                        if act_num:
                            #update data
                            #self._rx_cnt += act_num 
                            #self.strvRxCnt.set(str(self._rx_cnt))
                            #self.ViewDataUpdate(can_msgs, act_num, False, False)
                            for msg in can_msgs:
                                if msg.frame.can_id == ESC_TX_ID:
                                    self._rcvd_msgs.append(msg)
                                    self._rcvd_msgs_num += 1
                                    self._udsqueue_rx.put(msg.frame.data)
                                #self._udsbuffer_rx = bytes(msg.frame.data)
                            #print('收到的queue的size是：',self._udsqueue_rx.qsize())
                            #self._udsqueue_rx.join()
                            #print('收到的CAN报文的数目是：',self._rcvd_msgs_num)
                        else:
                            break
                        can_num -= act_num

                if canfd_num:
                    while canfd_num and not self._terminated:
                        read_cnt = MAX_RCV_NUM if canfd_num >= MAX_RCV_NUM else canfd_num
                        canfd_msgs, act_num = self._zcan.ReceiveFD(self._can_handle, read_cnt, MAX_RCV_NUM)
                        if act_num: 
                            #update data
                            self._rx_cnt += act_num 
                            self.strvRxCnt.set(str(self._rx_cnt))
                            self.ViewDataUpdate(canfd_msgs, act_num, True, False)
                        else:
                            break
                        canfd_num -= act_num
        except:
            print("Error occurred while read CAN(FD) data!")

    def ViewDataUpdate(self, msgs, msgs_num, is_canfd=False, is_send=True):
        with self._lock:
            if is_canfd:
                for i in range(msgs_num):
                    if len(self.treeMsg.get_children()) == MAX_DISPLAY:
                        self.treeMsg.delete(self.treeMsg.get_children()[0])
                    self.treeMsg.insert('', 'end', values=self.CANFDMsg2View(msgs[i].frame, is_send))
                    #focus section
                    child_id = self.treeMsg.get_children()[-1]
                    self.treeMsg.focus(child_id)
                    self.treeMsg.selection_set(child_id)
            else:
                for i in range(msgs_num):
                    if msgs[i].frame.can_id == ESC_TX_ID or msgs[i].frame.can_id ==  ESC_RX_ID:
                        if len(self.treeMsg.get_children()) == MAX_DISPLAY:
                            self.treeMsg.delete(self.treeMsg.get_children()[0])
                        self.treeMsg.insert('', 'end', values=self.CANMsg2View(msgs[i].frame, is_send))
                        #focus section
                        child_id = self.treeMsg.get_children()[-1]
                        self.treeMsg.focus(child_id)
                        self.treeMsg.selection_set(child_id)

    def PeriodSendIdUpdate(self, is_ext):
        self._cur_id += 1
        if is_ext:
            if self._cur_id > 0x1FFFFFFF:
                self._cur_id = 0
        else:
            if self._cur_id > 0x7FF:
                self._cur_id = 0

    def PeriodSendComplete(self):
        self._is_sending = False
        self.strvSend.set("发送")
        self._send_thread.send_stop()

    def PeriodSend(self):
        if self._is_canfd_msg: 
            ret = self._zcan.TransmitFD(self._can_handle, self._send_msgs, self._send_num)
        else:
            ret = self._zcan.Transmit(self._can_handle, self._send_msgs, self._send_num)
        
        #update transmit display
        #self._tx_cnt += ret
        #self.strvTxCnt.set(str(self._tx_cnt))
        #self.ViewDataUpdate(self._send_msgs, ret, self._is_canfd_msg, True)
        
        if ret != self._send_num:
            self.PeriodSendComplete()
            messagebox.showerror(title="发送报文", message="发送失败！")
            return

        self._send_cnt -= 1
        if self._send_cnt:
            if self._id_increase:
                for i in range(self._send_num):
                    self._send_msgs[i].frame.can_id = self._cur_id
                    self.PeriodSendIdUpdate(self._send_msgs[i].frame.eff)
        else:
            self.PeriodSendComplete()

    def MsgSend(self, msg, is_canfd, num=1, cnt=1, period=0, id_increase=0):
        self._id_increase  = id_increase
        self._send_num     = num if num else 1
        self._send_cnt     = cnt if cnt else 1
        self._is_canfd_msg = is_canfd

        if is_canfd:    
            self._send_msgs = (ZCAN_TransmitFD_Data * self._send_num)()
        else:
            self._send_msgs = (ZCAN_Transmit_Data * self._send_num)()

        self._cur_id = msg.frame.can_id
        for i in range(self._send_num):
            self._send_msgs[i] = msg
            self._send_msgs[i].frame.can_id = self._cur_id
            self.PeriodSendIdUpdate(self._send_msgs[i].frame.eff)

        self._is_sending = True    
        self._send_thread.send_start(period * 0.001)
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
            if self._is_canfd:
                self.cmbMsgCANFD["value"] = ("CAN", "CANFD", "CANFD BRS")
            else:
                self.cmbMsgCANFD["value"] = ("CAN")

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
            self._read_thread.join(0.1)
            self.empty_udsqueue_rx()

            #stop send thread
            self._send_thread.stop()

            #cancel send
            if self._is_sending:
                self.btnMsgSend.invoke()

            #Close channel
            self._zcan.ResetCAN(self._can_handle)
            self.strvCANCtrl.set("打开")
            self._isChnOpen = False
            self.btnMsgSend["state"] = tk.DISABLED
        else:
            #Initial channel
            self.empty_udsqueue_rx()
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

            #start send thread
            self._send_thread = PeriodSendThread(self.PeriodSend)
            self._send_thread.start()

            #start receive thread
            self._terminated = False
            self._read_thread = threading.Thread(None, target=self.MsgReadThreadFunc)
            self._read_thread.start()

            self.strvCANCtrl.set("关闭")
            self._isChnOpen = True 
            self.btnMsgSend["state"] = tk.NORMAL
            self.btnReadDTC["state"] = tk.NORMAL
            self.btnResetECU["state"] = tk.NORMAL
        self.ChnInfoDisplay(not self._isChnOpen)

    def BtnClrCnt_Click(self):
        self._tx_cnt = 0
        self._rx_cnt = 0
        self._view_cnt = 0
        self.strvTxCnt.set("0")
        self.strvRxCnt.set("0")
        # self.treeMsg
        for item in self.treeMsg.get_children():
            self.treeMsg.delete(item)

    def CmbMsgFormatUpdate(self, *args):
        if self.cmbMsgFormat.current() == 0: #Data Frame
            if self._is_canfd:
                self.cmbMsgCANFD["value"] = ("CAN", "CANFD", "CANFD BRS")
            else:
                self.cmbMsgCANFD["value"] = ("CAN")
                self.cmbMsgCANFD.current(0)
        else: #Remote Frame
            self.cmbMsgCANFD["value"] = ("CAN")
            self.cmbMsgCANFD.current(0)

    def CmbMsgCANFDUpdate(self, *args):
        tmp = self.cmbMsgLen.current()
        self.cmbMsgLen["value"] = tuple([self.__dlc2len(i) for i in range(16 if self.cmbMsgCANFD.current() else 9)]) 
        if tmp >= len(self.cmbMsgLen["value"]):
            self.cmbMsgLen.current(len(self.cmbMsgLen["value"]) - 1)            

    def BtnSendMsg_Click(self): 
        if not self._is_sending:
            is_canfd_msg = True if self.cmbMsgCANFD.current() > 0 else False
            if is_canfd_msg:
                msg = ZCAN_TransmitFD_Data()
            else:
                msg = ZCAN_Transmit_Data()

            msg.transmit_type = self.cmbSendType.current()
            try:
                msg.frame.can_id = int(self.entryMsgID.get(), 16)
            except:
                msg.frame.can_id = 0
            msg.frame.rtr = self.cmbMsgFormat.current()
            msg.frame.eff = self.cmbMsgType.current()

            if not is_canfd_msg:
                msg.frame.can_dlc = self.cmbMsgLen.current()
                msg_len = msg.frame.can_dlc
            else:
                msg.frame.brs = 1 if self.cmbMsgCANFD.current() == 2 else 0
                msg.frame.len = self.__dlc2len(self.cmbMsgLen.current())
                msg_len = msg.frame.len

            data = self.entryMsgData.get().split(' ')
            for i in range(msg_len):
                if i < len(data):
                    try:
                        msg.frame.data[i] = int(data[i], 16)
                    except:
                        msg.frame.data[i] = 0
                else:
                    msg.frame.data[i] = 0

            try:
                msg_num = int(self.entryMsgNum.get())
                msg_cnt = int(self.entryMsgCnt.get())
                period  = int(self.entryMsgPeriod.get())
            except:
                msg_num = 1
                msg_cnt = 1 
                period  = 1
            self.MsgSend(msg, is_canfd_msg, msg_num, msg_cnt, period, self.varIDInc.get())
            #self._tx_cnt += msg_cnt
            #self.strvTxCnt.set(str(self._tx_cnt))
            #self.ViewDataUpdate(self._send_msgs, msg_cnt, self._is_canfd_msg, True)
            self.strvSend.set("停止发送")
        else:
            self.PeriodSendComplete()

    def BtnReadDTC_Click(self):
        req = services.ReadDTCInformation.make_request(subfunction= 2, status_mask= 1)
        #print('从req解析出来要发送的报文是' ,req.get_payload(), time.time())
        self.UDS_send(req.get_payload())
        payload = self.UDS_rcv(1)

        print('UDS_rcv返回的是：', payload, time.time())
        #print(self._udsqueue_rx.qsize())
        response = Response.from_payload(payload)
        services.ReadDTCInformation.interpret_response(response, subfunction=2)

    def BtnClearDTC_Click(self):
        pass


    def BtnResetECU_Click(self):
        req = services.ECUReset.make_request(reset_type=1)
        #print(req.get_payload())
        self.UDS_send(req.get_payload())
        payload = self.UDS_rcv(1)
        #print(self._udsqueue_rx.qsize())
        #print(payload)
        response = Response.from_payload(payload)
        if response.service == services.ECUReset and response.code == Response.Code.PositiveResponse and response.service_data.reset_type_echo == 1:
            messagebox.showerror(title="Reset ECU", message="重启ECU成功!")
            return
        else:
            messagebox.showerror(title="Reset ECU", message="重启ECU失败!")
            return
###############################################################################
### udsoncan interface
###############################################################################
        """
        udsoncan interface
        """
    def UDS_rcv(self, timeout_max):
        timeout = False
        frame = None
        endtime = time.time() + timeout_max
        #print('while前的时间是：',time.time())
        while (endtime -time.time()) > 0:
            try:
                #print('取值前queue的size是：',self._udsqueue_rx.qsize())
                frame = self._udsqueue_rx.get(block = False, timeout = timeout_max)
                #print('queue里面get到的frame是' ,bytes(frame))
                #print('取完值后queue的size是：',self._udsqueue_rx.qsize())
                #print(type(frame))
                timeout = False
                break
            except queue.Empty:
                timeout = True
        #print('while后的时间是：',time.time())

        if timeout:
            messagebox.showerror(title="Response", message="响应超时!")
            return
        self._rx_cnt += self._rcvd_msgs_num 
        self.strvRxCnt.set(str(self._rx_cnt))
        self.ViewDataUpdate(self._rcvd_msgs, self._rcvd_msgs_num, False, False)
        self._rcvd_msgs = []
        self._rcvd_msgs_num = 0

        return bytes(frame)

    def UDS_send(self, payload):
        if not self._is_sending:
            msg = ZCAN_Transmit_Data()

            msg.transmit_type = 1 #单次发送
            msg.frame.can_id = ESC_RX_ID
            msg.frame.rtr = 0
            msg.frame.eff = 0
            #msg.frame.brs = 0
            msg.frame.can_dlc = 8
            #print(len(payload[0]))
            n = len(payload)
            data = struct.unpack("B"*n, payload)
            msg.frame.data[0] = n
            for i in range(n):
                msg.frame.data[i+1] = data[i]
            
            msg_num = 1
            msg_cnt = 1
            period  = 1
            self.MsgSend(msg, False, msg_num, msg_cnt, period, 0)
            #update transmit display
            self._tx_cnt += msg_cnt
            self.strvTxCnt.set(str(self._tx_cnt))
            self.ViewDataUpdate(self._send_msgs, msg_cnt, self._is_canfd_msg, True)
        else:
            self.PeriodSendComplete()

    def empty_udsqueue_rx(self):
        while not self._udsqueue_rx.empty():
            self._udsqueue_rx.get()


if __name__ == "__main__":
    demo = ZCAN_CCDiag()
    demo.mainloop()