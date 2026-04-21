#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import datetime
import logging
from apscheduler.schedulers.background import BackgroundScheduler

# 配置日志输出到文件和控制台
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_scheduler.log'),   # 写入文件
        logging.StreamHandler()                      # 同时输出到终端
    ]
)

def my_job():
    """要定时执行的任务"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"定时任务执行了！当前时间：{now}")

def main():
    logging.info("测试脚本启动...")
    
    # 创建后台调度器
    scheduler = BackgroundScheduler()
    
    # 添加一个定时任务：每隔 30 秒执行一次 my_job（方便快速观察效果）
    scheduler.add_job(my_job, 'interval', seconds=30)
    
    # 如果你希望每天在特定时间运行（比如 09:00），可以把上面那行注释掉，取消下面这行的注释
    # scheduler.add_job(my_job, 'cron', hour=9, minute=0)
    
    scheduler.start()
    logging.info("调度器已启动，按 Ctrl+C 可停止脚本")
    
    try:
        # 让主线程保持运行，否则脚本会立即退出
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("收到停止信号，正在关闭调度器...")
        scheduler.shutdown()
        logging.info("脚本已停止")

if __name__ == "__main__":
    main()