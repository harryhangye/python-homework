import os
import time
import json
import logging
import requests
import pyotp

from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager


# ================== 日志 ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# ================== 配置 ==================
class Config:
    def __init__(self, env_path="config/online.env"):
        if not os.path.exists(env_path):
            raise FileNotFoundError(f"找不到配置文件: {env_path}")

        load_dotenv(env_path)

        self.email = self._get("MOONTON_EMAIL")
        self.password = self._get("MOONTON_PASSWORD")
        self.mfa_secret = self._get("MOONTON_MFA_SECRET")

        self.feishu_app_id = self._get("FEISHU_APP_ID")
        self.feishu_app_secret = self._get("FEISHU_APP_SECRET")
        self.feishu_chat_id = self._get("FEISHU_CHAT_ID")

        self.chrome_binary = os.getenv("CHROME_BINARY_PATH", "/usr/bin/google-chrome")

    def _get(self, key):
        value = os.getenv(key)
        if not value:
            raise ValueError(f"缺少环境变量: {key}")
        return value


# ================== 浏览器 ==================
class BrowserJob:
    def __init__(self, config):
        self.config = config
        self.driver = None
        self.screenshot_path = "/tmp/online.png"

    def start(self):
        options = Options()
        options.binary_location = self.config.chrome_binary

        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=2560,1440")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)

        self.driver.get(
            "https://gamebi.moontontech.net/projectmlbb/realtime/online"
        )

        logging.info("浏览器启动成功")

    def _click(self, by, value, timeout=20):
        el = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )
        self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
        time.sleep(0.3)
        el.click()

    def _input(self, by, value, text, timeout=20):
        el = WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        el.clear()
        el.send_keys(text)

    def login(self):
        self._click(By.CSS_SELECTOR, ".tab-item:nth-child(2)")
        self._input(By.CSS_SELECTOR, ".el-form-item:nth-child(1) .el-input__inner", self.config.email)
        self._input(By.CSS_SELECTOR, ".el-form-item:nth-child(2) .el-input__inner", self.config.password)
        self._click(By.CSS_SELECTOR, ".moa-login-btn")

        code = pyotp.TOTP(self.config.mfa_secret).now()
        self._input(By.CLASS_NAME, "mfa-code", code)
        self._click(By.CLASS_NAME, "mfa-submit")

        logging.info("登录完成")

    def capture(self):
        self._click(By.XPATH, '//button[contains(.//span, "查询")]')
        time.sleep(5)

        canvas = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[contains(@id, "__qiankun_microapp")]//canvas')
            )
        )

        canvas.screenshot(self.screenshot_path)

        logging.info(f"截图完成: {self.screenshot_path}")

    def close(self):
        if self.driver:
            self.driver.quit()


# ================== 飞书 ==================
class Feishu:
    def __init__(self, config):
        self.config = config

    def get_token(self):
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        res = requests.post(url, json={
            "app_id": self.config.feishu_app_id,
            "app_secret": self.config.feishu_app_secret
        })
        res.raise_for_status()
        return res.json()["tenant_access_token"]

    def upload_file(self, token, path):
        url = "https://open.feishu.cn/open-apis/im/v1/files"

        with open(path, "rb") as f:
            res = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files={"file": f},
                data={"file_type": "stream"}
            )

        res.raise_for_status()
        data = res.json()

        if "file_key" not in data.get("data", {}):
            raise Exception(f"上传失败: {data}")

        return data["data"]["file_key"]

    def send_file(self, file_key):
        token = self.get_token()

        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

        payload = {
            "receive_id": self.config.feishu_chat_id,
            "msg_type": "file",
            "content": json.dumps({
                "file_key": file_key
            })
        }

        res = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json=payload
        )

        if res.status_code != 200:
            logging.error(f"飞书错误: {res.text}")

        res.raise_for_status()
        logging.info("飞书发送成功（高清文件）")

    def send_image(self, path):
        token = self.get_token()
        file_key = self.upload_file(token, path)
        self.send_file(file_key)


# ================== 重试机制 ==================
def retry(func, times=3):
    for i in range(1, times + 1):
        try:
            return func()
        except Exception as e:
            logging.error(f"第{i}次失败: {e}")
            if i == times:
                raise
            time.sleep(i * 5)


# ================== 主流程 ==================
def main():
    config = Config("config/online.env")

    browser = BrowserJob(config)
    feishu = Feishu(config)

    def job():
        browser.start()
        browser.login()
        browser.capture()
        feishu.send_image(browser.screenshot_path)

    try:
        retry(job)
    finally:
        browser.close()


if __name__ == "__main__":
    main()