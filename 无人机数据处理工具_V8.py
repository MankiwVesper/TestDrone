# -*- encoding: utf-8 -*-

"""
名称：无人机以太网报文提取工具(Windows平台)
目的：在项目中，软件会包含接收并显示无人机视频和飞控信息的功能。而在测试过程中通常需要给软件发送视频数据和飞控数据；
         该工具就是从抓取的原始数据（通过 wireshark、tcpdump等工具抓取到的原始以太网数据——pcapng/pcap格式）中提取中无人机视频数据和飞控数据
版本：V8.0
更新：继续完善部分功能
作者：mankiw
时间：2023/6/7~

TODO：
            2. 给对于的QLineEdit 添加提示功能，鼠标放上去之后，会有弹窗提示QLineEdit中的完整内容
            3. 将UI界面相关的代码分离到一个独立的Python脚本中
            4. 新增功能：在发送数据的时候，由用户选择是否将发送的报文打印到控制台和保存到日志文件中
            5. 新增功能：日志文件的分段存储功能
            6. 新增功能：可以同时提取多个数据或者发送多个数据，通过线程池来实现，同时在界面上增加对于的显示窗口，显示各个发送数据和提取数据的记录

FIXME：1. 连续点击窗口左上角图标处，软件会崩溃闪退

DONE:

"""

import datetime
import os
import sys
import socket
import time
import re
from subprocess import Popen, PIPE

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QIntValidator, QIcon, QKeySequence
from PyQt5.QtWidgets import QMessageBox, QDesktopWidget, QWidget, QPushButton, QRadioButton, QFileDialog, QApplication

from extract import Extractor, extract_logger, ExtractParam
from send import Sender, send_logger, SendParam


def has_chinese(text):
    """
    Func: 检查文本中是否包含中文
    :param text: 待检查的文本
    :return: Boolean
    """
    pattern = re.compile(r'[\u4e00-\u9fff]+')
    result = pattern.search(text)
    return True if result else False


def get_all_host_ip():
    """
    Func: 获取本机的所有IPv4地址
    :return: 一个列表，保存本机上所有的IPv4地址
    """
    hostname = socket.gethostname()
    if has_chinese(hostname):
        return
    address = socket.getaddrinfo(hostname, None, family=2)

    return [item[4][0] for item in address]


