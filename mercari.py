import json
import os
import threading
import time
import requests
import urllib
import random
import queue
import sys

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from win11toast import toast

import tkinter as tk
from tkinter import ttk

# 全局停止事件：一旦设置，就会要求所有线程停止
stop_event = threading.Event()

# 用于线程和GUI之间传递日志信息的队列: 元素是 (search, message)
log_queue = queue.Queue()

# 记录所有关键词 -> 其线程对象
monitor_threads = {}

# 记录关键词 -> Text 控件，用于单独显示输出
text_widgets = {}

# Selenium Chrome配置
chrome_options = Options()
chrome_options.add_argument("--headless=new")  # 无头模式
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--blink-settings=imagesEnabled=false")  # 禁用图片加载
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--log-level=3")
chrome_options.add_argument("--allow-insecure-localhost")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


def log_print(search, *args):
    """
    模拟 print() 的效果，把要打印的文本组合起来，
    并带上关键词标记存入队列，后续 GUI 再分发到对应 Text 控件。
    """
    message = " ".join(str(arg) for arg in args)
    log_queue.put((search, message))


def get_base_dir():
    if getattr(sys, "frozen", False):
        # 如果是打包后的 exe，返回 exe 所在目录
        return os.path.dirname(sys.executable)
    else:
        # 否则是源码运行，返回当前 .py 文件所在目录
        return os.path.dirname(os.path.abspath(__file__))


