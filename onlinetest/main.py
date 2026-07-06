import os
import re
import time
import logging
import requests
import pyotp
from pathlib import Path

from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.driver_cache import DriverCacheManager


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
    # 临时文件根目录: 优先 ONLINETEST_TMP_DIR 环境变量, 否则放到当前用户 ~/.cache/onlinetest/
    # 避免和 root 之前留下的 /tmp/*.png 冲突 (root 拥有的文件 non-root 无权覆盖)
    TMP_DIR = Path(os.environ.get("ONLINETEST_TMP_DIR", Path.home() / ".cache" / "onlinetest"))
    WDM_DIR = TMP_DIR / "wdm"             # webdriver_manager 缓存
    PROFILE_DIR = TMP_DIR / "chrome-profile"  # Chrome 用户数据目录

    def __init__(self, config):
        self.config = config
        self.driver = None
        for d in (self.TMP_DIR, self.WDM_DIR, self.PROFILE_DIR):
            d.mkdir(parents=True, exist_ok=True)

        self.screenshot_path = str(self.TMP_DIR / "online_hd.png")

    def start(self):
        options = Options()
        options.binary_location = self.config.chrome_binary

        # ================== 高清核心配置 ==================
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        # 🔥 4K viewport（关键提升）
        options.add_argument("--window-size=3840,2160")

        # 🔥 DPI 3x（提升清晰度核心）
        options.add_argument("--force-device-scale-factor=3")
        options.add_argument("--high-dpi-support=1")

        # 仅保留有效的非 root 沙箱参数
        # (--disable-dbus / --disable-machine-id / --disable-remote-display 不是真实的 Chrome flag)
        options.add_argument("--disable-setuid-sandbox")

        # Chrome 用户数据目录定向到当前用户可写位置, 避免默认 ~/.config/google-chrome/ 权限问题
        options.add_argument(f"--user-data-dir={self.PROFILE_DIR}")


        # webdriver-manager 4.x 的方式: 通过 cache_manager 把驱动缓存指向自定义目录
        # (旧版 WDM_CACHE_PATH 环境变量在 4.x 已失效)
        cache_mgr = DriverCacheManager(root_dir=str(self.WDM_DIR))
        service = Service(ChromeDriverManager(cache_manager=cache_mgr).install())
        self.driver = webdriver.Chrome(service=service, options=options)

        # ================== CDP 强制高清 ==================
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": 1920,
            "height": 1080,
            "deviceScaleFactor": 3,
            "mobile": False
        })

        # Profile 目录跨次运行复用(为了保留登录态跳过重复登录), 但这会让 Chrome 磁盘缓存也一起持久化,
        # 导致图表数据接口可能命中旧的缓存响应而非拿到最新数据。禁用 HTTP 缓存不影响 cookie/登录态。
        self.driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})

        self.driver.get("https://decismart.bi.moontontech.net/mlbb_real_time/monitor")

        logging.info("浏览器启动成功（4K + 3x高清模式）")

    def _dump_state(self, label="error"):
        """出错时dump screenshot + HTML + URL, 用于离线排查"""
        try:
            ts = int(time.time())
            safe_label = re.sub(r"[^A-Za-z0-9_-]", "_", label)
            png = self.TMP_DIR / f"debug_{safe_label}_{ts}.png"
            html = self.TMP_DIR / f"debug_{safe_label}_{ts}.html"
            self.driver.save_screenshot(str(png))
            with open(html, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logging.error(f"[DEBUG] URL={self.driver.current_url}")
            logging.error(f"[DEBUG] Title={self.driver.title}")
            logging.error(f"[DEBUG] Screenshot: {png}")
            logging.error(f"[DEBUG] HTML dump:  {html}")
        except Exception as e:
            logging.error(f"[DEBUG] dump 失败: {e}")

    def _click(self, by, value, timeout=20):
        try:
            el = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((by, value))
            )
        except Exception:
            logging.error(f"_click 超时: ({by}, {value})")
            self._dump_state(f"click_{value[:30]}")
            raise
        self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
        time.sleep(0.3)
        el.click()

    def _input(self, by, value, text, timeout=20):
        try:
            el = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
        except Exception:
            logging.error(f"_input 超时: ({by}, {value})")
            self._dump_state(f"input_{value[:30]}")
            raise
        el.clear()
        el.send_keys(text)

    def login(self):
        # 等待: 出现登录页 (.tab-item) 或已经登录后的 dashboard ("实时在线"图表卡片已挂载)
        # 内网若 SSO/证书自动鉴权会直接跳到 dashboard, 这种情况整个登录步骤跳过
        DASHBOARD = (By.ID, "monitor-chart-rtm_inc_online")
        LOGIN_TAB = (By.CSS_SELECTOR, ".tab-item")
        try:
            WebDriverWait(self.driver, 60).until(
                EC.any_of(
                    EC.presence_of_element_located(DASHBOARD),
                    EC.presence_of_element_located(LOGIN_TAB),
                )
            )
        except Exception:
            logging.error("60s 内既未渲染登录页也没渲染 dashboard")
            self._dump_state("login_init")
            raise

        # 已登录 -> 直接返回
        if self.driver.find_elements(*DASHBOARD):
            logging.info("已检测到登录态(dashboard 已挂载), 跳过登录步骤")
            return

        # 走登录流程
        time.sleep(1)  # 让 Vue 完成首次重绘
        self._click(By.CSS_SELECTOR, ".tab-item:nth-child(2)")
        self._input(By.CSS_SELECTOR, ".el-form-item:nth-child(1) .el-input__inner", self.config.email)
        self._input(By.CSS_SELECTOR, ".el-form-item:nth-child(2) .el-input__inner", self.config.password)
        self._click(By.CSS_SELECTOR, ".moa-login-btn")

        code = pyotp.TOTP(self.config.mfa_secret).now()
        self._input(By.CLASS_NAME, "mfa-code", code)
        self._click(By.CLASS_NAME, "mfa-submit")
        logging.info("登录完成")

    def capture(self):
        # 登录完成后图表数据仍需时间加载, 固定等待 1 分钟避免截到 loading 状态
        time.sleep(60)

        panel = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.ID, "monitor-chart-rtm_inc_online"))
        )
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", panel)

        WebDriverWait(self.driver, 30).until(
            lambda d: panel.find_elements(By.TAG_NAME, "canvas")
        )
        # 等待图表自身的加载动画消失, 确保数据渲染完成
        WebDriverWait(self.driver, 30).until(
            lambda d: not panel.find_elements(By.CSS_SELECTOR, ".ant-spin-spinning")
        )
        time.sleep(2)

        chart = panel.find_element(By.XPATH, './/div[contains(@class, "chart-container")]')
        chart.screenshot(self.screenshot_path)

        logging.info(f"超清截图完成: {self.screenshot_path}")

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

        logging.info("飞书发送成功（超清图）")


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