import logging
import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application, PicklePersistence
from telegram import Update
from quotes import get_random_quote
import datetime
import os
from server import app, run as run_server
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
import sys
from telegram.error import Conflict, NetworkError

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file")

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌤️ *Welcome to Heaven Ping!*\nEach morning, you’ll receive a Word of encouragement.\n\nType /today to get one now!",
        parse_mode="Markdown"
    )

# /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠️ *Commands Available:*\n"
        "/start - Start the bot\n"
        "/today - Get today’s devotion now\n"
        "/daily - Subscribe to daily devotionals at 9AM\n"
        "/unsubscribe - Unsubscribe from daily devotionals\n"
        "/help - See all commands",
        parse_mode="Markdown"
    )

# /today command
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quote = await asyncio.get_event_loop().run_in_executor(None, get_random_quote)
    await update.message.reply_text(f"📖 *Today's Word:*\n\n_{quote}_", parse_mode="Markdown")

# Function to send scheduled devotionals
async def scheduled_send(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = context.job.data
        quote = await asyncio.get_event_loop().run_in_executor(None, get_random_quote)
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"☀️ Good Morning!\n\n_{quote}_", 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send scheduled message: {e}")

# Function to set up daily jobs for all subscribed users
async def setup_daily_jobs(application: Application):
    try:
        # Get all subscribed users from persistence
        subscribed_users = application.bot_data.get("subscribed_users", set())
        logger.info(f"Setting up daily jobs for {len(subscribed_users)} users")
        
        for chat_id in subscribed_users:
            application.job_queue.run_daily(
                scheduled_send,
                datetime.time(hour=9, minute=0),
                data=chat_id,
                name=str(chat_id)
            )
    except Exception as e:
        logger.error(f"Error setting up daily jobs: {e}")

# /daily command - schedules daily devotional
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        # Add user to subscribed users list
        if "subscribed_users" not in context.application.bot_data:
            context.application.bot_data["subscribed_users"] = set()
        context.application.bot_data["subscribed_users"].add(chat_id)
        
        # Schedule the job
        job = context.job_queue.run_daily(
            scheduled_send,
            datetime.time(hour=9, minute=0),
            data=chat_id,
            name=str(chat_id)
        )
        
        # Send confirmation
        quote = await asyncio.get_event_loop().run_in_executor(None, get_random_quote)
        await update.message.reply_text(f"📖 *First devotional:*\n\n_{quote}_", parse_mode="Markdown")
        await update.message.reply_text("✅ Successfully subscribed! You'll receive daily devotionals at *9:00 AM*!", parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Scheduling error: {str(e)}")
        await update.message.reply_text("❌ Technical error. Please try again.")

async def remove_job_if_exists(name: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.job_queue:
        return False
        
    try:
        current_jobs = context.job_queue.get_jobs_by_name(name)
        if current_jobs:
            for job in current_jobs:
                job.schedule_removal()
            logger.info(f"Removed existing jobs for: {name}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error removing job {name}: {e}")
        return False

# Add unsubscribe command
async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        # Remove from subscribed users
        if "subscribed_users" in context.application.bot_data:
            context.application.bot_data["subscribed_users"].discard(chat_id)
        
        removed = await remove_job_if_exists(str(chat_id), context)
        await update.message.reply_text("✅ Successfully unsubscribed from daily devotionals.")
    except Exception as e:
        logger.error(f"Unsubscribe error: {str(e)}")
        await update.message.reply_text("❌ Could not unsubscribe. Please try again.")

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")
    if isinstance(context.error, Conflict):
        logger.error("Another bot instance is running!")
    elif isinstance(context.error, NetworkError):
        logger.error("Network error occurred")

# Main bot logic
def main():
    application = None
    try:
        # Initialize application
        application = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .persistence(PicklePersistence(filepath="bot_data"))
            .build()
        )
        
        # Add error handler
        application.add_error_handler(error_handler)
        
        # Register handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("today", today))
        application.add_handler(CommandHandler("daily", schedule))
        application.add_handler(CommandHandler("unsubscribe", unsubscribe))

        # Setup jobs for existing subscribers
        application.job_queue.run_once(
            lambda ctx: asyncio.create_task(setup_daily_jobs(application)),
            when=0
        )

        logger.info("Bot starting...")
        
        # Use webhooks in production
        if os.getenv("ENVIRONMENT") == "production":
            port = int(os.getenv("PORT", "8080"))
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                webhook_url=os.getenv("WEBHOOK_URL"),
                allowed_updates=Update.ALL_TYPES
            )
        else:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            
    except Exception as e:
        logger.error(f"Critical startup error: {e}")
        raise
    finally:
        if application:
            application.stop()

if __name__ == "__main__":
    try:
        # Start web server in background
        server_thread = Thread(target=run_server, daemon=True)
        server_thread.start()
        
        # Start bot
        main()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        sys.exit(0)