def check_ip_port_used(ip, port):
    """
    Func：检查本机的IP地址和端口能否使用
    :param: ip 字符串，指定的IP地址
    :param: port int类型，指定的端口号
    :return: Boolean 端口可以使用，返回 Ture；否则返回错误提示信息
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ip, port))
        return True
    except PermissionError as e:
        send_logger.critical(e)
        return e.__str__()
    except OSError as e:
        send_logger.critical(e)
        return e.__str__()
    except Exception as e:
        send_logger.critical(e)
        send_logger.critical('创建socket或绑定端口出现错误，请检查！')
        return e.__str__()
    finally:
        s.close()


def attach_time_tag(file):
    """
    Func: 给文件重命名，增加时间戳，不改变原文件的后缀名
    :param file: 需要增加时间戳重命名的文件
    :return: new_file: 增加时间戳之后的文件名
    """
    path_info = os.path.splitext(file)
    time_tag = datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S-%f')
    new_file = ''.join([''.join([path_info[0], '_', time_tag]), path_info[1]])
    return new_file


def extract_stop():
    ExtractParam.extract_flag = False


def send_stop():
    SendParam.send_flag = False


class ExtractCountTimeThread(QThread):
    """
    Func: 用来给提取数据的子线程计算时间
    """
    counting = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.count = 1
        self._isRunning = True

    def run(self) -> None:
        if not self._isRunning:
            self._isRunning = True

        while self.count and self._isRunning:
            self.counting.emit(str(self.count))
            time.sleep(1)
            self.count += 1

    def stop(self):
        """
        Func: 停止计时
        """
        self._isRunning = False             # 停止运行计时函数的标志位
        self.count = 0                          # 恢复计时的初始值为 0


class SendCountTimeThread(QThread):
    """
    Func: 用来给发送数据的子线程计算时间
    """
    counting = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.count = 1
        self._isRunning = True

    def run(self) -> None:
        if not self._isRunning:
            self._isRunning = True

        while self.count and self._isRunning:
            self.counting.emit(str(self.count))
            time.sleep(1)
            self.count += 1

    def stop(self):
        """
        Func: 停止计时
        """
        self._isRunning = False             # 停止运行计时函数的标志位
        self.count = 0                          # 恢复计时的初始值为 0


class ExtractThread(QObject):
    """
    Func: 提取数据的子线程
    """
    extract_start = pyqtSignal()
    extract_finished = pyqtSignal()
    update_tip = pyqtSignal(str, str)                                           # 更新提示信息的信号
    update_messagebox = pyqtSignal(str, str)                            # 更新消息弹窗的信号
    open_file_location = pyqtSignal()                                         # 打开文件位置的信号

    def __init__(self, tool, raw_data, neat_data, data_range):
        super().__init__()
        self.tool = tool
        self.raw_data = raw_data
        self.neat_data = neat_data
        self.data_range = data_range

    def start_extract(self):
        extract_logger.info('开始提取数据 ... ')
        self.update_tip.emit('LightSeaGreen', '开始提取数据 ... ')
        self.extract_start.emit()

        # 开始提取
        extractor = Extractor(self.tool, self.raw_data, self.data_range)
        extractor.extract_data(self.neat_data)

        # 提取结束，发送信号
        self.extract_finished.emit()

        if ExtractParam.code == 1:
            extract_logger.warning('数据提取停止！')
            self.update_tip.emit('OrangeRed', '数据提取停止！')
            return
        elif ExtractParam.code is None:
            extract_logger.critical('数据提取失败！ ')
            self.update_tip.emit('Red', '数据提取失败！')
            self.update_messagebox.emit('错误', '数据提取失败！')
            return
        elif ExtractParam.code == 0:
            if os.path.exists(self.neat_data):
                extract_logger.info(f'提取数据完成。')
                self.update_tip.emit('LightSeaGreen', '提取数据完成。')
                self.update_messagebox.emit('提示', '数据提取完成。')
            else:
                extract_logger.error('数据文件不存在！')
                self.update_tip.emit('Red', '数据文件不存在！')
                self.update_messagebox.emit('错误', '数据文件不存在！')
        else:
            extract_logger.error('数据提取出现异常！')
            self.update_tip.emit('Red', '数据提取出现异常！')
            self.update_messagebox.emit('错误', '数据提取出现异常！')


class SendThread(QObject):
    update_tip = pyqtSignal(str, str)                       # 更新提示信息的信号
    update_messagebox = pyqtSignal(str, str)        # 更新消息弹窗的信号
    send_start = pyqtSignal()
    send_finished = pyqtSignal()

    def __init__(self, src_address, dst_address, delta_time, data_file, times=0):
        super().__init__()
        self.src_address = src_address
        self.dst_address = dst_address
        self.delta_time = delta_time
        self.data_file = data_file
        self.times = times

    def start_send(self):
        send_logger.info('开始处理数据 ... ')
        self.update_tip.emit('LightSeaGreen', '开始处理数据 ... ')

        sender = Sender(self.src_address, self.dst_address, self.delta_time)
        data_list = sender.get_command_from_file(self.data_file)

        if len(data_list) == 0:
            send_logger.warning('指定的数据文件中没有有效数据！ ')
            self.update_tip.emit('OrangeRed', '指定的数据文件中没有有效数据！')
            self.update_messagebox.emit('警告', '指定的数据文件中没有有效数据！')
            return

        num = "无限" if self.times == 0 else self.times
        send_logger.info(f'开始发送数据，循环发送{num}次 ... ')
        self.update_tip.emit('LightSeaGreen', f'正在发送数据，循环发送{num}次 ... ')
        self.send_start.emit()

        ret = sender.send_command(data_list, self.times)
        self.send_finished.emit()

        if isinstance(ret, str):
            if '10048' in ret:
                send_logger.warning(f'指定的端口{self.src_address[1]}正在被使用，请更换其他端口或者等待当前数据发送完成！')
                self.update_tip.emit('OrangeRed', f'指定的端口{self.src_address[1]}正在被使用，请更换其他端口！')
                self.update_messagebox.emit('警告', f'指定的端口{self.src_address[1]}正在被使用，请更换其他端口或者等待当前数据发送完成！')
            elif '10013' in ret:
                send_logger.warning(f'指定的端口{self.src_address[1]}没有使用权限，请更换其他端口！')
                self.update_tip.emit('OrangeRed', f'指定的端口{self.src_address[1]}没有使用权限，请更换其他端口！')
                self.update_messagebox.emit('警告', f'指定的端口{self.src_address[1]}没有使用权限，请更换其他端口！')
            elif 'Error' in ret:
                send_logger.error(f'指定的IP地址或端口{self.src_address}不能使用，请更换其他端口！')
                self.update_tip.emit('Red', f'指定的IP地址或端口{self.src_address}不能使用，请更换其他端口！')
                self.update_messagebox.emit('错误', f'指定的IP地址或端口{self.src_address}不能使用，请更换其他端口！')
            elif 'break' == ret:
                send_logger.warning('停止发送数据。')
                self.update_tip.emit('OrangeRed', '停止发送数据。')
                # self.update_messagebox.emit('警告', '停止发送数据。')
            else:
                send_logger.critical(f'{ret}')
                self.update_tip.emit('Red', ret)
                self.update_messagebox.emit('错误', ret)
        elif ret is True:
            send_logger.info('数据发送完成。 ')
            self.update_tip.emit('LightSeaGreen', '数据发送完成。')


class DroneDataProcessor(QWidget):
    """
    Func: 筛选所需要的数据，同时可以以UDP的方式发送报文到指定的IP地址和端口
    """
    def __init__(self):
        super(DroneDataProcessor, self).__init__(parent=None)
        self.extract_timecount = ExtractCountTimeThread()
        self.send_timecount = SendCountTimeThread()
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("无人机数据处理工具 - V8.0")
        self.setWindowIcon(QIcon('../ico/Eagle.ico'))
        self.setFixedSize(720, 960)  # 设置窗口的固定大小，不能拉伸

        # 在屏幕中间显示窗口
        screen = QDesktopWidget().screenGeometry()             # 获取屏幕的坐标
        size = self.geometry()                                                  # 获取窗口坐标系
        # 计算窗口居中情况下的窗口的坐标
        new_left = (screen.width() - size.width()) // 2
        new_top = (screen.height() - size.height()) // 2 - 20      # 防止系统下面的状态栏遮挡，往上移动20
        self.move(new_left, new_top)

        # 提取数据的部分
        self.groupBox = QtWidgets.QGroupBox(self)
        self.groupBox.setGeometry(QtCore.QRect(30, 30, 660, 460))
        font = QtGui.QFont()
        font.setFamily("Microsoft YaHei UI")
        font.setPointSize(12)
        font.setWeight(75)
        self.groupBox.setFont(font)
        self.groupBox.setObjectName("groupBox")
        self.groupBox.setStyleSheet('''QGroupBox{background-color:#F8F8FF;}''')

        # 提取工具
        self.label_0 = QtWidgets.QLabel(self.groupBox)
        self.label_0.setGeometry(QtCore.QRect(40, 10, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_0.setFont(font)
        self.label_0.setObjectName("label_0")
        self.label_0.setText("提取工具:")

        self.label_00 = QtWidgets.QLabel(self.groupBox)
        self.label_00.setGeometry(QtCore.QRect(40, 40, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_00.setFont(font)
        self.label_00.setObjectName("label_00")
        self.label_00.setText("tshark路径:")

        self.lineEdit_0 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_0.setGeometry(QtCore.QRect(150, 40, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_0.setFont(font)
        self.lineEdit_0.setObjectName("lineEdit_0")
        self.lineEdit_0.setText('C:/Program Files/Wireshark/tshark.exe')
        self.lineEdit_0.setClearButtonEnabled(True)

        self.button_set_tshark = QPushButton(self.groupBox)
        self.button_set_tshark.setGeometry(QtCore.QRect(520, 40, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.button_set_tshark.setFont(font)
        self.button_set_tshark.setObjectName("button_set_tshark")
        self.button_set_tshark.setText("选择工具")
        self.button_set_tshark.clicked.connect(self.set_tshark)

        # 分隔条
        self.line_0 = QtWidgets.QFrame(self.groupBox)
        self.line_0.setGeometry(QtCore.QRect(40, 75, 580, 10))
        self.line_0.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_0.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_0.setObjectName("line_0")

        # 数据信息
        self.label_1 = QtWidgets.QLabel(self.groupBox)
        self.label_1.setGeometry(QtCore.QRect(40, 95, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_1.setFont(font)
        self.label_1.setObjectName("label_1")
        self.label_1.setText("数据信息:")

        self.label_2 = QtWidgets.QLabel(self.groupBox)
        self.label_2.setGeometry(QtCore.QRect(40, 125, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_2.setFont(font)
        self.label_2.setObjectName("label_2")
        self.label_2.setText("原始数据文件:")

        self.lineEdit_1 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_1.setGeometry(QtCore.QRect(150, 125, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_1.setFont(font)
        self.lineEdit_1.setObjectName("lineEdit_1")
        self.lineEdit_1.setClearButtonEnabled(True)

        self.button_loadfile = QPushButton(self.groupBox)
        self.button_loadfile.setGeometry(QtCore.QRect(520, 125, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.button_loadfile.setFont(font)
        self.button_loadfile.setObjectName("label_loadfile")
        self.button_loadfile.setText("选择文件")
        self.button_loadfile.clicked.connect(self.extract_raw_data_load)

        self.label_3 = QtWidgets.QLabel(self.groupBox)
        self.label_3.setGeometry(QtCore.QRect(40, 155, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_3.setFont(font)
        self.label_3.setObjectName("label_3")
        self.label_3.setText("输出数据文件:")

        self.lineEdit_2 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_2.setGeometry(QtCore.QRect(150, 155, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_2.setFont(font)
        self.lineEdit_2.setObjectName("lineEdit_2")
        self.lineEdit_2.setClearButtonEnabled(True)

        self.button_savefile = QPushButton(self.groupBox)
        self.button_savefile.setGeometry(QtCore.QRect(520, 155, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.button_savefile.setFont(font)
        self.button_savefile.setObjectName("button_savefile")
        self.button_savefile.setText("选择路径")
        self.button_savefile.clicked.connect(self.extract_neat_data_save)

        # 分隔条
        self.line_1 = QtWidgets.QFrame(self.groupBox)
        self.line_1.setGeometry(QtCore.QRect(40, 190, 580, 10))
        self.line_1.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_1.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_1.setObjectName("line_1")

        # 提取类型
        self.label_4 = QtWidgets.QLabel(self.groupBox)
        self.label_4.setGeometry(QtCore.QRect(40, 215, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_4.setFont(font)
        self.label_4.setObjectName("label_4")
        self.label_4.setText("数据类型:")

        self.radioButton_1 = QRadioButton(self.groupBox)
        self.radioButton_1.setGeometry(QtCore.QRect(150, 215, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_1.setFont(font)
        self.radioButton_1.setObjectName("radioButton_1")
        self.radioButton_1.setText("视频数据:")
        self.radioButton_1.clicked.connect(self.extract_video_data_ip_set)

        self.radioButton_2 = QRadioButton(self.groupBox)
        self.radioButton_2.setGeometry(QtCore.QRect(285, 215, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_2.setFont(font)
        self.radioButton_2.setObjectName("radioButton_2")
        self.radioButton_2.setText("飞控数据:")
        self.radioButton_2.clicked.connect(self.extract_fc_data_ip_set)

        self.radioButton_3 = QRadioButton(self.groupBox)
        self.radioButton_3.setGeometry(QtCore.QRect(420, 215, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_3.setFont(font)
        self.radioButton_3.setObjectName("radioButton_3")
        self.radioButton_3.setText("其他数据:")
        self.radioButton_3.setChecked(True)
        self.radioButton_3.clicked.connect(self.extract_other_data_ip_set)

        # 提取范围
        self.label_5 = QtWidgets.QLabel(self.groupBox)
        self.label_5.setGeometry(QtCore.QRect(40, 260, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_5.setFont(font)
        self.label_5.setObjectName("label_5")
        self.label_5.setText("提取范围:")

        self.label_6 = QtWidgets.QLabel(self.groupBox)
        self.label_6.setGeometry(QtCore.QRect(40, 295, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_6.setFont(font)
        self.label_6.setObjectName("label_6")
        self.label_6.setText("源IP地址：")

        self.lineEdit_3 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_3.setGeometry(QtCore.QRect(150, 295, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_3.setFont(font)
        self.lineEdit_3.setObjectName("lineEdit_3")
        self.lineEdit_3.setInputMask('000.000.000.000')

        self.label_7 = QtWidgets.QLabel(self.groupBox)
        self.label_7.setGeometry(QtCore.QRect(300, 295, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_7.setFont(font)
        self.label_7.setObjectName("label_7")
        self.label_7.setText("源端口：")

        self.lineEdit_4 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_4.setGeometry(QtCore.QRect(380, 295, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_4.setFont(font)
        self.lineEdit_4.setObjectName("lineEdit_4")
        self.lineEdit_4.setClearButtonEnabled(True)
        port_validator = QIntValidator(self)
        port_validator.setRange(0, 65535)
        self.lineEdit_4.setValidator(port_validator)

        self.label_8 = QtWidgets.QLabel(self.groupBox)
        self.label_8.setGeometry(QtCore.QRect(40, 330, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_8.setFont(font)
        self.label_8.setObjectName("label_8")
        self.label_8.setText("目的IP地址：")

        self.lineEdit_5 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_5.setGeometry(QtCore.QRect(150, 330, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_5.setFont(font)
        self.lineEdit_5.setObjectName("lineEdit_5")
        self.lineEdit_5.setInputMask('000.000.000.000')

        self.label_9 = QtWidgets.QLabel(self.groupBox)
        self.label_9.setGeometry(QtCore.QRect(300, 330, 150, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_9.setFont(font)
        self.label_9.setObjectName("label_9")
        self.label_9.setText("目的端口：")

        self.lineEdit_6 = QtWidgets.QLineEdit(self.groupBox)
        self.lineEdit_6.setGeometry(QtCore.QRect(380, 330, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_6.setFont(font)
        self.lineEdit_6.setObjectName("lineEdit_6")
        self.lineEdit_6.setClearButtonEnabled(True)
        self.lineEdit_6.setValidator(port_validator)

        # 分隔条
        self.line_2 = QtWidgets.QFrame(self.groupBox)
        self.line_2.setGeometry(QtCore.QRect(40, 370, 580, 10))
        self.line_2.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_2.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_2.setObjectName("line_2")

        self.pushButton_1 = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_1.setGeometry(QtCore.QRect(150, 390, 120, 40))
        self.pushButton_1.setFixedHeight(30)
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(50)
        self.pushButton_1.setFont(font)
        self.pushButton_1.setObjectName("pushButton_1")
        self.pushButton_1.setText("参数检查 (C)")
        self.pushButton_1.setShortcut(QKeySequence("Alt+c"))
        self.pushButton_1.clicked.connect(self.extract_parameter_check)

        self.pushButton_2 = QtWidgets.QPushButton(self.groupBox)
        self.pushButton_2.setGeometry(QtCore.QRect(380, 390, 120, 40))
        self.pushButton_2.setFixedHeight(30)
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(50)
        self.pushButton_2.setFont(font)
        self.pushButton_2.setObjectName("pushButton_2")
        self.pushButton_2.setText("提取数据 (X)")
        self.pushButton_2.setShortcut(QKeySequence("Alt+x"))
        self.pushButton_2.setDisabled(True)
        self.pushButton_2.clicked.connect(self.extract_data_start)
        self.pushButton_2.clicked.connect(self.extract_stop_button_set)

        self.label_10 = QtWidgets.QLabel(self.groupBox)
        self.label_10.setGeometry(QtCore.QRect(40, 430, 65, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_10.setFont(font)
        self.label_10.setObjectName("label_10")
        self.label_10.setText("提示信息:")

        self.label_11 = QtWidgets.QLabel(self.groupBox)
        self.label_11.setGeometry(QtCore.QRect(150, 430, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_11.setFont(font)
        self.label_11.setObjectName("label_11")
        self.extract_tips_update('OrangeRed', '请先输入相关参数并进行参数检查，检查通过后再提取！')

        self.label_extract_time = QtWidgets.QLabel(self.groupBox)
        self.label_extract_time.setGeometry(QtCore.QRect(520, 430, 65, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_extract_time.setFont(font)
        self.label_extract_time.setObjectName("label_extract_time")
        self.label_extract_time.setText("提取时间:")

        self.label_extract_time_display = QtWidgets.QLabel(self.groupBox)
        self.label_extract_time_display.setGeometry(QtCore.QRect(580, 430, 60, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_extract_time_display.setFont(font)
        self.label_extract_time_display.setObjectName("label_extract_time_display")

        # 发送数据的部分
        self.groupBox1 = QtWidgets.QGroupBox(self)
        self.groupBox1.setGeometry(QtCore.QRect(30, 520, 660, 410))
        font = QtGui.QFont()
        font.setFamily("Microsoft YaHei UI")
        font.setPointSize(12)
        font.setWeight(75)
        self.groupBox1.setFont(font)
        self.groupBox1.setObjectName("groupBox")
        self.groupBox1.setStyleSheet('''QGroupBox{background-color:#F8F8FF;}''')

        self.label_12 = QtWidgets.QLabel(self.groupBox1)
        self.label_12.setGeometry(QtCore.QRect(40, 10, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_12.setFont(font)
        self.label_12.setObjectName("label_12")
        self.label_12.setText("数据信息:")

        self.label_13 = QtWidgets.QLabel(self.groupBox1)
        self.label_13.setGeometry(QtCore.QRect(40, 40, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_13.setFont(font)
        self.label_13.setObjectName("label_13")
        self.label_13.setText("发送数据文件:")

        self.lineEdit_7 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_7.setGeometry(QtCore.QRect(150, 40, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_7.setFont(font)
        self.lineEdit_7.setObjectName("lineEdit_7")
        self.lineEdit_7.setClearButtonEnabled(True)

        self.button_pick_data = QPushButton(self.groupBox1)
        self.button_pick_data.setGeometry(QtCore.QRect(520, 40, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.button_pick_data.setFont(font)
        self.button_pick_data.setObjectName("button_pick_data")
        self.button_pick_data.setText("选择文件")
        self.button_pick_data.clicked.connect(self.send_neat_data_pick)

        # 分隔条
        self.line_4 = QtWidgets.QFrame(self.groupBox1)
        self.line_4.setGeometry(QtCore.QRect(40, 80, 580, 10))
        self.line_4.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_4.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_4.setObjectName("line_4")

        # 发送类型
        self.label_14 = QtWidgets.QLabel(self.groupBox1)
        self.label_14.setGeometry(QtCore.QRect(40, 100, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_14.setFont(font)
        self.label_14.setObjectName("label_14")
        self.label_14.setText("数据类型:")

        self.radioButton_4 = QRadioButton(self.groupBox1)
        self.radioButton_4.setGeometry(QtCore.QRect(150, 100, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_4.setFont(font)
        self.radioButton_4.setObjectName("radioButton_4")
        self.radioButton_4.setText("视频数据:")
        self.radioButton_4.clicked.connect(self.send_video_data_ip_set)

        self.radioButton_5 = QRadioButton(self.groupBox1)
        self.radioButton_5.setGeometry(QtCore.QRect(285, 100, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_5.setFont(font)
        self.radioButton_5.setObjectName("radioButton_5")
        self.radioButton_5.setText("飞控数据:")
        self.radioButton_5.clicked.connect(self.send_fc_data_ip_set)

        self.radioButton_6 = QRadioButton(self.groupBox1)
        self.radioButton_6.setGeometry(QtCore.QRect(420, 100, 80, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.radioButton_6.setFont(font)
        self.radioButton_6.setObjectName("radioButton_6")
        self.radioButton_6.setText("其他数据:")
        self.radioButton_6.setChecked(True)
        self.radioButton_6.clicked.connect(self.send_other_data_ip_set)

        # 发送范围
        self.label_15 = QtWidgets.QLabel(self.groupBox1)
        self.label_15.setGeometry(QtCore.QRect(40, 145, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_15.setFont(font)
        self.label_15.setObjectName("label_15")
        self.label_15.setText("发送范围:")

        self.label_16 = QtWidgets.QLabel(self.groupBox1)
        self.label_16.setGeometry(QtCore.QRect(40, 180, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_16.setFont(font)
        self.label_16.setObjectName("label_16")
        self.label_16.setText("源IP地址：")

        # self.lineEdit_8 = QtWidgets.QLineEdit(self.groupBox1)
        self.combo_box = QtWidgets.QComboBox(self.groupBox1)
        self.combo_box.setGeometry(QtCore.QRect(150, 180, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.combo_box.setFont(font)
        self.combo_box.setObjectName("combo_box")
        # self.combo_box.setInputMask('000.000.000.000')
        self.combo_box.addItems(get_all_host_ip())

        self.label_17 = QtWidgets.QLabel(self.groupBox1)
        self.label_17.setGeometry(QtCore.QRect(300, 180, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_17.setFont(font)
        self.label_17.setObjectName("label_17")
        self.label_17.setText("源端口：")

        self.lineEdit_9 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_9.setGeometry(QtCore.QRect(380, 180, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_9.setFont(font)
        self.lineEdit_9.setObjectName("lineEdit_9")
        self.lineEdit_9.setClearButtonEnabled(True)
        port_validator = QIntValidator(self)
        port_validator.setRange(1024, 65535)
        self.lineEdit_9.setValidator(port_validator)
        self.lineEdit_9.setPlaceholderText("1024~65535")

        self.label_18 = QtWidgets.QLabel(self.groupBox1)
        self.label_18.setGeometry(QtCore.QRect(40, 215, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_18.setFont(font)
        self.label_18.setObjectName("label_18")
        self.label_18.setText("目的IP地址：")

        self.lineEdit_10 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_10.setGeometry(QtCore.QRect(150, 215, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_10.setFont(font)
        self.lineEdit_10.setObjectName("lineEdit_10")
        self.lineEdit_10.setInputMask('000.000.000.000')

        self.label_19 = QtWidgets.QLabel(self.groupBox1)
        self.label_19.setGeometry(QtCore.QRect(300, 215, 150, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_19.setFont(font)
        self.label_19.setObjectName("label_19")
        self.label_19.setText("目的端口：")

        self.lineEdit_11 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_11.setGeometry(QtCore.QRect(380, 215, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.lineEdit_11.setFont(font)
        self.lineEdit_11.setObjectName("lineEdit_11")
        self.lineEdit_11.setClearButtonEnabled(True)
        self.lineEdit_11.setValidator(port_validator)
        self.lineEdit_11.setPlaceholderText("1024~65535")

        self.label_22 = QtWidgets.QLabel(self.groupBox1)
        self.label_22.setGeometry(QtCore.QRect(40, 255, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(75)
        self.label_22.setFont(font)
        self.label_22.setObjectName("label_22")
        self.label_22.setText("发送参数:")

        self.label_23 = QtWidgets.QLabel(self.groupBox1)
        self.label_23.setGeometry(QtCore.QRect(40, 295, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_23.setFont(font)
        self.label_23.setObjectName("label_23")
        self.label_23.setText("时间间隔(ms)：")

        self.lineEdit_12 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_12.setGeometry(QtCore.QRect(150, 295, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_12.setFont(font)
        self.lineEdit_12.setObjectName("lineEdit_12")
        self.lineEdit_12.setClearButtonEnabled(True)
        self.lineEdit_12.setPlaceholderText('1~99999')
        time_validator = QIntValidator(self)
        time_validator.setRange(1, 99999)
        self.lineEdit_12.setValidator(time_validator)

        # 是否循环发送
        self.label_24 = QtWidgets.QLabel(self.groupBox1)
        self.label_24.setGeometry(QtCore.QRect(300, 295, 100, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.label_24.setFont(font)
        self.label_24.setObjectName("label_24")
        self.label_24.setText('循环次数：')

        self.lineEdit_13 = QtWidgets.QLineEdit(self.groupBox1)
        self.lineEdit_13.setGeometry(QtCore.QRect(380, 295, 120, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 11)
        font.setWeight(50)
        self.lineEdit_13.setFont(font)
        self.lineEdit_13.setObjectName("lineEdit_13")
        self.lineEdit_13.setPlaceholderText('0 表示无限循环')
        self.lineEdit_13.setClearButtonEnabled(True)
        num_validator = QIntValidator(self)
        num_validator.setRange(0, 9999)
        self.lineEdit_13.setValidator(num_validator)

        # 分隔条
        self.line_5 = QtWidgets.QFrame(self.groupBox1)
        self.line_5.setGeometry(QtCore.QRect(40, 330, 580, 10))
        self.line_5.setFrameShape(QtWidgets.QFrame.HLine)
        self.line_5.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.line_5.setObjectName("line_5")

        self.button_send_param = QtWidgets.QPushButton(self.groupBox1)
        self.button_send_param.setGeometry(QtCore.QRect(150, 350, 120, 40))
        self.button_send_param.setFixedHeight(30)
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(50)
        self.button_send_param.setFont(font)
        self.button_send_param.setObjectName("button_send_param")
        self.button_send_param.setText("参数检查 (Q)")
        self.button_send_param.setShortcut(QKeySequence("Alt+q"))
        self.button_send_param.clicked.connect(self.send_parameter_check)

        self.button_send = QtWidgets.QPushButton(self.groupBox1)
        self.button_send.setGeometry(QtCore.QRect(380, 350, 120, 40))
        self.button_send.setFixedHeight(30)
        font = QtGui.QFont("Microsoft YaHei UI", 12)
        font.setWeight(50)
        self.button_send.setFont(font)
        self.button_send.setObjectName("button_send")
        self.button_send.setText("发送数据 (S)")
        self.button_send.setShortcut(QKeySequence("Alt+s"))
        self.button_send.setDisabled(True)
        self.button_send.clicked.connect(self.send_data_start)
        self.button_send.clicked.connect(self.send_stop_button_set)

        self.label_20 = QtWidgets.QLabel(self.groupBox1)
        self.label_20.setGeometry(QtCore.QRect(40, 380, 65, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_20.setFont(font)
        self.label_20.setObjectName("label_20")
        self.label_20.setText("提示信息:")

        self.label_21 = QtWidgets.QLabel(self.groupBox1)
        self.label_21.setGeometry(QtCore.QRect(150, 380, 350, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_21.setFont(font)
        self.label_21.setObjectName("label_21")
        self.send_tips_update('OrangeRed', '请先输入相关参数并进行参数检查，检查通过后再发送！')

        self.label_send_time = QtWidgets.QLabel(self.groupBox1)
        self.label_send_time.setGeometry(QtCore.QRect(520, 380, 65, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_send_time.setFont(font)
        self.label_send_time.setObjectName("label_send_time")
        self.label_send_time.setText("发送时间:")

        self.label_send_time_display = QtWidgets.QLabel(self.groupBox1)
        self.label_send_time_display.setGeometry(QtCore.QRect(580, 380, 60, 25))
        font = QtGui.QFont("Microsoft YaHei UI", 10)
        font.setWeight(50)
        self.label_send_time_display.setFont(font)
        self.label_send_time_display.setObjectName("label_send_time_display")

    def keyPressEvent(self, event):
        """
        Func: 按下 ESC 关闭程序.
        :param event: 按键事件
        """
        if event.key() == Qt.Key_Escape:
            self.close()

    def set_tshark(self):
        """
        Func: 设置tshark.exe的安装路径，一般与 wireshark.exe 同级目录
        """
        tshark_path, file_filter = QFileDialog.getOpenFileName(self, '选择 tshark', '.', '*.exe')

        if tshark_path == '':
            tshark_path = 'C:/Program Files/Wireshark/tshark.exe'

        self.lineEdit_0.setText(tshark_path)
        extract_logger.info(f'设置tshark的路径为：{tshark_path}')

    def extract_raw_data_load(self):
        """
        Func: 打开选择文件的对话框，用来选择原始数据文件，目前支持 pcapng 和 pcap格式。选择完成之后，会自动生成一个保存输出数据文件的路径
        """
        file_abs_path, file_filter = QFileDialog.getOpenFileName(self, '选择原始数据文件', '.', '数据文件(*.pcap *.pcapng)')
        self.lineEdit_1.setText(file_abs_path)

        # 选择原始数据文件之后，自动默认生成一个数据输出文件
        self.extract_neat_data_save_default()

    def extract_neat_data_save(self):
        """
        Func: 用来保存提取出来的数据，默认保存到和原始数据同级目录下
        """
        self.lineEdit_2.setToolTip(self.lineEdit_2.text())
        if self.lineEdit_1.text() == '':
            extract_logger.warning('请首先设置原始数据文件！')
            self.extract_tips_update('Orange', '请首先设置原始数据文件！')
            self.update_messageboxes('警告', '请首先设置原始数据文件！')
        elif not os.path.exists(self.lineEdit_1.text()):
            extract_logger.error('原始数据文件不存在！')
            self.extract_tips_update('Red', '原始数据文件不存在件！')
            self.update_messageboxes('错误', '原始数据文件不存在！')
        else:
            # 获取原始数据文件的绝对路径
            abs_path = self.lineEdit_1.text()
            # 根据原始数据文件生产处理提取之后的数据文件名称（保存为txt格式）
            neat_filename = os.path.splitext(os.path.basename(abs_path))[0] + '.txt'
            # 打开选择文件的对话框，并返回 选中的目录
            file_path = QFileDialog(parent=None).getExistingDirectory(parent=None)

            # 但打开选择文件的对话框之后，并没有选择某个目录，而是直接关掉对话框，则会返回一个空字符串
            if file_path == '':
                extract_logger.info('没有选择路径，将使用默认的输出数据文件！')
                self.extract_tips_update('LightSeaGreen', '没有选择路径，将使用默认的输出数据文件！')
                self.update_messageboxes('提示', '没有选择路径，将使用默认的输出数据文件！')
                file_path = os.path.split(abs_path)[0]

            # NOTE: 根据选择的保存路径和新的文件名称组成新的据对路径（注意使用 join()函数时 添加 ‘/’）
            neat_file_abs_path = os.path.join(file_path + '/', neat_filename)
            # 将选择的绝对路径显示到对应的文本框中
            self.lineEdit_2.setText(neat_file_abs_path)
            self.lineEdit_2.setToolTip(neat_file_abs_path)  # 鼠标放上去的提示信息
            extract_logger.info(f'提取的数据将保存到 {neat_file_abs_path}。')

    def extract_neat_data_save_default(self):
        """
        Func: 直接设置默认设置保存数据的路径和文件，而无须打开 选择文件的对话框来选择保存的文件
        """
        # 先判断原始文件的路径是否正确设置
        if self.lineEdit_1.text() == '':
            extract_logger.warning('请首先设置原始数据文件！')
            self.extract_tips_update('Orange', '请首先设置原始数据文件！')
            self.update_messageboxes('警告', '请首先设置原始数据文件！')
        elif not os.path.exists(self.lineEdit_1.text()):
            extract_logger.error( '原始数据文件不存在！')
            self.extract_tips_update('Red', '原始数据文件不存在！')
            self.update_messageboxes('错误', '原始数据文件不存在！')
        else:
            # 获取原始数据文件的绝对路径
            abs_path = self.lineEdit_1.text()
            # 根据原始数据文件生产处理提取之后的数据文件名称（保存为txt格式）
            new_filename = os.path.splitext(os.path.basename(abs_path))[0] + '.txt'
            # NOTE: 根据选择的保存路径和新的文件名称组成新的据对路径（注意使用 join()函数时 添加 ‘/’）
            new_file_abs_path = os.path.join(os.path.split(abs_path)[0] + '/', new_filename)
            # 将选择的绝对路径显示到对应的文本框中
            self.lineEdit_2.setText(new_file_abs_path)
            # 鼠标放上去的提示信息
            self.lineEdit_2.setToolTip(new_file_abs_path)
            extract_logger.info(f'提取的数据将保存到：{self.lineEdit_2.text()}')

    def extract_video_data_ip_set(self):
        """
        Func: 设置提取无人机视频数据的IP地址和端口 - '192.168.0.163:5000,226.0.0.80:8001'
        """
        src_ip = '192.168.0.163'
        src_port = '5000'
        dst_ip = '226.0.0.80'
        dst_port = '8001'
        self.lineEdit_3.setText(src_ip)
        self.lineEdit_4.setText(src_port)
        self.lineEdit_5.setText(dst_ip)
        self.lineEdit_6.setText(dst_port)
        extract_logger.info('设置提取无人机视频数据的IP地址和端口。')

    def extract_fc_data_ip_set(self):
        """
        Func: 设置提取无人机飞控数据的IP地址和端口 - '192.168.0.163:4700,226.0.0.80:6091'
        """
        src_ip = '192.168.0.163'
        src_port = '4700'
        dst_ip = '226.0.0.80'
        dst_port = '6091'
        self.lineEdit_3.setText(src_ip)
        self.lineEdit_4.setText(src_port)
        self.lineEdit_5.setText(dst_ip)
        self.lineEdit_6.setText(dst_port)
        extract_logger.info('设置提取无人机飞控数据的IP地址和端口。')

    def extract_other_data_ip_set(self):
        """
        Func: 先清空文本框中的原有内容，然后设置提取其他数据的IP地址和端口
        """
        self.lineEdit_3.clear()
        self.lineEdit_4.clear()
        self.lineEdit_5.clear()
        self.lineEdit_6.clear()
        extract_logger.info('清空原有的提取数据的IP地址和端口。')

    def check_tshark(self):
        """
        Func: 检查提供的tshark.exe是否存在，路径是否正确，以及能否正常使用
        :return: 如果tshark.exe存在且能正常使用，返回其绝对路径（string）; 否则返回False
        """
        self.extract_tips_update('LightSeaGreen', '开始检查 tshark ... ')
        extract_logger.info('开始检查 tshark ... ')
        tshark_path = self.lineEdit_0.text()
        if not os.path.exists(tshark_path):
            extract_logger.critical('提取工具tshark不存在或路径错误！')
            self.extract_tips_update('Red', '提取工具tshark不存在或路径错误！')
            self.update_messageboxes('错误', '提取工具tshark不存在或路径错误！')
            return False
        else:
            # NOTE: 在系统中执行 tshark.exe -v 查看tshark的版本信息
            extract_process = Popen(f'"{tshark_path}" -v', shell=True, stdout=PIPE, stderr=PIPE)
            stdout, stderr = extract_process.communicate()

            # NOTE: tshark.exe -v 正确执行的输出结果是 ‘TShark (Wireshark) 4.0.5 ..... ’；同时命令正确执行之后，返回码为 0
            if not str(stdout.decode()).startswith('TShark (Wireshark)') or extract_process.returncode != 0:
                extract_logger.critical('指定的tshark不能使用！')
                self.extract_tips_update('Red', '指定的tshark不能使用！')
                self.update_messageboxes('错误', '指定的tshark不能使用！')
                return False

            return tshark_path

    def extract_raw_data_check(self):
        """
        Fund: 检查用户提供的原始数据文件是否存在
        :return: 返回给定的原始文件的绝对路径（string）；否则返回 False
        """
        self.extract_tips_update('LightSeaGreen', '开始检查原始数据文件 ... ')
        extract_logger.info('开始检查原始数据文件 ... ')
        raw_data_file = self.lineEdit_1.text()
        if not os.path.exists(raw_data_file):
            extract_logger.critical('原始数据文件不存在或路径错误！')
            self.extract_tips_update('Red', '原始数据文件不存在或路径错误！')
            self.update_messageboxes('错误', '原始数据文件不存在或路径错误！')
            return False

        return raw_data_file

    def extract_neat_data_check(self):
        """
        Func: 检查指定的保存提取出来的数据文件格式是否为txt，指定的路径是否存在，如果已经存在，则给指定的文件名增加时间戳
        :return: 返回确认之后的文件绝对路径（string）；否则返回False
        """
        self.extract_tips_update('LightSeaGreen', '开始检查输出数据文件 ... ')
        extract_logger.info('开始检查输出数据文件 ... ')
        neat_data_file = self.lineEdit_2.text()
        if os.path.exists(neat_data_file):
            self.extract_tips_update('Orange', '设置的输出数据文件已经存在！')

            # NOTE：给定的文件已经存在，则在默认文件名的基础上增加当前时间戳，生成新的文件名
            self.extract_neat_data_save_default()
            new_neat_data_file = attach_time_tag(self.lineEdit_2.text())
            self.lineEdit_2.setText(new_neat_data_file)

            # 鼠标放上去的提示信息
            self.lineEdit_2.setToolTip(new_neat_data_file)

            extract_logger.warning(f'设置的输出数据文件已经存在，数据将保存到 {new_neat_data_file}！')
            self.update_messageboxes('警告', f'设置的输出数据文件已经存在！数据将保存到\n{new_neat_data_file}！')
        elif neat_data_file == '':
            extract_logger.critical('输出数据文件为空！')
            self.extract_tips_update('Red', '输出数据文件为空！')
            self.update_messageboxes('错误', '输出数据文件为空！')
            return False
        elif os.path.splitext(neat_data_file)[-1] != '.txt':
            extract_logger.error('输出数据文件的格式不是txt！')
            self.extract_tips_update('Red', '输出数据文件的格式不是txt！')
            self.update_messageboxes('错误', '输出数据文件的格式不是txt！')
            return False
        elif not os.path.isdir(os.path.split(neat_data_file)[0]):
            extract_logger.error('输出数据文件的保存路径不存在！')
            self.extract_tips_update('Red', '输出数据文件的保存路径不存在！')
            self.update_messageboxes('错误', '输出数据文件的保存路径不存在！')
            return False
        else:
            return

    def extract_ip_and_port_check(self):
        """
        Func: 检查发送提取数据的IP地址和端口
        :return: Boolean
        """
        src_ip = self.lineEdit_3.text()
        src_port = self.lineEdit_4.text()
        dst_ip = self.lineEdit_5.text()
        dst_port = self.lineEdit_6.text()

        self.extract_tips_update('LightSeaGreen', '开始检查提取数据的IP地址和端口 ... ')
        extract_logger.info('开始检查提取数据的IP地址和端口 ... ')
        if src_ip == '...' or src_port == '' or dst_ip == '...' or dst_port == '':
            extract_logger.critical('提取数据的IP地址或端口不能为空！')
            self.extract_tips_update('Red', '提取数据的IP地址或端口不能为空！')
            self.update_messageboxes('错误', '提取数据的IP地址或端口不能为空！')
            return False
        else:
            src_port = int(src_port)
            dst_port = int(dst_port)
            if src_port < 1 or src_port > 65535 or dst_port < 1 or dst_port > 65535:
                extract_logger.error('提取数据的端口必须介于 1~65535！')
                self.extract_tips_update('Red', '提取数据的端口必须介于 1~65535！')
                self.update_messageboxes('错误', '提取数据的端口必须介于 1~65535！')
                return False

            # 数据提取范围，格式为 192.168.0.163:4700,226.0.0.80:6091
            src = ':'.join([src_ip, str(src_port)])
            dst = ':'.join([dst_ip, str(dst_port)])
            data_range = ','.join([src, dst])

            self.extract_tips_update('LightSeaGreen', f'数据的提取范围为{data_range}。')
            extract_logger.info(f'数据的提取范围为{data_range}。')
            return True

    def extract_check_pushbutton_set(self):
        """
        Func: 检查各项参数符合要求之后，将各个输入框和按钮进行设置，不允许临时修改
        """
        self.lineEdit_0.setReadOnly(True)
        self.button_set_tshark.setDisabled(True)
        self.lineEdit_1.setReadOnly(True)
        self.button_loadfile.setDisabled(True)
        self.lineEdit_2.setReadOnly(True)
        self.button_savefile.setDisabled(True)
        self.radioButton_1.setEnabled(False)
        self.radioButton_2.setEnabled(False)
        self.radioButton_3.setEnabled(False)
        self.lineEdit_3.setReadOnly(True)
        self.lineEdit_4.setReadOnly(True)
        self.lineEdit_5.setReadOnly(True)
        self.lineEdit_6.setReadOnly(True)
        self.label_extract_time_display.clear()
        extract_logger.info('各项参数符合要求，设置相关按钮和输入框的状态，防止在提取过程中修改参数。')

    def extract_modify_pushbutton_set(self):
        """
        Func: 将各个按钮和输入框恢复成可以编译和点击的状态
        """
        self.lineEdit_0.setReadOnly(False)
        self.button_set_tshark.setDisabled(False)
        self.lineEdit_1.setReadOnly(False)
        self.button_loadfile.setDisabled(False)
        self.lineEdit_2.setReadOnly(False)
        self.button_savefile.setDisabled(False)
        self.radioButton_1.setEnabled(True)
        self.radioButton_2.setEnabled(True)
        self.radioButton_3.setEnabled(True)
        self.lineEdit_3.setReadOnly(False)
        self.lineEdit_4.setReadOnly(False)
        self.lineEdit_5.setReadOnly(False)
        self.lineEdit_6.setReadOnly(False)
        self.label_extract_time_display.clear()
        extract_logger.info('恢复相关按钮和输入框的状态，用户可自由修改提取参数。')

    def extract_stop_button_set(self):
        """
        Func: 设置【停止提取】按钮的相关状态，解绑和绑定对应的槽函数
        """
        if self.pushButton_1.text() == '参数检查 (C)':
            return

        self.pushButton_2.setText("停止提取 (D)")
        self.pushButton_2.setShortcut(QKeySequence("Alt+d"))
        self.pushButton_2.clicked.disconnect()
        # Note: 此处注意绑定槽函数的顺序，不能对调顺序，否则 extract_stop() 无法调用
        self.pushButton_2.clicked.connect(extract_stop)
        self.pushButton_2.clicked.connect(self.extract_start_button_set)

    def extract_start_button_set(self):
        """
        Func: 提取按钮 从【停止提取】恢复成【提取数据】的状态设置，解绑和绑定对应的槽函数
        """
        if self.pushButton_1.text() == '参数检查 (C)':
            extract_logger.warning('请先进行参数检查！')
            self.send_tips_update('OrangeRed', '请先进行参数检查！')
            self.update_messageboxes('警告', '请先进行参数检查！')
            return

        self.pushButton_2.setText("提取数据 (X)")
        self.pushButton_2.setShortcut(QKeySequence("Alt+x"))
        self.pushButton_2.clicked.disconnect()
        # Note: 此处注意绑定槽函数的顺序，如果交换顺序则会到导致再次点击【提取数据】时，不会开始提取操作
        self.pushButton_2.clicked.connect(self.extract_data_start)
        self.pushButton_2.clicked.connect(self.extract_stop_button_set)
        self.label_extract_time_display.clear()

    def extract_parameter_check(self):
        """
        Func: 提取之前检查各项参数是否符合要求
        """
        if self.pushButton_1.text() == '参数检查 (C)':
            extract_logger.info('开始检查各项参数 ... ')
            self.extract_tips_update('LightSeaGreen', '开始检查各项参数 ... ')
            status = [self.check_tshark(), self.extract_raw_data_check(), self.extract_neat_data_check(), self.extract_ip_and_port_check()]
            if False in status:
                self.pushButton_2.setEnabled(False)
            else:
                self.pushButton_1.setText('参数修改 (M)')
                self.pushButton_1.setShortcut(QKeySequence('Alt+m'))
                self.extract_check_pushbutton_set()

                # 判断上一次提取数据的子线程是否运行结束
                if self.extract_timecount.isRunning():
                    # 当上一次的提取数据子线程仍然在运行时，不能再次提取新的数据，故将【提取数据】的按钮置灰。
                    self.pushButton_2.setEnabled(False)
                    extract_logger.warning('各项参数符合要求，请等待当前提取完成之后再开始提取！')
                    self.extract_tips_update('OrangeRed', '各项参数符合要求，请等待当前提取完成之后再开始提取！')
                else:
                    # 只有当上一次的提取数据子线程结束之后，才能重新放开 【提取数据】的按钮
                    self.pushButton_2.setEnabled(True)
                    extract_logger.info('各项参数符合要求，可以开始提取。')
                    self.extract_tips_update('LightSeaGreen', '各项参数符合要求，可以提取。')
        elif self.pushButton_1.text() == '参数修改 (M)':
            extract_logger.warning('请修改相关参数，修改后需要再次检查！')
            self.extract_modify_pushbutton_set()
            self.extract_tips_update('OrangeRed', '请修改相关参数，修改后需要再次检查！')
            self.pushButton_1.setText('参数检查 (C)')
            self.pushButton_1.setShortcut(QKeySequence("Alt+c"))
        else:
            pass

    def extract_data_start(self):
        """
        Func: 创建提取数据的子线程
        """
        # 点击【开始发送】按钮，将发送标志位ExtractParam.extract_flag 置为True
        ExtractParam.extract_flag = True

        if self.pushButton_1.text() != '参数修改 (M)':
            extract_logger.warning('请先点击参数检查，检查无误后再提取数据！')
            self.send_tips_update('OrangeRed', '请先点击参数检查，检查无误后再提取数据！')
            self.update_messageboxes('警告', '请先点击参数检查，检查无误后再提取数据！')
            return

        # 如果设置的文件已经存在，则一默认文件名加上时间戳来作为新的输出文件名
        if os.path.exists(self.lineEdit_2.text()):
            # 恢复默认输出文件名
            self.extract_neat_data_save_default()
            # 添加时间戳
            new_neat_data_file = attach_time_tag(self.lineEdit_2.text())
            self.lineEdit_2.setText(new_neat_data_file)
            # 鼠标放上去的提示信息
            self.lineEdit_2.setToolTip(new_neat_data_file)
            extract_logger.info(f'保存数据到{new_neat_data_file}')

        tool = self.lineEdit_0.text()
        raw_data = self.lineEdit_1.text()
        neat_data = self.lineEdit_2.text()

        src = ':'.join([self.lineEdit_3.text(), str(int(self.lineEdit_4.text()))])
        dst = ':'.join([self.lineEdit_5.text(), str(int(self.lineEdit_6.text()))])
        data_range = ','.join([src, dst])

        # 创建 提取数据的子线程
        extract_logger.info('开始创建提取数据的子线程 ... ')
        self.extract_thread = QThread()
        self.my_extract_thread = ExtractThread(tool, raw_data, neat_data, data_range)
        self.my_extract_thread.moveToThread(self.extract_thread)

        # 提取数据的子线程开始之后, 调用提取函数提取数据
        self.extract_thread.started.connect(self.my_extract_thread.start_extract)

        # 提取数据的子线程开始之后，开始调用计时的子线程
        self.my_extract_thread.extract_start.connect(self.extract_timecount_start)

        # 提取数据的子线程开始之后，【参数修改】按钮置灰
        self.my_extract_thread.extract_start.connect(lambda: self.pushButton_1.setDisabled(True))

        # 提取数据的子线程结束之后，【停止提取】按钮恢复为【提取数据】按钮
        self.my_extract_thread.extract_finished.connect(self.extract_start_button_set)

        # 提取数据的子线程开始和结束之后，【参数修改】按钮和【提取数据】按钮恢复为可点击状态
        self.my_extract_thread.extract_finished.connect(lambda: self.pushButton_1.setEnabled(True))
        self.my_extract_thread.extract_finished.connect(lambda: self.pushButton_2.setEnabled(True))

        # 提取数据的子线程结束之后退出，之后删除
        self.my_extract_thread.extract_finished.connect(self.extract_thread.quit)
        self.my_extract_thread.extract_finished.connect(self.my_extract_thread.deleteLater)
        self.extract_thread.finished.connect(self.extract_thread.deleteLater)

        # 提取数据的子线程结束之后，终止用来计时的子线程，停止计时
        self.my_extract_thread.extract_finished.connect(self.extract_timecount_stop)

        # 在界面上更新进展
        self.my_extract_thread.update_tip.connect(self.extract_tips_update)
        self.my_extract_thread.update_messagebox.connect(self.update_messageboxes)

        # 提取完成之后，打开文件所在的目录
        self.my_extract_thread.open_file_location.connect(self.extract_open_file_locations)

        # 开始提取数据的线程
        extract_logger.info('开始启动提取数据的子线程 ... ')
        self.extract_thread.start()

    def extract_timecount_start(self):
        """
        Func: 开始计时, 并显示提取数据所消耗的时间
        """
        self.label_extract_time_display.clear()
        self.extract_timecount.counting.connect(self.extract_time_display)
        self.extract_timecount.start()

    def extract_time_display(self, count_time):
        """
        Func: 显示提取所用的时间
        :param count_time: 字符串，表示提取所花费的时间
        """
        self.label_extract_time_display.setText(f'<font color=LightSeaGreen>{count_time}s</font>')
        self.label_extract_time_display.repaint()

    def extract_timecount_stop(self):
        """
        Func: 停止计时的子线程，之后退出提取数据的子线程
        """
        self.extract_timecount.stop()
        self.extract_thread.quit()
        self.extract_thread.wait()

    def extract_tips_update(self, level, tip):
        """
        Func: 更新提取数据时相关的提示信息
        :param level: 提示信息的级别，不同的级别用不同的颜色标记
        :param tip: 提示信息的内容
        """
        self.label_11.setText(f"<font color={level}>{tip}</font>")
        self.label_11.repaint()

    def extract_open_file_locations(self):
        """
        Func: 在资源管理器中打开文件所在的位置
        """
        data_path = self.lineEdit_2.text()
        if data_path == '':
            self.send_tips_update('Red', '输出数据文件的路径为空！')
            self.update_messageboxes('错误', '输出数据文件的路径为空！')
            extract_logger.critical('输出数据文件的路径为空！')
            return
        else:
            path = os.path.split(data_path)[0]
            if not os.path.isdir(path):
                self.send_tips_update('Red', '输出数据文件的路径错误！')
                self.update_messageboxes('错误', '输出数据文件的路径错误！')
                extract_logger.critical('输出数据文件的路径错误！')
                return

        # NOTE: explorer.exe 后面跟的路径需要使用 '\\' 来分隔路径，不能使用 '/'
        path = path.replace('/', '\\')
        extract_logger.info(f'打开提取数据文件所在的路径：{path}')
        os.system(f'explorer.exe {path}')

    def update_messageboxes(self, level, tip):
        """
        Func：更新弹窗提示信息
        :param level：提示信息的级别
        :param tip：提示信息的内容
        """
        if level == '错误':
            QMessageBox.critical(self, level, tip, QMessageBox.Ok, QMessageBox.Ok)
        elif level == '警告':
            QMessageBox.warning(self, level, tip, QMessageBox.Ok, QMessageBox.Ok)
        else:
            QMessageBox.information(self, level, tip, QMessageBox.Ok, QMessageBox.Ok)

    def send_tips_update(self, level, tip):
        """
        Func: 更新发送数据时相关的提示信息
        :param level: 提示信息的级别，不同的级别用不同的颜色标记
        :param tip: 提示信息的内容
        """
        self.label_21.setText(f"<font color={level}>{tip}</font>")
        self.label_21.repaint()                                     # 立即显示

    def send_neat_data_pick(self):
        """
        Func: 打开选择文件的对话框，用来选择保存发送UDP数据的文本文件，目前支持 txt 和 log格式
        """
        self.lineEdit_7.setEnabled(True)
        file_abs_path, file_filter = QFileDialog.getOpenFileName(self, '选择发送数据文件', '.', '数据文件(*.txt *.log)')
        self.lineEdit_7.setText(file_abs_path)

    def send_video_data_ip_set(self):
        """
        Func: 设置发送视频数据的IP地址和端口 - '192.168.0.163:5000,226.0.0.80:8001'
        """
        src_port = '8899'
        dst_ip = '226.0.0.80'
        dst_port = '8001'
        self.lineEdit_9.setText(src_port)
        self.lineEdit_10.setText(dst_ip)
        self.lineEdit_11.setText(dst_port)
        send_logger.info('设置发送无人机视频数据的IP地址和端口。')

    def send_fc_data_ip_set(self):
        """
        Func: 设置发送飞控数据的IP地址和端口 - '192.168.0.163:4700,226.0.0.80:6091'
        """
        src_port = '9988'
        dst_ip = '226.0.0.80'
        dst_port = '6091'
        self.lineEdit_9.setText(src_port)
        self.lineEdit_10.setText(dst_ip)
        self.lineEdit_11.setText(dst_port)
        send_logger.info('设置发送无人机飞控数据的IP地址和端口。')

    def send_other_data_ip_set(self):
        """
        Func: 先清空文本框中的原有内容，然后需要用户设置需要的IP地址和端口
        """
        self.lineEdit_9.clear()
        self.lineEdit_10.clear()
        self.lineEdit_11.clear()
        send_logger.info('清空原有的发送数据的IP地址和端口。')

    def send_datafile_check(self):
        """
        Func: 检查所选择的发送数据的文件
        :return: Boolean
        """
        datafile = self.lineEdit_7.text()
        if datafile == '':
            send_logger.critical('请首先设置发送数据文件！')
            self.send_tips_update('Orange', '请首先设置发送数据文件！')
            self.update_messageboxes('错误', '请首先设置发送数据文件！')
            return False
        elif not os.path.exists(datafile):
            send_logger.error('发送数据文件不存在！')
            self.send_tips_update('Red', '发送数据文件不存在！')
            self.update_messageboxes('错误', '发送数据文件不存在！')
            return False
        else:
            send_logger.info(f'选择发送的数据文件 {datafile}。')
            return True

    def send_ip_and_port_check(self):
        """
        Func: 检查发送数据使用的IP地址和端口是否可用
        :return: Boolean
        """
        src_ip = self.combo_box.currentText()
        src_port = self.lineEdit_9.text()
        dst_ip = self.lineEdit_10.text()
        dst_port = self.lineEdit_11.text()

        send_logger.info('开始检查发送数据的IP地址和端口 ... ')
        self.send_tips_update('LightSeaGreen', '开始检查发送数据的IP地址和端口 ... ')
        if src_ip == '...' or src_port == '' or dst_ip == '...' or dst_port == '':
            send_logger.critical('发送数据的IP地址或端口不能为空！')
            self.send_tips_update('Red', '发送数据的IP地址或端口不能为空！')
            self.update_messageboxes('错误', '发送数据的IP地址或端口不能为空！')
            return False
        else:
            # 检查指定的端口是否在 1024~65535 之间
            src_port = int(src_port)
            if src_port < 1024 or src_port > 65535:
                send_logger.warning(f'指定的端口{src_port}必须在 1024~65535 之间！')
                self.send_tips_update('OrangeRed', f'指定的端口{src_port}必须在 1024~65535 之间！')
                self.update_messageboxes('警告', f'指定的端口{src_port}必须在 1024~65535 之间！')
                return False
            else:
                # 检查指定的本机IP地址和端口是否可用
                ret = check_ip_port_used(src_ip, src_port)

                if isinstance(ret, str):
                    if '10048' in ret:
                        send_logger.warning(f'指定的端口{src_port}正在被使用，请更换其他端口或者等待当前数据发送完成！')
                        self.send_tips_update('OrangeRed', f'指定的端口{src_port}正在被使用，请更换其他端口！')
                        self.update_messageboxes('警告', f'指定的端口{src_port}正在被使用，请更换其他端口或者等待当前数据发送完成！')
                    elif '10013' in ret:
                        send_logger.warning(f'指定的端口{src_port}没有使用权限，请更换其他端口！')
                        self.send_tips_update('OrangeRed', f'指定的端口{src_port}没有使用权限，请更换其他端口！')
                        self.update_messageboxes('警告', f'指定的端口{src_port}没有使用权限，请更换其他端口！')
                    elif 'Error' in ret:
                        send_logger.warning(f'指定的IP地址{src_ip}或端口{src_port}不能使用，请更换其他地址或端口！')
                        self.send_tips_update('Red', f'指定的IP地址{src_ip}或端口{src_port}不能使用，请更换其他端口！')
                        self.update_messageboxes('错误', f'指定的IP地址{src_ip}或端口{src_port}不能使用，请更换其他端口！')
                    else:
                        send_logger.warning(f'{ret}')
                        self.send_tips_update('Red', ret)
                        self.update_messageboxes('错误', ret)
                    return False
                elif ret is True:
                    send_logger.info(f'指定IP地址{src_ip}和端口{src_port}可以正常使用。 ')
                    self.send_tips_update('LightSeaGreen', f'指定IP地址{src_ip}和端口{src_port}可以正常使用。 ')
                    return True

    def send_delta_time_check(self):
        """
        Func: 检查发送次数和循环次数
        :return: Boolean
        """
        # 发送时间间隔 1~99999 毫秒
        delta_time = self.lineEdit_12.text()
        if delta_time == '':
            send_logger.critical('请输入发送报文的时间间隔！')
            self.send_tips_update('Orange', '请输入发送报文的时间间隔！')
            self.update_messageboxes('警告', '请输入发送报文的时间间隔！')
            return False
        elif int(delta_time) == 0:
            send_logger.critical('发送报文的时间间隔不能为0！')
            self.send_tips_update('Red', '发送报文的时间间隔不能为0！')
            self.update_messageboxes('错误', '发送报文的时间间隔不能为0！')
            return False

        return True

    def send_cycle_times_check(self):
        """
        Func: 对发送给的循环次数进行检测
        :return: Boolean
        """
        # 发送的循环次数
        cycle_num = self.lineEdit_13.text()
        if cycle_num == '':
            send_logger.critical('循环次数不能为空！')
            self.send_tips_update('Red', '循环次数不能为空！')
            self.update_messageboxes('错误', '循环次数不能为空！')
            return False

        return True

    def send_check_pushbutton_set(self):
        """
        Func: 发送数据的参数检查合格之后，调整相关按钮的状态，禁止修改参数
        """
        self.lineEdit_7.setReadOnly(True)
        self.button_pick_data.setDisabled(True)
        self.radioButton_4.setEnabled(False)
        self.radioButton_5.setEnabled(False)
        self.radioButton_6.setEnabled(False)
        self.combo_box.setDisabled(True)
        self.lineEdit_9.setReadOnly(True)
        self.lineEdit_10.setReadOnly(True)
        self.lineEdit_11.setReadOnly(True)
        self.lineEdit_12.setReadOnly(True)
        self.lineEdit_13.setReadOnly(True)
        self.label_send_time_display.clear()
        send_logger.info('发送数据的各项参数符合要求，设置相关按钮和输入框的状态，防止在发送数据的过程中修改参数。')

    def send_modify_pushbutton_set(self):
        """
        Func: 将各个按钮和输入框恢复成可以编译和点击的状态
        """
        self.lineEdit_7.setReadOnly(False)
        self.button_pick_data.setDisabled(False)
        self.radioButton_4.setEnabled(True)
        self.radioButton_5.setEnabled(True)
        self.radioButton_6.setEnabled(True)
        self.combo_box.setDisabled(False)
        self.lineEdit_9.setReadOnly(False)
        self.lineEdit_10.setReadOnly(False)
        self.lineEdit_11.setReadOnly(False)
        self.lineEdit_12.setReadOnly(False)
        self.lineEdit_13.setReadOnly(False)
        self.label_send_time_display.clear()
        send_logger.info('恢复相关按钮和输入框的状态，用户可自由修改发送参数。')

    def send_parameter_check(self):
        """
        Func: 发送数据之前检查各项参数
        """
        if self.button_send_param.text() == '参数检查 (Q)':
            send_logger.info('开始检查各项参数 ... ')
            self.send_tips_update('LightSeaGreen', '开始检查各项参数 ... ')
            status = [self.send_datafile_check(), self.send_ip_and_port_check(), self.send_delta_time_check(),
                      self.send_cycle_times_check()]
            if False in status:
                pass
            else:
                self.button_send_param.setText('参数修改 (W)')
                self.button_send_param.setShortcut(QKeySequence("Alt+w"))
                self.send_check_pushbutton_set()

                # 判断上一次发送数据的子线程是否运行结束
                if self.send_timecount.isRunning():
                    # 当上一次的发送数据子线程仍然在运行时，不能再次提取新的数据，故将【发送数据】的按钮置灰。
                    self.button_send.setDisabled(True)
                    send_logger.warning('各项参数符合要求，请等待当前发送完成之后再开始发送！')
                    self.send_tips_update('OrangeRed', '各项参数符合要求，请等待当前发送完成之后再开始发送！')
                else:
                    # 开始时，【发送数据】按钮默认不可点击，在参数检查通过之后，将其设置为可以点击
                    self.button_send.setEnabled(True)
                    send_logger.info('各项参数符合要求，可以开始发送数据。')
                    self.send_tips_update('LightSeaGreen', '各项参数符合要求，可以开始发送数据。')
        elif self.button_send_param.text() == '参数修改 (W)':
            send_logger.warning('用户需要修改发送数据的参数，修改后需要再次检查！')
            self.send_modify_pushbutton_set()
            self.send_tips_update('OrangeRed', '请修改相关参数，修改后需要再次检查！')
            self.button_send_param.setText('参数检查 (Q)')
            self.button_send_param.setShortcut(QKeySequence("Alt+q"))
        else:
            pass

    def send_data_start(self):
        """
        Func: 开始发送文件中的UDP报文
        """
        # 点击【开始发送】按钮，将发送标志位SendParam.send_flag 置为True
        SendParam.send_flag = True

        if self.button_send_param.text() != '参数修改 (W)':
            send_logger.warning('请先点击参数检查，检查无误后再发送数据！')
            self.send_tips_update('OrangeRed', '请先点击参数检查，检查无误后再发送数据！')
            self.update_messageboxes('警告', '请先点击参数检查，检查无误后再发送数据！')
            return

        src_address = (self.combo_box.currentText(), int(self.lineEdit_9.text()))
        dst_address = (self.lineEdit_10.text(), int(self.lineEdit_11.text()))
        delta_time = int(self.lineEdit_12.text()) / 1000
        data_file = self.lineEdit_7.text()
        times = int(self.lineEdit_13.text())

        # 创建发送数据的子线程
        send_logger.info('开始创建发送数据的子线程 ... ')
        self.send_thread = QThread()
        self.my_send_thread = SendThread(src_address, dst_address, delta_time, data_file, times)
        self.my_send_thread.moveToThread(self.send_thread)

        # 发送数据的子线程开始之后, 调用发送函数发送数据
        self.send_thread.started.connect(self.my_send_thread.start_send)

        # 发送数据的子线程开始之后，开始调用计时的子线程
        self.my_send_thread.send_start.connect(self.send_timecount_start)

        # 发送数据的子线程开始之后，【参数修改】按钮置灰
        self.my_send_thread.send_start.connect(lambda: self.button_send_param.setDisabled(True))

        # 发送数据的子线程结束之后，【停止发送】按钮恢复为【发送数据】按钮
        self.my_send_thread.send_finished.connect(self.send_start_button_set)

        # 发送数据的子线程结束之后，【参数修改】按钮和【发送数据】按钮恢复为可点击状态
        self.my_send_thread.send_finished.connect(lambda: self.button_send.setEnabled(True))
        self.my_send_thread.send_finished.connect(lambda: self.button_send_param.setEnabled(True))

        # 发送数据结束之后退出，之后删除相关的子线程
        self.my_send_thread.send_finished.connect(self.send_thread.quit)
        self.my_send_thread.send_finished.connect(self.my_send_thread.deleteLater)
        self.send_thread.finished.connect(self.send_thread.deleteLater)

        # 发送数据的子线程结束之后，终止用来计时的子线程，停止计时
        self.my_send_thread.send_finished.connect(self.send_timecount_stop)

        # 在界面上更新进展
        self.my_send_thread.update_tip.connect(self.send_tips_update)
        self.my_send_thread.update_messagebox.connect(self.update_messageboxes)

        # 开始线程
        send_logger.info('开始启动发送数据的子线程 ... ')
        self.send_thread.start()

    def send_time_display(self, count_time):
        """
        Func: 显示提取所用的时间
        :param count_time: 字符串，表示提取所花费的时间
        """
        self.label_send_time_display.setText(f'<font color=LightSeaGreen>{count_time}s</font>')
        self.label_send_time_display.repaint()

    def send_timecount_start(self):
        """
        Func: 开始计时, 并显示提取数据所消耗的时间
        """
        self.label_send_time_display.setText('')
        self.send_timecount.counting.connect(self.send_time_display)
        self.send_timecount.start()

    def send_timecount_stop(self):
        """
        Func: 停止计时的子线程，之后退出提取数据的子线程
        """
        self.send_timecount.stop()
        self.send_thread.quit()
        self.send_thread.wait()

    def send_stop_button_set(self):
        """
        Func: 设置【停止发送】按钮的相关状态，解绑和绑定对应的槽函数
        """
        if self.button_send_param.text() == '参数检查 (Q)':
            return

        self.button_send.setText("停止发送 (T)")
        self.button_send.setShortcut(QKeySequence("Alt+t"))
        self.button_send.clicked.disconnect()
        # 点击【停止发送】之后，通过 调用send_stop()函数 将发送数据标志位 SendParam.send_flag 置为False
        # Note: 调用 send_stop 槽函数的操作必须在调用 send_start_button_set 之前，否则对send_stop的调用不生效
        self.button_send.clicked.connect(send_stop)
        self.button_send.clicked.connect(self.send_start_button_set)

    def send_start_button_set(self):
        """
        Func: 发送按钮 从【停止发送】恢复成【发送数据】的状态设置，解绑和绑定对应的槽函数
        """
        if self.button_send_param.text() == '参数检查 (Q)':
            send_logger.warning('请先进行参数检查！')
            self.send_tips_update('OrangeRed', '请先进行参数检查！')
            self.update_messageboxes('警告', '请先进行参数检查！')
            return

        self.button_send.setText("发送数据 (S)")
        self.button_send.setShortcut(QKeySequence("Alt+s"))
        self.button_send.clicked.disconnect()
        self.button_send.clicked.connect(self.send_data_start)
        self.button_send.clicked.connect(self.send_stop_button_set)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main = DroneDataProcessor()
    main.show()

    sys.exit(app.exec_())
