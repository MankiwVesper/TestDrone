# -*- encoding: utf-8 -*-
import logging
import colorlog
import time


def get_logger(logger_flag, log_path):
    logger = logging.getLogger(logger_flag)
    logger.setLevel(logging.DEBUG)

    date_format = "%Y-%m-%d %H:%M:%S"

    # 保存到日志文件中的log
    log_file = f'{log_path}/{logger_flag}-{time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())}.log'
    file_handler = logging.FileHandler(log_file)

    # 不适用颜色标记日志等级，直接使用文字标明日志级别
    file_log_format = "%(asctime)s.%(msecs)03d\t%(filename)s:%(lineno)d\t%(funcName)s\t【%(levelname)s】%(message)s"
    file_formatter = logging.Formatter(file_log_format, datefmt=date_format)

    # 设置保存到日志文件的日志格式
    file_handler.setFormatter(file_formatter)

    # 打印到控制台中的日志
    control_handler = logging.StreamHandler()

    # 不同级别的日志显示不同的颜色，通过 colorlog 模块实现
    control_log_format = "%(log_color)s%(asctime)s.%(msecs)03d\t%(filename)s:%(lineno)d\t%(message)s"
    control_formatter = colorlog.ColoredFormatter(control_log_format, datefmt=date_format)

    # 设置打印在控制台中的日志格式
    control_handler.setFormatter(control_formatter)

    # 分别添加两个日志控制器
    logger.addHandler(control_handler)
    logger.addHandler(file_handler)

    return logger
