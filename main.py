from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import asyncio
import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List
import json
import uuid
import aiohttp
from bson import ObjectId
import logging

# Configuration
BOT_TOKEN = "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo"
ADMIN_USERNAME = "kashaf"
ADMIN_PASSWORD = "kashaf"
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017/walletbot")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# Initialize FastAPI
app = FastAPI(title="Wallet Bot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBasic()

# MongoDB Connection
class Database:
    client: AsyncIOMotorClient = None

db = Database()

async def connect_to_mongo():
    db.client = AsyncIOMotorClient(MONGODB_URL)

async def close_mongo_connection():
    db.client.close()

# Database Models
class UserModel:
    def __init__(self):
        self.collection = db.client.walletbot.users
    
    async def create_user(self, user_data: dict):
        user_data["created_at"] = datetime.utcnow()
        user_data["wallet_balance"] = 0.0
        user_data["total_earned"] = 0.0
        user_data["referral_earnings"] = 0.0
        user_data["total_referrals"] = 0
        user_data["is_active"] = True
        result = await self.collection.insert_one(user_data)
        return str(result.inserted_id)
    
    async def get_user(self, user_id: int):
        return await self.collection.find_one({"user_id": user_id})
    
    async def update_user(self, user_id: int, update_data: dict):
        await self.collection.update_one(
            {"user_id": user_id},
            {"$set": update_data}
        )
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        user = await self.get_user(user_id)
        if not user:
            return False
        
        new_balance = user["wallet_balance"] + amount
        total_earned = user["total_earned"] + amount if amount > 0 else user["total_earned"]
        
        await self.collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "wallet_balance": new_balance,
                    "total_earned": total_earned,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        # Create transaction record
        transaction = TransactionModel()
        await transaction.create_transaction({
            "user_id": user_id,
            "amount": amount,
            "type": transaction_type,
            "description": description,
            "balance_after": new_balance,
            "status": "completed"
        })
        
        return True

class CampaignModel:
    def __init__(self):
        self.collection = db.client.walletbot.campaigns
    
    async def create_campaign(self, campaign_data: dict):
        campaign_data["created_at"] = datetime.utcnow()
        campaign_data["is_active"] = True
        campaign_data["completion_count"] = 0
        result = await self.collection.insert_one(campaign_data)
        return str(result.inserted_id)
    
    async def get_campaign(self, campaign_id: str):
        return await self.collection.find_one({"_id": ObjectId(campaign_id)})
    
    async def get_campaign_by_number(self, campaign_number: int):
        return await self.collection.find_one({"campaign_number": campaign_number})
    
    async def get_active_campaigns(self):
        cursor = self.collection.find({"is_active": True})
        return await cursor.to_list(length=None)
    
    async def update_campaign(self, campaign_id: str, update_data: dict):
        await self.collection.update_one(
            {"_id": ObjectId(campaign_id)},
            {"$set": update_data}
        )

class TransactionModel:
    def __init__(self):
        self.collection = db.client.walletbot.transactions
    
    async def create_transaction(self, transaction_data: dict):
        transaction_data["created_at"] = datetime.utcnow()
        result = await self.collection.insert_one(transaction_data)
        return str(result.inserted_id)
    
    async def get_user_transactions(self, user_id: int):
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", -1)
        return await cursor.to_list(length=None)

class SettingsModel:
    def __init__(self):
        self.collection = db.client.walletbot.settings
    
    async def get_setting(self, key: str):
        setting = await self.collection.find_one({"key": key})
        return setting["value"] if setting else None
    
    async def update_setting(self, key: str, value):
        await self.collection.update_one(
            {"key": key},
            {"$set": {"value": value, "updated_at": datetime.utcnow()}},
            upsert=True
        )

# Initialize Models
user_model = UserModel()
campaign_model = CampaignModel()
transaction_model = TransactionModel()
settings_model = SettingsModel()

# Telegram Bot Setup
class WalletBot:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("wallet", self.wallet_command))
        self.application.add_handler(CommandHandler("campaigns", self.campaigns_command))
        self.application.add_handler(CommandHandler("referral", self.referral_command))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_screenshot))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def check_force_join(self, user_id: int) -> bool:
        """Check if user has joined all required channels"""
        force_channels = await settings_model.get_setting("force_channels")
        if not force_channels:
            return True
        
        for channel in force_channels:
            try:
                member = await self.bot.get_chat_member(channel, user_id)
                if member.status in ['left', 'kicked']:
                    return False
            except:
                continue
        return True
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        first_name = update.effective_user.first_name or "User"
        
        # Check for deep link parameters
        args = context.args
        referrer_id = None
        campaign_number = None
        
        if args:
            param = args[0]
            if param.startswith("camp_"):
                campaign_number = int(param.replace("camp_", ""))
            elif param.isdigit():
                referrer_id = int(param)
        
        # Check if user exists
        user = await user_model.get_user(user_id)
        if not user:
            # Create new user
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "referrer_id": referrer_id
            }
            await user_model.create_user(user_data)
            
            # Give referral bonus to referrer
            if referrer_id:
                referral_amount = await settings_model.get_setting("referral_amount") or 10
                await user_model.add_to_wallet(
                    referrer_id, 
                    referral_amount, 
                    "referral", 
                    f"Referral bonus for {first_name}"
                )
                
                # Update referrer stats
                referrer = await user_model.get_user(referrer_id)
                if referrer:
                    await user_model.update_user(referrer_id, {
                        "total_referrals": referrer.get("total_referrals", 0) + 1,
                        "referral_earnings": referrer.get("referral_earnings", 0) + referral_amount
                    })
        
        # Check force join
        if not await self.check_force_join(user_id):
            force_channels = await settings_model.get_setting("force_channels") or []
            keyboard = []
            for channel in force_channels:
                try:
                    chat = await self.bot.get_chat(channel)
                    keyboard.append([InlineKeyboardButton(f"Join {chat.title}", url=f"https://t.me/{chat.username}")])
                except:
                    continue
            
            keyboard.append([InlineKeyboardButton("✅ Check Join Status", callback_data="check_join")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🔒 **Please join our channels first to use this bot:**",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
        
        # If campaign number specified, show that campaign
        if campaign_number:
            campaign = await campaign_model.get_campaign_by_number(campaign_number)
            if campaign:
                await self.show_campaign(update, context, campaign)
                return
        
        # Welcome message
        welcome_msg = await settings_model.get_setting("welcome_message") or """
🎉 **Welcome to Cashback Wallet Bot!**

💰 Earn money by completing simple tasks
💳 Instant payments to your wallet
👥 Refer friends and earn bonus
📱 Easy withdrawal system

Click the buttons below to get started:
        """
        
        keyboard = [
            [InlineKeyboardButton("💰 My Wallet", callback_data="wallet")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("👥 Referral", callback_data="referral")],
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User not found. Please /start first.")
            return
        
        wallet_msg = f"""
💰 **Your Wallet**

💳 **Current Balance:** ₹{user['wallet_balance']:.2f}
📊 **Total Earned:** ₹{user['total_earned']:.2f}
👥 **Referral Earnings:** ₹{user.get('referral_earnings', 0):.2f}
🎯 **Total Referrals:** {user.get('total_referrals', 0)}

**Recent Transactions:**
        """
        
        # Get recent transactions
        transactions = await transaction_model.get_user_transactions(user_id)
        for tx in transactions[:5]:  # Show last 5 transactions
            tx_type = "+" if tx["amount"] > 0 else ""
            wallet_msg += f"\n• {tx_type}₹{tx['amount']:.2f} - {tx['description']}"
        
        keyboard = [
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def campaigns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        campaigns = await campaign_model.get_active_campaigns()
        
        if not campaigns:
            await update.message.reply_text("📋 No active campaigns available right now.")
            return
        
        campaigns_msg = "📋 **Available Campaigns:**\n\n"
        keyboard = []
        
        for campaign in campaigns:
            campaigns_msg += f"🎯 **{campaign['title']}**\n"
            campaigns_msg += f"💰 Reward: ₹{campaign['reward']:.2f}\n"
            campaigns_msg += f"📝 {campaign['description'][:50]}...\n\n"
            
            keyboard.append([InlineKeyboardButton(
                f"🎯 {campaign['title']} - ₹{campaign['reward']:.2f}", 
                callback_data=f"campaign_{campaign['_id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="campaigns")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(campaigns_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def referral_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User not found. Please /start first.")
            return
        
        referral_amount = await settings_model.get_setting("referral_amount") or 10
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        
        referral_msg = f"""
👥 **Referral Program**

🎁 **Earn ₹{referral_amount} for each friend you refer!**

📊 **Your Stats:**
• Total Referrals: {user.get('total_referrals', 0)}
• Referral Earnings: ₹{user.get('referral_earnings', 0):.2f}

🔗 **Your Referral Link:**
`{referral_link}`

**How it works:**
1. Share your referral link with friends
2. When they join and start using the bot
3. You get ₹{referral_amount} instantly!

💡 **Tip:** Share in groups and social media to earn more!
        """
        
        keyboard = [
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}")],
            [InlineKeyboardButton("🔄 Refresh Stats", callback_data="referral")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def show_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE, campaign: dict):
        campaign_msg = f"""
🎯 **{campaign['title']}**

💰 **Reward:** ₹{campaign['reward']:.2f}
📝 **Description:**
{campaign['description']}

**Instructions:**
{campaign.get('instructions', 'Complete the task as described.')}
        """
        
        if campaign.get('image_url'):
            campaign_msg += f"\n🖼️ **Reference Image:** [View]({campaign['image_url']})"
        
        keyboard = []
        
        if campaign.get('task_url'):
            keyboard.append([InlineKeyboardButton("🚀 Start Task", url=campaign['task_url'])])
        
        if campaign.get('requires_screenshot', False):
            keyboard.append([InlineKeyboardButton("📸 Upload Screenshot", callback_data=f"upload_{campaign['_id']}")])
        else:
            keyboard.append([InlineKeyboardButton("✅ Mark Complete", callback_data=f"complete_{campaign['_id']}")])
        
        keyboard.append([InlineKeyboardButton("⬅️ Back to Campaigns", callback_data="campaigns")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(
                campaign_msg, reply_markup=reply_markup, parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(campaign_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "campaigns":
            await self.campaigns_command(update, context)
        elif data == "referral":
            await self.referral_command(update, context)
        elif data == "check_join":
            if await self.check_force_join(user_id):
                await query.edit_message_text("✅ Great! You have joined all channels. Now you can use the bot!")
                await asyncio.sleep(2)
                await self.start_command(update, context)
            else:
                await query.answer("❌ Please join all channels first!", show_alert=True)
        elif data.startswith("campaign_"):
            campaign_id = data.replace("campaign_", "")
            campaign = await campaign_model.get_campaign(campaign_id)
            if campaign:
                await self.show_campaign(update, context, campaign)
        elif data.startswith("upload_"):
            campaign_id = data.replace("upload_", "")
            context.user_data["waiting_for_screenshot"] = campaign_id
            await query.edit_message_text(
                "📸 **Upload Screenshot**\n\nPlease send a screenshot of your completed task.",
                parse_mode="Markdown"
            )
        elif data.startswith("complete_"):
            campaign_id = data.replace("complete_", "")
            await self.complete_campaign(update, context, campaign_id)
        elif data == "withdraw":
            await self.show_withdrawal_options(update, context)
    
    async def handle_screenshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if "waiting_for_screenshot" not in context.user_data:
            await update.message.reply_text("❌ Please select a campaign first before uploading screenshots.")
            return
        
        campaign_id = context.user_data["waiting_for_screenshot"]
        campaign = await campaign_model.get_campaign(campaign_id)
        
        if not campaign:
            await update.message.reply_text("❌ Campaign not found.")
            return
        
        # Save screenshot for admin approval
        photo = update.message.photo[-1]  # Get highest resolution
        file_id = photo.file_id
        
        # Create submission record
        submission = {
            "user_id": user_id,
            "campaign_id": campaign_id,
            "screenshot_file_id": file_id,
            "status": "pending",
            "submitted_at": datetime.utcnow(),
            "amount": campaign["reward"]
        }
        
        submission_collection = db.client.walletbot.submissions
        await submission_collection.insert_one(submission)
        
        await update.message.reply_text(
            f"✅ **Screenshot submitted successfully!**\n\n"
            f"🎯 Campaign: {campaign['title']}\n"
            f"💰 Reward: ₹{campaign['reward']:.2f}\n\n"
            f"⏳ Your submission is under review. You will be notified once approved!",
            parse_mode="Markdown"
        )
        
        # Clear waiting state
        del context.user_data["waiting_for_screenshot"]
    
    async def complete_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE, campaign_id: str):
        user_id = update.effective_user.id
        campaign = await campaign_model.get_campaign(campaign_id)
        
        if not campaign:
            await update.callback_query.answer("❌ Campaign not found!", show_alert=True)
            return
        
        # Add reward to wallet
        success = await user_model.add_to_wallet(
            user_id, 
            campaign["reward"], 
            "campaign", 
            f"Completed: {campaign['title']}"
        )
        
        if success:
            # Update campaign completion count
            await campaign_model.update_campaign(campaign_id, {
                "completion_count": campaign.get("completion_count", 0) + 1
            })
            
            await update.callback_query.edit_message_text(
                f"🎉 **Congratulations!**\n\n"
                f"✅ Campaign completed successfully!\n"
                f"💰 ₹{campaign['reward']:.2f} added to your wallet!\n\n"
                f"💳 Check your wallet balance with /wallet",
                parse_mode="Markdown"
            )
        else:
            await update.callback_query.answer("❌ Error processing reward!", show_alert=True)
    
    async def show_withdrawal_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        min_withdrawal = await settings_model.get_setting("min_withdrawal") or 6
        
        if user["wallet_balance"] < min_withdrawal:
            await update.callback_query.edit_message_text(
                f"❌ **Insufficient Balance**\n\n"
                f"💰 Your Balance: ₹{user['wallet_balance']:.2f}\n"
                f"🎯 Minimum Withdrawal: ₹{min_withdrawal}\n\n"
                f"💡 Complete more campaigns to reach minimum withdrawal amount!",
                parse_mode="Markdown"
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("🏦 Bank Transfer", callback_data="withdraw_bank")],
            [InlineKeyboardButton("📱 UPI", callback_data="withdraw_upi")],
            [InlineKeyboardButton("💳 PayTM", callback_data="withdraw_paytm")],
            [InlineKeyboardButton("⬅️ Back", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"💸 **Withdrawal Options**\n\n"
            f"💰 Available Balance: ₹{user['wallet_balance']:.2f}\n"
            f"🎯 Minimum Withdrawal: ₹{min_withdrawal}\n\n"
            f"Choose your preferred withdrawal method:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Handle any text messages (like withdrawal details, etc.)
        text = update.message.text
        user_id = update.effective_user.id
        
        # Default response
        await update.message.reply_text(
            "👋 Hi! Use the menu buttons or commands to navigate:\n\n"
            "• /wallet - Check your balance\n"
            "• /campaigns - View available tasks\n"
            "• /referral - Your referral program\n"
            "• /start - Main menu"
        )

# Initialize bot
wallet_bot = WalletBot()

# Admin Authentication
def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username

# API Routes
@app.post("/webhook")
async def telegram_webhook(update: dict):
    """Handle Telegram webhook updates"""
    try:
        telegram_update = Update.de_json(update, wallet_bot.bot)
        await wallet_bot.application.process_update(telegram_update)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

# Admin Panel APIs
@app.get("/api/admin/dashboard")
async def get_dashboard(admin: str = Depends(authenticate_admin)):
    """Get admin dashboard stats"""
    users_count = await db.client.walletbot.users.count_documents({})
    active_campaigns = await db.client.walletbot.campaigns.count_documents({"is_active": True})
    pending_submissions = await db.client.walletbot.submissions.count_documents({"status": "pending"})
    total_withdrawals = await db.client.walletbot.transactions.count_documents({"type": "withdrawal"})
    
    return {
        "users_count": users_count,
        "active_campaigns": active_campaigns,
        "pending_submissions": pending_submissions,
        "total_withdrawals": total_withdrawals
    }

@app.get("/api/admin/users")
async def get_users(admin: str = Depends(authenticate_admin)):
    """Get all users"""
    cursor = db.client.walletbot.users.find({}).sort("created_at", -1)
    users = await cursor.to_list(length=None)
    
    # Convert ObjectId to string for JSON serialization
    for user in users:
        user["_id"] = str(user["_id"])
    
    return {"users": users}

@app.post("/api/admin/campaigns")
async def create_campaign(
    title: str = Form(...),
    description: str = Form(...),
    instructions: str = Form(...),
    reward: float = Form(...),
    campaign_number: int = Form(...),
    task_url: Optional[str] = Form(None),
    requires_screenshot: bool = Form(False),
    admin: str = Depends(authenticate_admin)
):
    """Create new campaign"""
    campaign_data = {
        "title": title,
        "description": description,
        "instructions": instructions,
        "reward": reward,
        "campaign_number": campaign_number,
        "task_url": task_url,
        "requires_screenshot": requires_screenshot
    }
    
    campaign_id = await campaign_model.create_campaign(campaign_data)
    return {"status": "success", "campaign_id": campaign_id}

@app.get("/api/admin/campaigns")
async def get_campaigns(admin: str = Depends(authenticate_admin)):
    """Get all campaigns"""
    campaigns = await campaign_model.get_active_campaigns()
    
    # Convert ObjectId to string
    for campaign in campaigns:
        campaign["_id"] = str(campaign["_id"])
    
    return {"campaigns": campaigns}

@app.get("/api/admin/submissions")
async def get_submissions(admin: str = Depends(authenticate_admin)):
    """Get all pending submissions"""
    cursor = db.client.walletbot.submissions.find({"status": "pending"}).sort("submitted_at", -1)
    submissions = await cursor.to_list(length=None)
    
    # Get user and campaign details
    for submission in submissions:
        submission["_id"] = str(submission["_id"])
        submission["campaign_id"] = str(submission["campaign_id"])
        
        # Get user info
        user = await user_model.get_user(submission["user_id"])
        submission["user_name"] = user["first_name"] if user else "Unknown"
        
        # Get campaign info
        campaign = await campaign_model.get_campaign(submission["campaign_id"])
        submission["campaign_title"] = campaign["title"] if campaign else "Unknown"
    
    return {"submissions": submissions}

@app.post("/api/admin/submissions/{submission_id}/approve")
async def approve_submission(submission_id: str, admin: str = Depends(authenticate_admin)):
    """Approve a submission"""
    submission = await db.client.walletbot.submissions.find_one({"_id": ObjectId(submission_id)})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Add money to user's wallet
    success = await user_model.add_to_wallet(
        submission["user_id"],
        submission["amount"],
        "campaign",
        f"Screenshot approved for campaign"
    )
    
    if success:
        # Update submission status
        await db.client.walletbot.submissions.update_one(
            {"_id": ObjectId(submission_id)},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow()}}
        )
        
        # Notify user via Telegram
        try:
            await wallet_bot.bot.send_message(
                submission["user_id"],
                f"✅ **Screenshot Approved!**\n\n"
                f"💰 ₹{submission['amount']:.2f} has been added to your wallet!\n"
                f"💳 Check your balance with /wallet",
                parse_mode="Markdown"
            )
        except:
            pass  # User might have blocked the bot
        
        return {"status": "approved"}
    else:
        raise HTTPException(status_code=500, detail="Error processing approval")

@app.post("/api/admin/submissions/{submission_id}/reject")
async def reject_submission(submission_id: str, reason: str = Form(...), admin: str = Depends(authenticate_admin)):
    """Reject a submission"""
    submission = await db.client.walletbot.submissions.find_one({"_id": ObjectId(submission_id)})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Update submission status
    await db.client.walletbot.submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {"$set": {"status": "rejected", "rejection_reason": reason, "rejected_at": datetime.utcnow()}}
    )
    
    # Notify user via Telegram
    try:
        await wallet_bot.bot.send_message(
            submission["user_id"],
            f"❌ **Screenshot Rejected**\n\n"
            f"**Reason:** {reason}\n\n"
            f"💡 Please try again with a proper screenshot.",
            parse_mode="Markdown"
        )
    except:
        pass
    
    return {"status": "rejected"}

@app.get("/api/admin/settings")
async def get_settings(admin: str = Depends(authenticate_admin)):
    """Get all settings"""
    settings = {}
    
    # Get all settings
    cursor = db.client.walletbot.settings.find({})
    async for setting in cursor:
        settings[setting["key"]] = setting["value"]
    
    # Set defaults if not exists
    default_settings = {
        "min_withdrawal": 6,
        "referral_amount": 10,
        "welcome_message": "🎉 Welcome to Cashback Wallet Bot!",
        "force_channels": [],
        "payment_gateway_api": ""
    }
    
    for key, value in default_settings.items():
        if key not in settings:
            settings[key] = value
    
    return {"settings": settings}

@app.post("/api/admin/settings")
async def update_settings(settings: dict, admin: str = Depends(authenticate_admin)):
    """Update settings"""
    for key, value in settings.items():
        await settings_model.update_setting(key, value)
    
    return {"status": "updated"}

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    
    # Set webhook if URL provided
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        await wallet_bot.bot.set_webhook(webhook_url)

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


# Health check endpoint for Railway
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "wallet-bot", "timestamp": datetime.utcnow().isoformat()}

@app.get("/")
async def root():
    return {"message": "Telegram Wallet Bot API", "status": "running"}
