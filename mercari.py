import json
import os
import threading
import time
import requests
import urllib
import random
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from win11toast import toast
from concurrent.futures import ThreadPoolExecutor


# 要监视的搜索关键词
searches = ["クドわふたー", "ブルアカ"]


######################################################################################
# 设置 Chrome 选项
chrome_options = Options()
chrome_options.add_argument("--headless")  # 无头模式
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--log-level=3")
chrome_options.add_argument("--allow-insecure-localhost")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


# **检查是否跳转到 search_condition_id= 页面**
def get_redirected_url(driver, search):
    try:
        WebDriverWait(driver, 15).until(EC.url_contains("search_condition_id="))
        return driver.current_url
    except Exception:
        print(
            f"关键词{search}\t ⚠️喵~页面没有跳转哦，可能是没有找到宝贝或者被拦住了捏(>_<)"
        )
        return None


# **通知函数**
def download_image(image_url, search):
    """下载并调整图片尺寸，防止 Windows 11 Hero Image 裁剪，增加对 webp 及 @webp 的支持"""
    try:
        # 创建临时目录
        TEMP_DIR = os.path.join(search, "temp")
        os.makedirs(TEMP_DIR, exist_ok=True)

        # 解析原始文件名
        original_name = (
            urllib.parse.urlparse(image_url).path.split("/")[-1].split("?")[0]
        )

        # 如果文件名中带有 @webp 或以 .webp 结尾，则统一改成 .webp
        if "@webp" in original_name.lower():
            # 有些文件名可能是 ***.jpg@webp 这种形式
            # 这里将其统一改为 ***.webp
            base_name = original_name.lower().replace("@webp", "")
            if not base_name.endswith(".webp"):
                base_name = os.path.splitext(base_name)[0] + ".webp"
            image_name = base_name
        else:
            # 若末尾直接为 .webp
            if original_name.lower().endswith(".webp"):
                image_name = original_name
            else:
                # 否则保持原名称
                image_name = original_name

        image_path = os.path.join(TEMP_DIR, image_name)

        # 如果图片已存在，则直接返回路径
        if os.path.exists(image_path):
            return os.path.abspath(image_path)

        # 下载图片
        response = requests.get(image_url, stream=True)
        if response.status_code == 200:
            with open(image_path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
        else:
            print("⚠️图片缓存失败了喵~:", response.status_code)
            return None

        # 使用 Pillow 打开并处理图片
        with Image.open(image_path) as img:
            target_width = 453
            target_height = 223  # 可以按照需要调整

            # 计算缩放比例
            width_ratio = target_width / img.width
            height_ratio = target_height / img.height
            scale_ratio = min(width_ratio, height_ratio)

            # 等比缩放
            new_size = (int(img.width * scale_ratio), int(img.height * scale_ratio))
            resized_img = img.resize(new_size, Image.Resampling.LANCZOS)

            # 创建白色背景画布
            canvas = Image.new("RGB", (target_width, target_height), (255, 255, 255))

            # 计算居中粘贴位置
            paste_position = (
                (target_width - resized_img.width) // 2,
                (target_height - resized_img.height) // 2,
            )

            # 将缩放后的图片粘贴到画布并保存
            canvas.paste(resized_img, paste_position)
            canvas.save(image_path)

        return os.path.abspath(image_path)

    except Exception as e:
        print("❌下载图片出错了喵~:", e)
        return None


def send_toast_notification(title, message, image, link, search):
    image_path = download_image(image, search)
    imagere = {"src": image_path, "placement": "hero"}
    toast(title, message, image=imagere, on_click=link)


# **检查所有图片是否加载完成**
def all_images_loaded(driver):
    img_elements = driver.find_elements(
        By.CSS_SELECTOR, ".imageContainer__f8ddf3a2 img"
    )
    loaded_count = sum(
        1
        for img in img_elements
        if img.get_attribute("src") and "https" in img.get_attribute("src")
    )
    print(f"✅ 已加载 {loaded_count}/{len(img_elements)} 张图片了哦！")
    return loaded_count == len(img_elements)


# **循环监控新商品**
def get_search_url(search, stop_event):
    # 指定 ChromeDriver 路径
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )
    # **初始化 search_condition_id= 页面**
    base_url = f"https://jp.mercari.com/search?keyword={search}"
    driver.get(base_url)
    search_url = get_redirected_url(driver, search) or base_url
    while not stop_event.is_set():
        # **读取旧数据**
        os.makedirs(search, exist_ok=True)
        if os.path.exists(search + "/mercari_data.json"):
            with open(search + "/mercari_data.json", "r", encoding="utf-8") as f:
                old_items = json.load(f)
        else:
            with open(search + "/mercari_data.json", "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
            old_items = []

        old_item_ids = {item["id"] for item in old_items}
        # 构造旧数据的字典，便于查找历史价格
        old_items_dict = {item["id"]: item for item in old_items}

        try:
            print(
                f"\n 关键词{search}\t (=^･ω･^=)=== 刷新页面啦~正在努力嗅探新商品喵~ ==="
            )
            driver.get(search_url)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "merItemThumbnail"))
            )

            # **确保所有图片加载完成**
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_all_elements_located(
                        (By.CLASS_NAME, "imageContainer__f8ddf3a2")
                    )
                )
                WebDriverWait(driver, 15).until(all_images_loaded)
                print(f"关键词{search}\t ✅ 喵呜~所有图片都加载完成啦~(≧∇≦)/")
            except Exception as e:
                print("⚠️喵~图片加载失败啦，可能是网络卡住了呢(；´д｀)ゞ\n", e)
                continue

            # **检查 search_condition_id= 是否仍然有效**
            current_search_url = get_redirected_url(driver, search)
            search_url = current_search_url if current_search_url else base_url

            # **检查并设置排序方式**
            try:
                select_element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "select__da4764db"))
                )
                select = Select(select_element)
                current_value = select.first_selected_option.get_attribute("value")

                if current_value != "created_time:desc":
                    print(f"关键词{search}\t 🔄 切换排序方式为最新哦~请稍等(๑>ᴗ<๑)")
                    select.select_by_value("created_time:desc")
                    WebDriverWait(driver, 5).until(EC.staleness_of(select_element))
                    WebDriverWait(driver, 10).until(all_images_loaded)
                else:
                    print(f"关键词{search}\t ✅ 已经是最新排序啦~(´･ω･`)")
            except Exception as e:
                print(f"关键词{search}\t ✅ 已经是最新排序啦~(´･ω･`)")

            # **获取所有商品信息**
            items = driver.find_elements(By.CLASS_NAME, "merItemThumbnail")
            new_items = []

            for item in items:
                try:
                    # 获取商品 ID
                    item_id = item.get_attribute("id")

                    # 获取商品名称
                    name_element = item.find_element(
                        By.CLASS_NAME, "itemName__a6f874a2"
                    )
                    item_name = name_element.text

                    # 获取价格
                    price_element = item.find_element(By.CLASS_NAME, "number__6b270ca7")
                    item_price = price_element.text

                    # 获取图片链接
                    img_element = item.find_element(
                        By.CSS_SELECTOR, ".imageContainer__f8ddf3a2 img"
                    )
                    img_url = img_element.get_attribute("src")

                    # 获取商品详情页链接
                    link_element = item.find_element(
                        By.XPATH, "./ancestor::a[@data-testid='thumbnail-link']"
                    )
                    item_link = link_element.get_attribute("href")

                    # 保存数据
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
                    print(f"关键词{search}\t ⚠️ 等待解析商品信息喵~(；´д｀)ゞ\n")

            # **对比是否有新商品**
            new_item_ids = {item["id"] for item in new_items}
            added_items = [item for item in new_items if item["id"] not in old_item_ids]

            # **输出新增商品**
            if added_items:
                print(
                    f"关键词{search}\t 🎉发现{len(added_items)}个新宝贝喵~！快来看看嘛(๑>ᴗ<๑)"
                )
                for item in added_items:
                    print(f"🌸【{item['name']}】 - {item['price']}円喵~")
                    print(f"📸 图片链接: {item['image']}\n")
                    print(f"🔗 详情页: https://jp.mercari.com{item['link']}\n")

                    send_toast_notification(
                        f"关键词 {search} 🎉新宝贝提醒喵~",
                        f"{item['name']} - {item['price']}",
                        item["image"],
                        item["link"],
                        search,
                    )
                old_item_ids.update(new_item_ids)

            for item in new_items:
                if item["id"] in old_items_dict:
                    try:
                        # 将价格文本转为数字
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
                            print(
                                f"关键词{search}\t 喵呜~宝贝【{item['name']}】降价啦~从{old_price}円变成{new_price}円哦，赶紧捡漏嘛(≧ω≦)"
                            )
                            send_toast_notification(
                                f"关键词{search}\t 💰降价警报喵！",
                                f"{item['name']} 降价啦~\n原价:{old_price}円 → 现价:{new_price}円\n不买会后悔哒~(*´∀`)~♥",
                                item["image"],
                                item["link"],
                                search,
                            )

                    except Exception as e:
                        print("⚠️喵呀~价格转换失败，爪子不好使了(´；ω；`)\n", e)

            with open(search + "/mercari_data.json", "w", encoding="utf-8") as f:
                json.dump(new_items, f, ensure_ascii=False, indent=4)

        except Exception as e:
            print("❌喵呜~发生错误啦，主人请稍后再试吧(>_<)\n", e)

        delay = random.randint(1, 10)
        print(f"喵呜~{delay//60}分钟{delay % 60}秒后再次检查喵~\n")
        time.sleep(delay)


# **Mercari 搜索页面**
if __name__ == "__main__":

    # 用于控制子线程结束的事件
    stop_event = threading.Event()

    # 创建线程池并提交监控任务
    with ThreadPoolExecutor(max_workers=len(searches)) as executor:
        futures = [
            executor.submit(get_search_url, search, stop_event) for search in searches
        ]
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n收到退出指令，正在关闭所有监控线程…")
            stop_event.set()  # 通知所有子线程停止
            # 等待子线程执行结束
            for future in futures:
                # 此处如果有需要可以做 future.result() 来捕获子线程异常信息
                future.result()
