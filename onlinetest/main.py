import os
import time
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
from PIL import Image


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
        self.feishu_webhook = self._get("FEISHU_WEBHOOK")

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
        self.screenshot_path = "/tmp/online_hd.png"
        self.full_path = "/tmp/full.png"

    def start(self):
        options = Options()
        options.binary_location = self.config.chrome_binary

        # ===== 核心高清优化 =====
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=2560,1440")
        options.add_argument("--force-device-scale-factor=2")
        options.add_argument("--high-dpi-support=1")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)

        # ===== CDP 强制高清 =====
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 1920,
            "height": 1080,
            "deviceScaleFactor": 2,
            "mobile": False
        })

        self.driver.get("https://gamebi.moontontech.net/projectmlbb/realtime/online")

        logging.info("浏览器启动成功（高清模式）")

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

        # 滚动确保渲染完成
        self.driver.execute_script("arguments[0].scrollIntoView(true);", canvas)
        time.sleep(1)

        # ===== 1. 整页高清截图 =====
        self.driver.save_screenshot(self.full_path)

        # ===== 2. 精确裁剪 canvas =====
        location = canvas.location
        size = canvas.size

        img = Image.open(self.full_path)

        scale = 2  # 高清关键参数

        left = int(location["x"] * scale)
        top = int(location["y"] * scale)
        right = int((location["x"] + size["width"]) * scale)
        bottom = int((location["y"] + size["height"]) * scale)

        img = img.crop((left, top, right, bottom))
        img.save(self.screenshot_path, quality=100)

        logging.info(f"高清截图完成: {self.screenshot_path}")

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

    def send_image(self, path):
        token = self.get_token()

        with open(path, "rb") as f:
            res = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                files={"image": f},
                data={"image_type": "message"}
            )

        res.raise_for_status()
        image_key = res.json()["data"]["image_key"]

        payload = {
            "msg_type": "image",
            "content": {"image_key": image_key}
        }

        requests.post(self.config.feishu_webhook, json=payload)

        logging.info("飞书发送成功（高清图）")


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