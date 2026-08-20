# -*- encoding:utf-8 -*-

"""
名称：无人机以太网报文提取工具(Windows平台)
目的：在项目中，软件会包含接收并显示无人机视频和飞控信息的功能。而在测试过程中通常需要给软件发送视频数据和飞控数据；
     该工具就是从抓取的原始数据（通过 wireshark、tcpdump等工具抓取到的原始以太网数据——pcapng/pcap格式）中提取中无人机视频数据和飞控数据
版本：V5.0
更新：增加监控提取过程的子线程，接到命令后，停止提取线程，不再使用全局变量
作者：mankiw
邮件：mankiw007@outlook.com
时间：2023/6/5~2023/6/9
"""

from datetime import datetime
from os import path, system
from subprocess import Popen
from threading import Thread

from log_setting import get_logger


class ExtractParam:
    """
    Func: 定义一个类，用来保存变量，这些类变量的值可以在不同的Python脚本之间共享
    """
    extract_flag = True      # 是否提取数据的标志位
    code = None  # 保存提取数据的子进程的返回码


class Extractor(object):
    """
    Func: 从原始数据中提取无人机的视频数据和飞控数据，并保存到指定文件中，返回数据文件的路径
    """
    def __init__(self, tool, raw_data, data_range=None):
        """
        :param tool: 提取数据工具的路径，一般使用 tshark
        :param raw_data: 保存原始数据的文件的绝对路径，一般是用 .pcap/.pcapng 格式的文件
        :param data_range:  使用 tshark 提取UDP数据的范围，可以是 src_ip:src_port,dst_ip:dst_port，也可以是UDP会话的序号(从0开始)
        """
        # 确认 tshark.exe 的路径
        if path.exists(tool):
            extract_logger.info(f'提取工具的路径为 {tool}。')
            self.tool = f'"{tool}"'
        else:
            extract_logger.error('系统中没有找到指定的工具，请检查！')
            return

        # 确认原始数据文件
        if path.exists(raw_data):
            # 给路径外层嵌套一对双引号，防止路径中存在空格，导致执行时无法正确判断路径
            self.raw_data = f'"{raw_data}"'
        else:
            extract_logger.error(f'原数据文件 {raw_data} 不存在，请检查!')
            return

        # 确认UDP数据的提取范围
        if not data_range:
            # data_range 默认为 0，表示没有指定提取范围 data_range 的情况下，默认提取UDP数据流中的第一个会话数据
            extract_logger.warning('没有指定UDP数据的提取范围，默认提取UDP数据流中的第一个会话数据，请知悉！')
            self.data_range = '0'
        else:
            # data_range 的典型格式为 ‘192.168.0.163:5000,226.0.0.80:8001’，表示提取 192.168.0.163:5000 和 226.0.0.80:8001之间的数据
            self.data_range = str(data_range)

        # 构造提取数据的命令 —— 该命令表示 使用tshark提取 源地址和目的地址之间的UDP数据流
        self.extract_command = ' '.join([f'{self.tool}', '-r', f'{self.raw_data}', f'-qz follow,udp,raw,{self.data_range}'])

        # 将路径中的 单斜杠 替换成 双斜杠，避免将命令中的单斜杆当做转义字符处理，而导致系统无法正确识别路径
        self.extract_command = self.extract_command.replace('\\', '\\\\')
        extract_logger.info(f'提取命令为 {self.extract_command}。')

        self.process = None             # 用来保存提取数据的子进程
        self.kill_flag = True               # 是否强制杀掉子进程的标志位

    def extract_exec(self, data_file):
        """
        Func: 执行提取数据的操作
        :param data_file: 保存提取之后的数据的文件
        """
        if path.exists(data_file):
            # 给定的文件已经存在，则在给定文件名的基础上增加当前时间戳，生成新的文件名
            path_info = path.splitext(data_file)
            time_tag = datetime.now().strftime('%Y-%m-%d-%H%M%S-%f')
            data_file = ''.join([''.join([path_info[0], '_', time_tag]), path_info[1]])
            extract_logger.warning(f'给定的目标数据文件已经存在，提取的数据将保存到 {data_file}，请知悉!')

        try:
            with open(data_file, 'w') as out_stream:
                # 通过 subprocess.Popen 执行提取命令，命令的执行结果重定向到 stdout(即保存到文件中)
                self.process = Popen(self.extract_command, shell=True, stdout=out_stream)

                # 使用communicate() 防止产生死锁：如果子进程输出了大量的数据到stdout的管道，并且达到了系统pipe的缓存大小
                # 子进程就会等待父进程读取管道；而此时如果父进程正在 wait，就会产生死锁
                # communicate() 返回一个元组 (stdoutdata, dtderrdata)
                self.process.communicate()

            # 获取状态码
            ExtractParam.code = self.process.returncode

            # 判断提取是否执行完成（子进程正常完成，返回 0）
            if ExtractParam.code == 0:
                extract_logger.info(f'数据提取完成，数据保存在{data_file}。')
            elif ExtractParam.code == 1:
                extract_logger.warning(f'数据提取终止！')
            else:
                extract_logger.error(f'状态码为：{ExtractParam.code}，数据提取过程未完成；或未生成目标文件，请检查！')
        except Exception as e:
            extract_logger.error('提取过程出现异常，请检查！')
            extract_logger.error(e)
        finally:
            # 提取完成之后，将标志位置为False
            ExtractParam.extract_flag = False
            self.kill_flag = False

    def stop_extract(self):
        """
        Func: 根据进程id杀掉进程，使用与Windows平台
        """
        while True:
            if ExtractParam.extract_flag:
                pass
            else:
                # 当提取数据的子线程自行结束，则无需强制杀掉
                if not self.kill_flag:
                    return
                try:
                    # 强制杀掉提取数据的进程
                    ret = system(f'taskkill /t /f /pid {self.process.pid}')
                    if ret == 0:
                        extract_logger.info('关闭提取数据的进程。')
                    else:
                        extract_logger.error(f'提取数据的进程未正常关闭：{ret}')
                except Exception as e:
                    extract_logger.critical("关闭提取数据的进程出现异常！")
                    extract_logger.critical(e)
                finally:
                    ExtractParam.extract_flag = True
                    break

    def extract_data(self, neat_data):
        """
        Func: 提取数据并保存到文件中
        :param neat_data: 保存提取之后的数据的文件绝对路径
        """
        # 开始执行提取命令
        extract_logger.info(f'开始使用从 {self.raw_data} 中提取数据 ... ')
        if self.data_range.isdigit():
            extract_logger.info(f'提取源文件中的第【{int(self.data_range)+1}】个UDP会话！')
        else:
            extract_logger.info(f'数据的提取范围为 {self.data_range.replace(",", " <=> ")}!')

        # 创建提取数据的子线程
        extractor = Thread(target=self.extract_exec, args=(neat_data,))
        # 创建监控提取数据的子线程，收到信号之后 停止提取数据
        monitor = Thread(target=self.stop_extract)
        # 启动子线程
        extractor.start()
        monitor.start()
        # 等待线程执行完成
        monitor.join()
        extractor.join()
        # 当提取数据的子线程结束之后，将提取标志位置为False
        if not extractor.is_alive():
            ExtractParam.extract_flag = False
            self.kill_flag = False


