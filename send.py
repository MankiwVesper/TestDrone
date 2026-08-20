# -*- encoding: utf-8 -*-

"""
名称：无人机以太网报文发送工具(Windows平台)
目的：在项目中，软件会包含接收并显示无人机视频和飞控信息的功能。而在测试过程中通常需要给软件发送视频数据和飞控数据；
     该工具可以指定文件中的数据以某种速率发送到某个地址和端口（不仅限于无人机数据，任何UDP数据都可以）
版本：V5.0
更新：新增一个类，用来保存是否发送数据的标志位，来和GUI界面进行交互
作者：mankiw
邮件：mankiw007@outlook.com
时间：2023/6/1
"""

import ipaddress
import socket
from os import path
from datetime import datetime
from time import sleep

from log_setting import get_logger


def ipaddr_check(address):
    """
    确认以元组形式输入的IP地址和端口号是否符合要求
    :param address: address = (ip_address, port) -> ('192.168.0.8', 8888)
    :return: 如果address的格式符合要求，则返回 address；否则返回 False
    """
    # 确认源地址和端口号
    if isinstance(address, tuple):
        src_ip = address[0]
        src_port = address[1]

        # 判断IP地址是否正确
        try:
            ipaddress.IPv4Address(src_ip)
        except ValueError as e:
            send_logger.error(f'输入的IP地址【{src_ip}】错误，请检查！')
            send_logger.error(e)
            return False

        # 判断端口号是否正确
        try:
            src_port = int(src_port)
            if 1024 <= src_port <= 65535:
                return src_ip, int(src_port)
            else:
                send_logger.error(f'输入的端口号【{src_port}】范围错误(1024~65535)，请检查！')
                return False
        except Exception as e:
            send_logger.error(f'输入的端口号【{src_port}】格式错误(要求int型)，请检查！')
            send_logger.error(e)
            return False
    else:
        send_logger.error(f'输入的地址【{address}】不是元组格式，请检查！')
        return False


class SendParam:
    """
    Func: 用来保存 是否发送数据的标志位，通过在GUI界面上操作该变量，可以控制是否发送脚本
    """
    send_flag = True


class Sender(object):
    """
    Func: 发送UDP报文到对应的地址和端口
    """
    def __init__(self, src_address, dst_address, inter_time):
        # 确认源地址和端口
        self.src_address = ipaddr_check(src_address)

        # 确认目的地址和端口
        self.dst_address = ipaddr_check(dst_address)

        # 确认两条命令之间的发送时间间隔
        if isinstance(inter_time, (int, float)) and inter_time > 0:
            self.inter_time = inter_time
        else:
            send_logger.error(f'输入的时间间隔【{inter_time}】错误，请检查！')

    @staticmethod
    def get_command_from_file(data_file):
        """
        打开存放命令的文件, 将命令存放到列表中, 最后返回该列表
        :param data_file: 存放原始数据的文件路径
        :return: 存放有效数据的列表
        """
        # 确认保存数据的文件路径
        if not path.exists(data_file):
            send_logger.error(f'数据文件 {data_file} 不存在，请检查！')
            return

        # 打开数据文件，逐行读取
        try:
            with open(data_file, 'r', encoding='utf-8') as file_object:
                command_list = file_object.read().splitlines()
        except PermissionError as e:
            send_logger.error(f'没有权限访问{data_file}, 请检查！')
            send_logger.error(e)
            return
        except Exception as e:
            send_logger.error('其他异常, 请检查！')
            send_logger.error(e)
            return

        # 去掉包含非数字字母的数据行
        useful_command_list = [command for command in command_list if command.isalnum()]

        return useful_command_list

    def send_command(self, command_list, times=0):
        """
        Func: 发送列表中的UDP报文到指定的地址和端口
        :param flag: 是否发送数据的标志位：Ture表示发送，False表示不发送
        :param times: 发送的次数，默认为0 表示循环发送
        :param command_list: 存放命令的列表
        """
        if not command_list:
            send_logger.error('输入的命令列表为空，请检查！')
            return

        if isinstance(times, int):
            if times < 0:
                send_logger.error('循环次数不能小于0（0表示无限循环发送）！')
                return
        else:
            send_logger.error('循环次数必须为正整数，请确认！')
            return

        # 创建一个 udp 套接字
        # 第一个参数 socket.AF_INET 表示满足IP地址协议
        # 第二个参数 socket.SOCK_DGRAM 表示创建的socket完成UDP协议
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.settimeout(2)                                            # 超时时间 2s
            udp_socket.bind(self.src_address)                                # 绑定发送端的port, 参数类型为元组
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

        command_num = 1                                                              # 发送命令的序号
        total_num = len(command_list)                                            # 命令列表中元素的个数
        send_logger.info(f'开始发送数据到 {self.dst_address} ... ')        # 循环发送列表中的命令

        # times为0 表示无限循环发送列表中的数据
        if times == 0:
            times = True

        while times and SendParam.send_flag:
            for i in range(total_num):
                time1 = datetime.now()                                                                # 记录此刻时间为 time1

                send_logger.info(' '.join([str(command_num), command_list[i]]))    # 记录发送的数据
                byte_hex_command = bytes.fromhex(command_list[i])                  # 以十六进制的方式读取列表中的元素，并将数据转换成字节形式
                udp_socket.sendto(byte_hex_command, self.dst_address)              # 将数据发送到指定的地址和端口
                command_num += 1                                                                    # 发送成功后，命令数加一

                time2 = datetime.now()                                                                # 记录此刻时间为 time2
                delta_time = time2 - time1                                                           # 计算从 time1 到 time2 所消耗的时间

                # 精确计算发送两条命令之间的间隔时间
                # 用指定的间隔时间 减去 两次发送命令的中间过程所消耗的时间，得到此次等待的时间
                delta = self.inter_time - delta_time.microseconds / 1000000

                # 如果 delta 小于0，说明两次发送命令中间所消耗的时间 已经大于 指定的时间间隔，则不能再等待，而直接发送下一条接口
                if delta >= 0:
                    sleep(delta)

                if not SendParam.send_flag:
                    return 'break'

            if times is True:
                continue            # 无限循环发送
            else:
                times -= 1

        # 数据发送完成后，关闭 socket
        udp_socket.close()
        return True


class App:
    def __init__(self, sender):
        self.sender = sender

    def run_sender(self, data_file):
        """
        发送文件中的数据到指定的地址
        :param data_file: 保存处理之后的数据的文件路径
        """
        data_list = self.sender.get_command_from_file(data_file)
        self.sender.send_command(data_list, 2)


send_logger = get_logger("Send_Data", './log')


if __name__ == '__main__':
    # 保存数据的文件路径
    data = r'E:\TestTools\TestDrone\V5.0\data\cc.txt'

    # 用来发送数据的源地址和端口
    source_address = ('10.100.25.67', 6789)

    # 接收数据的目的地址和端口
    destination_address = ('226.0.0.80', 6091)

    # 数据发送间隔(单位：秒)
    deltaTime = 1

    # 实例化发送器
    flyControlDataSender = Sender(source_address, destination_address, deltaTime)
    # videoDataSender = Sender(source_address, destination_address, delta_time)

    # 开始发送数据
    app = App(flyControlDataSender)
    app.run_sender(data)
