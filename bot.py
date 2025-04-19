import asyncio
import logging
import threading
import io
import re
import cloudscraper
from flask import Flask
from bs4 import BeautifulSoup
from pyrogram import Client, errors
from config import BOT, API, OWNER, CHANNEL

# ------------------ Logging Setup ------------------
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

# ------------------ Flask App for Health Check ------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

# ------------------ Utility Function ------------------
def extract_size(text):
    match = re.search(r"(\d+(\.\d+)?\s*(GB|MB|KB))", text, re.IGNORECASE)
    return match.group(1) if match else "Unknown"

# ------------------ 1TamilMV Crawler ------------------
def extract_tamilmv_post_details(post_url, scraper):
    response = scraper.get(post_url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    torrent_files = []
    for tag in soup.find_all("a", attrs={"data-fileext": "torrent"}):
        if not tag.has_attr("href"):
            continue
        link = tag["href"].strip()
        title = tag.get_text(strip=True)
        prefix = "www.1TamilMV.esq - "
        if title.startswith(prefix):
            title = title[len(prefix):]
        if title.lower().endswith(".torrent"):
            title = title[:-len(".torrent")].strip()
        torrent_files.append({"type": "torrent", "title": title, "link": link})

    overall_title = torrent_files[0]["title"] if torrent_files else "TamilMV Post"
    size = extract_size(overall_title)
    return {"thread_url": post_url, "title": overall_title, "size": size, "links": torrent_files}

def crawl_tamilmv():
    base_url = "https://www.1tamilmv.moi"
    scraper = cloudscraper.create_scraper()
    torrents = []

    try:
        resp = scraper.get(base_url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Collect up to 15 unique topic URLs
        post_paths = {
            a["href"]
            for a in soup.find_all("a", href=re.compile(r'/forums/topic/'))
            if a.get("href")
        }
        for path in list(post_paths)[:15]:
            full_url = path if path.startswith("http") else base_url + path
            logging.info(f"Found topic: {full_url}")
            try:
                details = extract_tamilmv_post_details(full_url, scraper)
                torrents.append(details)
                logging.info(f"→ {details['title']} ({len(details['links'])} files)")
            except Exception as e:
                logging.error(f"Error scraping {full_url}: {e}")
    except Exception as e:
        logging.error(f"Failed to load TamilMV homepage: {e}")

    return torrents

# ------------------ Bot Class ------------------
class MN_Bot(Client):
    MAX_MSG_LENGTH = 4000

    def __init__(self):
        super().__init__(
            "MN-Bot",
            api_id=API.ID,
            api_hash=API.HASH,
            bot_token=BOT.TOKEN,
            plugins=dict(root="plugins"),
            workers=16,
        )
        self.channel_id = int(CHANNEL.ID)
        self.seen_threads = set()
        self.last_posted = set()

    async def safe_send_message(self, chat_id, text, **kwargs):
        if len(text) <= self.MAX_MSG_LENGTH:
            return await self.send_message(chat_id, text, **kwargs)
        for i in range(0, len(text), self.MAX_MSG_LENGTH):
            await self.send_message(chat_id, text[i:i+self.MAX_MSG_LENGTH], **kwargs)
            await asyncio.sleep(1)

    async def start(self):
        await super().start()
        me = await self.get_me()
        BOT.USERNAME = f"@{me.username}"
        asyncio.create_task(self.auto_post_torrents())
        await self.send_message(int(OWNER.ID), f"{me.first_name} ✅ BOT started")
        logging.info("Bot started")

    async def stop(self, *args):
        await super().stop()
        logging.info("Bot stopped")

    async def auto_post_torrents(self):
        while True:
            try:
                all_threads = crawl_tamilmv()
                for thread in all_threads:
                    tid = thread["thread_url"]

                    # NEW FORUM THREAD: post *all* its torrent-files
                    if tid not in self.seen_threads:
                        logging.info(f"New thread detected: {tid}")
                        for file in thread["links"]:
                            await self._send_torrent(file, thread["size"])
                        self.seen_threads.add(tid)

                    # EXISTING THREAD: post only *new* files
                    else:
                        for file in thread["links"]:
                            if file["link"] not in self.last_posted:
                                logging.info(f"New file in known thread: {file['link']}")
                                await self._send_torrent(file, thread["size"])

                logging.info(f"Sleeping for 15 minutes…")
            except Exception as e:
                logging.error(f"auto_post_torrents error: {e}")

            await asyncio.sleep(900)  # 15 minutes

    async def _send_torrent(self, file, size):
        link = file["link"]
        try:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(link, timeout=10)
            resp.raise_for_status()
            bio = io.BytesIO(resp.content)
            filename = f"{file['title'].replace(' ', '_')}.torrent"
            await self.send_document(
                self.channel_id,
                bio,
                file_name=filename,
                caption=f"{file['title']}\n📦 {size}\n\n#tamilmv"
            )
            self.last_posted.add(link)
            await asyncio.sleep(3)
        except Exception as ex:
            logging.error(f"Failed to send {file['title']}: {ex}")

# ------------------ Main ------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    MN_Bot().run()