class App:
    def __init__(self, ex):
        self.ex = ex

    def run_extract(self, neat_data):
        """
        运行提取工具，提取数据
        :param neat_data: 提取之后的数据所保存的文件路径
        :return: 提取之后的数据所保存的文件路径；否则返回 None
        """
        return self.ex.extract_data(neat_data)


extract_logger = get_logger("Extract_Data", './log')


if __name__ == '__main__':
    # tshark 安装路径
    extractTool = r'C:\Program Files\Wireshark\tshark.exe'

    # 源数据文件
    rawDataFile = r'E:\TestTools\pythonUDP\WRJ\example.pcapng'

    # 保存结果的文件
    videoNeatData = r'E:\TestTools\pythonUDP\WRJ\VideoData.txt'
    flyControlNeatData = r'E:\TestTools\pythonUDP\WRJ\FlyControlData.txt'

    # 提取数据的范围
    flyDataRange = '192.168.0.163:4700,226.0.0.80:6091'     # 第一个UDP会话(flyDataRange -> 0)，表示飞控数据
    videoDataRange = '192.168.0.163:5000,226.0.0.80:8001'   # 第二个UDP会话(videoDataRange -> 1)，表示无人机视频数据

    # 实例化一个无人机视频数据处理工具
    # videoExtractor = Extractor(extractTool, rawDataFile, videoDataRange)

    # 实例化一个无人机飞控数据处理工具
    flyDataExtractor = Extractor(extractTool, rawDataFile, flyDataRange)

    # 开始提取数据
    app = App(flyDataExtractor)
    fly_control_neat_data_file = app.run_extract(flyControlNeatData)