# 图象缓存处理
def download_image(image_url, search):
    try:
        temp_dir = os.path.join(get_base_dir() + "/" + search, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        original_name = (
            urllib.parse.urlparse(image_url).path.split("/")[-1].split("?")[0]
        )
        if "@webp" in original_name.lower():
            base_name = original_name.lower().replace("@webp", "")
            if not base_name.endswith(".webp"):
                base_name = os.path.splitext(base_name)[0] + ".webp"
            image_name = base_name
        else:
            if original_name.lower().endswith(".webp"):
                image_name = original_name
            else:
                image_name = original_name

        image_path = os.path.join(temp_dir, image_name)
        if os.path.exists(image_path):
            return os.path.abspath(image_path)

        response = requests.get(image_url, stream=True)
        if response.status_code == 200:
            with open(image_path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
        else:
            log_print(search, "⚠️图片缓存失败:", response.status_code)
            return None

        with Image.open(image_path) as img:
            target_width = 453
            target_height = 223
            width_ratio = target_width / img.width
            height_ratio = target_height / img.height
            scale_ratio = min(width_ratio, height_ratio)
            new_size = (int(img.width * scale_ratio), int(img.height * scale_ratio))
            resized_img = img.resize(new_size, Image.Resampling.LANCZOS)

            canvas = Image.new("RGB", (target_width, target_height), (255, 255, 255))
            paste_position = (
                (target_width - resized_img.width) // 2,
                (target_height - resized_img.height) // 2,
            )
            canvas.paste(resized_img, paste_position)
            canvas.save(image_path)

        return os.path.abspath(image_path)

    except Exception as e:
        log_print(search, "❌下载图片出错:", e)
        return None


# 构造系统消息
def send_toast_notification(title, message, image, link, search):
    image_path = download_image(image, search)
    imagere = {"src": image_path, "placement": "hero"}
    toast(title, message, image=imagere, on_click=link)


# 页面加载判断
def get_redirected_url(driver, search):
    try:
        WebDriverWait(driver, 15).until(EC.url_contains("search_condition_id="))
        return driver.current_url
    except:
        log_print(search, f"⚠️页面没有跳转，可能没找到宝贝或被拦截(>_<)")
        return None


# 模拟滑动加载
def scroll_until_all_loaded(driver, search):
    scroll_pause_time = 0.3
    while True:
        # 滚动到底部（强制）
        driver.execute_script("window.scrollBy(0, 2000);")
        time.sleep(scroll_pause_time)
        if (
            len(driver.find_elements(By.CSS_SELECTOR, ".merItemThumbnail"))
            >= len(driver.find_elements(By.CSS_SELECTOR, '[data-testid="item-cell"]'))
            and len(driver.find_elements(By.CSS_SELECTOR, '[data-testid="item-cell"]'))
            != 0
        ):
            log_print(search, "✅ 商品列表加载完成~")
            break


# 主循环
def get_search_url(search, stop_event, min_delay, max_delay):
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )
    driver.set_window_size(888, 5000)
    # 启用 CDP
    driver.execute_cdp_cmd("Network.enable", {})
    # 设置请求拦截规则
    driver.execute_cdp_cmd(
        "Network.setBlockedURLs",
        {"urls": ["*.css", "*.woff", "*.ttf"]},
    )
    search_url = f"https://jp.mercari.com/search?keyword={urllib.parse.quote(search)}&sort=created_time&order=desc"

    while not stop_event.is_set():
        # 读取旧数据
        os.makedirs(get_base_dir() + "/" + search, exist_ok=True)
        json_path = os.path.join(get_base_dir(), search, "mercari_data.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                old_items = json.load(f)
        else:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
            old_items = []

        old_item_ids = {item["id"] for item in old_items}
        old_items_dict = {item["id"]: item for item in old_items}
        #########################################################################
        try:
            log_print(search, f"\n(=^･ω･^=) 刷新页面，嗅探新商品…")
            driver.get(search_url)
            # 检查并设置排序方式
            try:
                select_element = WebDriverWait(driver, 5)
                select = Select(select_element)
                current_value = select.first_selected_option.get_attribute("value")
                if current_value != "created_time:desc":
                    log_print(search, f"🔄 切换排序方式为最新(๑>ᴗ<๑)")
                    select.select_by_value("created_time:desc")
                else:
                    log_print(search, f"✅ 已经是最新排序啦~")
            except Exception as e:
                log_print(search, f"✅ 已经是最新排序啦~")  # 不管了（倒下）
            scroll_until_all_loaded(driver, search)
            items = driver.find_elements(By.CSS_SELECTOR, 'li[data-testid="item-cell"]')
            ittms = driver.find_elements(By.CSS_SELECTOR, ".merItemThumbnail")

            log_print(search, f"📦 共发现 {len(items)} 个商品块")
            log_print(search, f"📦 实际加载 {len(ittms)} 个商品块")

            # 替换原搜索链接
            current_search_url = get_redirected_url(driver, search)
            search_url = current_search_url if current_search_url else search_url

            new_items = []

            # 读取新数据

            for item in items:
                try:
                    thumb_div = item.find_element(By.CSS_SELECTOR, ".merItemThumbnail")
                    item_id = thumb_div.get_attribute("id")
                    item_price = item.find_element(
                        By.CSS_SELECTOR, ".number__6b270ca7"
                    ).text.strip()
                    img_element = item.find_element(By.CSS_SELECTOR, "img")
                    img_url = img_element.get_attribute("src")
                    item_link = item.find_element(
                        By.CSS_SELECTOR, 'a[data-testid="thumbnail-link"]'
                    ).get_attribute("href")
                    item_name = item.find_element(
                        By.CSS_SELECTOR, ".imageContainer__f8ddf3a2"
                    ).get_attribute("aria-label")

                    new_items.append(
                        {
                            "id": item_id,
                            "name": item_name,
                            "price": item_price,
                            "image": img_url,
                            "link": item_link,
                        }
                    )
                except Exception as e:
                    continue

            new_item_ids = {item["id"] for item in new_items}
            added_items = [item for item in new_items if item["id"] not in old_item_ids]

            # 新商品提示
            if added_items:
                log_print(search, f"🎉发现{len(added_items)}个新宝贝喵~！")
                for item in added_items:
                    log_print(search, f"🌸【{item['name']}】 - {item['price']}円")
                    log_print(search, f"📸 图片链接: {item['image']}")
                    log_print(search, f"🔗 详情页: {item['link']}\n")
                    # 发送系统通知
                    send_toast_notification(
                        f"关键词 {search} 🎉新宝贝提醒喵~",
                        f"{item['name']} - {item['price']}",
                        item["image"],
                        item["link"],
                        search,
                    )
                old_item_ids.update(new_item_ids)

            # 检查降价
            for item in new_items:
                if item["id"] in old_items_dict:
                    try:
                        old_price = float(
                            old_items_dict[item["id"]]["price"]
                            .replace("円", "")
                            .replace(",", "")
                            .strip()
                        )
                        new_price = float(
                            item["price"].replace("円", "").replace(",", "").strip()
                        )
                        if new_price < old_price:
                            log_print(
                                search,
                                f"宝贝【{item['name']}】降价: {old_price}円 → {new_price}円(≧ω≦)",
                            )
                            send_toast_notification(
                                f"关键词{search}\t💰降价警报喵！",
                                f"{item['name']} \n{old_price}円 → {new_price}円",
                                item["image"],
                                item["link"],
                                search,
                            )
                    except Exception as e:
                        log_print(search, f"⚠️价格转换失败: {e}")

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    list({d["id"]: d for d in old_items + new_items}.values()),
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

        except Exception as e:
            log_print(search, f"❌发生错误，也许是网络问题呢？绝对不是本喵的错！")

        delay = random.uniform(min_delay, max_delay)
        log_print(search, f"下次检查将在 {delay} 秒后…\n")
        time.sleep(delay)

    # 循环退出后，关闭浏览器
    driver.quit()
    log_print(search, f"监控线程已结束。")


class MercariGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mercari监控")
        self.geometry("900x600")
        # 底部区域
        down_frame = tk.Frame(self)
        down_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        tk.Label(down_frame, text="最小间隔(秒):").pack(side=tk.LEFT)
        self.entry_min_delay = tk.Entry(down_frame, width=10)
        self.entry_min_delay.pack(side=tk.LEFT, padx=5)
        tk.Label(down_frame, text="最大间隔(秒):").pack(side=tk.LEFT)
        self.entry_max_delay = tk.Entry(down_frame, width=10)
        self.entry_max_delay.pack(side=tk.LEFT, padx=5)

        # 顶部区域：添加关键词 + 按钮
        top_frame = tk.Frame(self)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="新关键词:").pack(side=tk.LEFT)
        self.entry_search = tk.Entry(top_frame, width=20)
        self.entry_search.pack(side=tk.LEFT, padx=5)

        btn_add = tk.Button(top_frame, text="添加", command=self.add_search_tab)
        btn_add.pack(side=tk.LEFT, padx=5)

        btn_start = tk.Button(top_frame, text="开始监控", command=self.start_all)
        btn_start.pack(side=tk.LEFT, padx=5)

        btn_stop = tk.Button(top_frame, text="停止所有", command=self.stop_all)
        btn_stop.pack(side=tk.LEFT, padx=5)

        # Notebook，用于多个标签页，每个搜索关键词一个 Text
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 定时刷新队列日志
        self.after(100, self.poll_log_queue)

    def add_search_tab(self):
        """
        从输入框获取新的搜索关键词，添加到 Notebook 中
        """
        search = self.entry_search.get().strip()
        if not search:
            return  # 空输入直接忽略

        if search in text_widgets:
            # 已经存在相同标签页了
            return

        # 创建一个新的标签页
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text=search)

        # 在标签页里放一个 Text
        text_area = tk.Text(frame, wrap="word")
        text_area.pack(fill=tk.BOTH, expand=True)

        # 记录到全局字典，便于后续写日志
        text_widgets[search] = text_area

        self.entry_search.delete(0, tk.END)  # 清空输入

    def start_all(self):
        """
        为所有还没启动的关键词创建并启动一个线程
        """
        for search in text_widgets.keys():
            stop_event.clear()
            if search not in monitor_threads:
                try:
                    min_delay = float(self.entry_min_delay.get().strip())
                    max_delay = float(self.entry_max_delay.get().strip())
                    if min_delay > max_delay:
                        min_delay, max_delay = max_delay, min_delay
                except ValueError:
                    min_delay = 0.05
                    max_delay = 1.00
                    log_print(
                        search,
                        "⚠️主人，这样输入的时间间隔才不对啦，我直接用0.05到1.00秒了哦！",
                    )
                # 创建并启动该关键词的线程
                t = threading.Thread(
                    target=get_search_url,
                    args=(search, stop_event, min_delay, max_delay),
                    daemon=True,
                )
                monitor_threads[search] = t
                t.start()

    def stop_all(self):
        """
        设置 stop_event
        """
        stop_event.set()
        # 也可以在这里等待线程结束
        for search, t in monitor_threads.items():
            if t.is_alive():
                # 等待线程自己退出
                t.join(timeout=1)
        monitor_threads.clear()  # 清空线程记录
        self.log_to_text("(所有线程请求停止)")

    def log_to_text(self, message, search=None):
        """
        辅助函数：将 message 插入到指定 search 的 Text 中；
        若 search=None，就统一插到当前激活页的 Text，或做其它处理
        """
        if not search:
            # 默认插到当前页
            current_tab = self.notebook.select()
            if not current_tab:
                return
            # 可能需要倒查找 "tab_id -> search" 的对应关系，这里简单演示
            # 直接插到所有tab里
            for s, text_area in text_widgets.items():
                text_area.insert(tk.END, message + "\n")
                text_area.see(tk.END)
        else:
            text_area = text_widgets.get(search)
            if text_area:
                text_area.insert(tk.END, message + "\n")
                text_area.see(tk.END)

    def poll_log_queue(self):
        """
        定时从 log_queue 取出 (search, message)，插到对应的 Text
        """
        while not log_queue.empty():
            search, msg = log_queue.get_nowait()
            self.log_to_text(msg, search)

        # 继续安排下一轮
        self.after(100, self.poll_log_queue)

    def on_closing(self):
        """
        窗口关闭时，停止所有线程然后关闭
        """
        stop_event.set()
        for search, t in monitor_threads.items():
            if t.is_alive():
                t.join(timeout=1)
        self.destroy()


if __name__ == "__main__":
    app = MercariGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
