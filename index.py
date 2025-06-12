import asyncio
import logging
import os
import json
from datetime import datetime, time

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.methods import SendMessage, SendPhoto 

from fastapi import FastAPI, Request, Response

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
BASE_WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_HOSTNAME")

if not all([BOT_TOKEN, CHANNEL_ID, ADMIN_ID, BASE_WEBHOOK_URL]):
    logging.critical("CRITICAL ERROR: One or more environment variables are not set!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

START_TIME = time(8, 0)
END_TIME = time(22, 0)
POSTS_PER_DAY = 10
post_queue = asyncio.Queue()

@dp.message()
async def handle_new_post(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("شما اجازه ارسال پست را ندارید.")

    post_data = None
    if message.photo:
        post_data = {"type": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption or ""}
    elif message.text:
        post_data = {"type": "text", "content": message.text}
    
    if post_data:
        await post_queue.put(post_data)
        await message.answer(f"✅ پست شما با موفقیت به صف انتظار اضافه شد.\nتعداد پست‌های در صف: {post_queue.qsize()}")
    else:
        await message.answer("نوع پیام پشتیبانی نمی‌شود. لطفاً فقط متن یا عکس ارسال کنید.")

async def send_post_to_channel(post_data: dict):
    try:
        if post_data["type"] == "photo":
            await bot.send_photo(chat_id=CHANNEL_ID, photo=post_data["file_id"], caption=post_data["caption"])
        elif post_data["type"] == "text":
            await bot.send_message(chat_id=CHANNEL_ID, text=post_data["content"])
        logging.info(f"Post sent to channel {CHANNEL_ID}.")
    except Exception as e:
        logging.error(f"Error sending post: {e}, re-queueing...")
        await asyncio.sleep(5)
        await post_queue.put(post_data)

async def scheduler():
    while True:
        try:
            start_total_seconds = START_TIME.hour * 3600 + START_TIME.minute * 60
            end_total_seconds = END_TIME.hour * 3600 + END_TIME.minute * 60
            total_seconds_in_day = end_total_seconds - start_total_seconds
            
            interval = total_seconds_in_day / POSTS_PER_DAY if POSTS_PER_DAY > 0 else 3600

            now_time = datetime.now().time()
            if START_TIME <= now_time <= END_TIME and not post_queue.empty():
                post = await post_queue.get()
                await send_post_to_channel(post)
                logging.info(f"Post sent by scheduler. Next post in {interval} seconds.")
            else:
                logging.info("Scheduler waiting: outside active hours or queue is empty. Checking in 30 seconds.")
                await asyncio.sleep(30) 
            # اگر scheduler به صورت مداوم اجرا شود، این sleep ها برای کنترل نرخ مفید هستند.
            # در مدل Serverless، فقط برای جلوگیری از حلقه بی نهایت در یک Cold Start استفاده می شود.
        except Exception as e:
            logging.error(f"Error in scheduler loop: {e}")
            await asyncio.sleep(60) 

app = FastAPI()

@app.on_event("startup")
async def on_startup_fastapi():
    webhook_url = f"https://{BASE_WEBHOOK_URL}/webhook"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set to {webhook_url}.")
    
    asyncio.create_task(scheduler())
    logging.info("Scheduler background task initiated (Render background workers can run continuously).")

@app.get("/")
async def health_check():
    return {"status": "Bot server is running and healthy!"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update_json = await request.json()
        update = types.Update.model_validate(update_json)
        await dp.feed_update(bot, update)
        
        return Response(status_code=200, content=json.dumps({"ok": True}), media_type="application/json")
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        return Response(status_code=500, content=json.dumps({"ok": False, "error": str(e)}), media_type="application/json")

# تغییر مهم: این Endpoint حالا هر دو متد GET و POST را قبول می کند
@app.get("/run-scheduler") 
@app.post("/run-scheduler")
async def run_scheduler_endpoint():
    try:
        if not post_queue.empty():
            post = await post_queue.get()
            await send_post_to_channel(post)
            logging.info("Scheduler endpoint triggered: A post was sent.")
            return {"status": "Post sent from queue", "queue_size": post_queue.qsize()}
        else:
            logging.info("Scheduler endpoint triggered: Queue is empty.")
            return {"status": "Queue is empty"}
    except Exception as e:
        logging.error(f"Error in run-scheduler endpoint: {e}")
        return Response(status_code=500, content=json.dumps({"ok": False, "error": str(e)}), media_type="application/json")

if __name__ == "__main__":
    import uvicorn
    logging.info("Running FastAPI app locally...")
    uvicorn.run(app, host="0.0.0.0", port=8000)