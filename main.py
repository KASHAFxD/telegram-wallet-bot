from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
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
import warnings

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo"
ADMIN_USERNAME = "kashaf"
ADMIN_PASSWORD = "kashaf"
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017/walletbot")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8000))

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

# MongoDB Connection with proper error handling
class Database:
    def __init__(self):
        self.client = None
        self.connected = False
        self.connect()
    
    def connect(self):
        mongodb_url = os.getenv("MONGODB_URL")
        logger.info("Attempting MongoDB connection...")
        
        if mongodb_url:
            try:
                self.client = AsyncIOMotorClient(mongodb_url)
                self.connected = True
                logger.info("MongoDB client initialized successfully")
            except Exception as e:
                logger.error(f"MongoDB connection error: {e}")
                self.client = None
                self.connected = False
        else:
            logger.error("MONGODB_URL environment variable not found")
            self.client = None
            self.connected = False

# Initialize database
db = Database()

async def connect_to_mongo():
    """Test MongoDB connection"""
    if db.client:
        try:
            await db.client.admin.command('ping')
            logger.info("MongoDB connection test successful")
            db.connected = True
        except Exception as e:
            logger.error(f"MongoDB connection test failed: {e}")
            db.connected = False

async def close_mongo_connection():
    if db.client:
        db.client.close()
        logger.info("MongoDB connection closed")

# Database Models with FIXED collection validation
class UserModel:
    def __init__(self):
        self.collection = None
        if db.client is not None:
            self.collection = db.client.walletbot.users
    
    async def create_user(self, user_data: dict):
        if self.collection is None:  # FIXED: Proper None check
            logger.warning("UserModel: Collection not available")
            return None
            
        try:
            user_data["created_at"] = datetime.utcnow()
            user_data["wallet_balance"] = 0.0
            user_data["total_earned"] = 0.0
            user_data["referral_earnings"] = 0.0
            user_data["total_referrals"] = 0
            user_data["is_active"] = True
            
            result = await self.collection.insert_one(user_data)
            logger.info(f"User created: {user_data['user_id']}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return None
    
    async def get_user(self, user_id: int):
        if self.collection is None:  # FIXED: Proper None check
            return None
        try:
            return await self.collection.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def update_user(self, user_id: int, update_data: dict):
        if self.collection is None:  # FIXED: Proper None check
            return False
        try:
            update_data["updated_at"] = datetime.utcnow()
            await self.collection.update_one(
                {"user_id": user_id},
                {"$set": update_data}
            )
            return True
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            return False
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        if self.collection is None:  # FIXED: Proper None check
            logger.warning("Cannot add to wallet - database not connected")
            return False
            
        try:
            user = await self.get_user(user_id)
            if not user:
                logger.warning(f"User not found: {user_id}")
                return False
            
            new_balance = user.get("wallet_balance", 0) + amount
            total_earned = user.get("total_earned", 0)
            if amount > 0:
                total_earned += amount
            
            update_result = await self.collection.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "wallet_balance": new_balance,
                        "total_earned": total_earned,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            
            if update_result.modified_count > 0:
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
                logger.info(f"Wallet updated for user {user_id}: +{amount}")
                return True
            else:
                logger.error(f"Failed to update wallet for user {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error adding to wallet: {e}")
            return False

class CampaignModel:
    def __init__(self):
        self.collection = None
        if db.client is not None:
            self.collection = db.client.walletbot.campaigns
    
    async def create_campaign(self, campaign_data: dict):
        if self.collection is None:  # FIXED: Proper None check
            return None
        try:
            campaign_data["created_at"] = datetime.utcnow()
            campaign_data["is_active"] = True
            campaign_data["completion_count"] = 0
            result = await self.collection.insert_one(campaign_data)
            logger.info(f"Campaign created: {campaign_data['title']}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating campaign: {e}")
            return None
    
    async def get_campaign(self, campaign_id: str):
        if self.collection is None:  # FIXED: Proper None check
            return None
        try:
            return await self.collection.find_one({"_id": ObjectId(campaign_id)})
        except Exception as e:
            logger.error(f"Error getting campaign: {e}")
            return None
    
    async def get_campaign_by_number(self, campaign_number: int):
        if self.collection is None:  # FIXED: Proper None check
            return None
        try:
            return await self.collection.find_one({"campaign_number": campaign_number})
        except Exception as e:
            logger.error(f"Error getting campaign by number: {e}")
            return None
    
    async def get_active_campaigns(self):
        if self.collection is None:  # FIXED: Proper None check
            return []
        try:
            cursor = self.collection.find({"is_active": True})
            campaigns = await cursor.to_list(length=None)
            return campaigns
        except Exception as e:
            logger.error(f"Error getting active campaigns: {e}")
            return []
    
    async def update_campaign(self, campaign_id: str, update_data: dict):
        if self.collection is None:  # FIXED: Proper None check
            return False
        try:
            update_data["updated_at"] = datetime.utcnow()
            await self.collection.update_one(
                {"_id": ObjectId(campaign_id)},
                {"$set": update_data}
            )
            return True
        except Exception as e:
            logger.error(f"Error updating campaign: {e}")
            return False

class TransactionModel:
    def __init__(self):
        self.collection = None
        if db.client is not None:
            self.collection = db.client.walletbot.transactions
    
    async def create_transaction(self, transaction_data: dict):
        if self.collection is None:  # FIXED: Proper None check
            return None
        try:
            transaction_data["created_at"] = datetime.utcnow()
            result = await self.collection.insert_one(transaction_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating transaction: {e}")
            return None
    
    async def get_user_transactions(self, user_id: int):
        if self.collection is None:  # FIXED: Proper None check
            return []
        try:
            cursor = self.collection.find({"user_id": user_id}).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error getting user transactions: {e}")
            return []

class SettingsModel:
    def __init__(self):
        self.collection = None
        if db.client is not None:
            self.collection = db.client.walletbot.settings
    
    async def get_setting(self, key: str):
        if self.collection is None:  # FIXED: Proper None check
            # Return default values
            defaults = {
                "referral_amount": 10,
                "min_withdrawal": 6,
                "welcome_message": "🎉 Welcome to Cashback Wallet Bot!\n\n💰 Earn money by completing simple tasks\n💳 Instant payments to your wallet\n👥 Refer friends and earn bonus\n📱 Easy withdrawal system\n\nClick the buttons below to get started:",
                "force_channels": []
            }
            return defaults.get(key)
        
        try:
            setting = await self.collection.find_one({"key": key})
            return setting["value"] if setting else None
        except Exception as e:
            logger.error(f"Error getting setting: {e}")
            return None
    
    async def update_setting(self, key: str, value):
        if self.collection is None:  # FIXED: Proper None check
            return False
        try:
            await self.collection.update_one(
                {"key": key},
                {"$set": {"value": value, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error updating setting: {e}")
            return False

# Initialize Models
user_model = UserModel()
campaign_model = CampaignModel()
transaction_model = TransactionModel()
settings_model = SettingsModel()

# Telegram Bot Setup (Fixed version)
class WalletBot:
    def __init__(self):
        self.bot = None
        self.application = None
        self.initialized = False
        self.setup_bot()
    
    def setup_bot(self):
        try:
            # Initialize bot
            self.bot = Bot(token=BOT_TOKEN)
            
            # Create application using ApplicationBuilder (stable method)
            self.application = ApplicationBuilder().token(BOT_TOKEN).build()
            
            # Setup handlers
            self.setup_handlers()
            
            self.initialized = True
            logger.info("Telegram bot initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing Telegram bot: {e}")
            self.bot = None
            self.application = None
            self.initialized = False
    
    def setup_handlers(self):
        if self.application is None:
            logger.error("Application not initialized, skipping handler setup")
            return
            
        try:
            # Add command handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("campaigns", self.campaigns_command))
            self.application.add_handler(CommandHandler("referral", self.referral_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            
            # Add callback and message handlers
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_screenshot))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            logger.info("Telegram bot handlers setup successfully")
            
        except Exception as e:
            logger.error(f"Error setting up handlers: {e}")
    
    async def check_force_join(self, user_id: int) -> bool:
        """Check if user has joined all required channels"""
        try:
            force_channels = await settings_model.get_setting("force_channels")
            if not force_channels or len(force_channels) == 0:
                return True
            
            for channel in force_channels:
                try:
                    member = await self.bot.get_chat_member(channel, user_id)
                    if member.status in ['left', 'kicked']:
                        return False
                except Exception as e:
                    logger.warning(f"Could not check membership for {channel}: {e}")
                    continue
            return True
        except Exception as e:
            logger.error(f"Error checking force join: {e}")
            return True
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            first_name = update.effective_user.first_name or "User"
            
            logger.info(f"Start command from user: {user_id} ({first_name})")
            
            # Check for deep link parameters
            args = context.args
            referrer_id = None
            campaign_number = None
            
            if args:
                param = args[0]
                if param.startswith("camp_"):
                    try:
                        campaign_number = int(param.replace("camp_", ""))
                    except ValueError:
                        pass
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
                    success = await user_model.add_to_wallet(
                        referrer_id, 
                        referral_amount, 
                        "referral", 
                        f"Referral bonus for {first_name}"
                    )
                    
                    if success:
                        # Update referrer stats
                        referrer = await user_model.get_user(referrer_id)
                        if referrer:
                            await user_model.update_user(referrer_id, {
                                "total_referrals": referrer.get("total_referrals", 0) + 1,
                                "referral_earnings": referrer.get("referral_earnings", 0) + referral_amount
                            })
                            
                            # Notify referrer
                            try:
                                await self.bot.send_message(
                                    referrer_id,
                                    f"🎉 **New Referral!**\n\n"
                                    f"👤 {first_name} joined using your link!\n"
                                    f"💰 ₹{referral_amount} added to your wallet!\n"
                                    f"💳 Check your balance with /wallet",
                                    parse_mode="Markdown"
                                )
                            except:
                                pass
            
            # Check force join
            if not await self.check_force_join(user_id):
                force_channels = await settings_model.get_setting("force_channels") or []
                if force_channels:
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
            welcome_msg = await settings_model.get_setting("welcome_message") or """🎉 **Welcome to Cashback Wallet Bot!**

💰 Earn money by completing simple tasks
💳 Instant payments to your wallet
👥 Refer friends and earn bonus
📱 Easy withdrawal system

Click the buttons below to get started:"""
            
            keyboard = [
                [InlineKeyboardButton("💰 My Wallet", callback_data="wallet")],
                [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
                [InlineKeyboardButton("👥 Referral", callback_data="referral")],
                [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error in start command: {e}")
            try:
                await update.message.reply_text("❌ An error occurred. Please try again later.")
            except:
                pass
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            if not user:
                await update.message.reply_text("❌ User not found. Please /start first.")
                return
            
            wallet_msg = f"""💰 **Your Wallet**

💳 **Current Balance:** ₹{user.get('wallet_balance', 0):.2f}
📊 **Total Earned:** ₹{user.get('total_earned', 0):.2f}
👥 **Referral Earnings:** ₹{user.get('referral_earnings', 0):.2f}
🎯 **Total Referrals:** {user.get('total_referrals', 0)}

**Recent Transactions:**"""
            
            # Get recent transactions
            transactions = await transaction_model.get_user_transactions(user_id)
            if transactions:
                for tx in transactions[:5]:  # Show last 5 transactions
                    tx_type = "+" if tx["amount"] > 0 else ""
                    wallet_msg += f"\n• {tx_type}₹{tx['amount']:.2f} - {tx['description']}"
            else:
                wallet_msg += "\nNo transactions yet."
            
            keyboard = [
                [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
                [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
                [InlineKeyboardButton("🔄 Refresh", callback_data="wallet")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
                
        except Exception as e:
            logger.error(f"Error in wallet command: {e}")
            error_msg = "❌ Error loading wallet. Please try again."
            try:
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(error_msg)
                else:
                    await update.message.reply_text(error_msg)
            except:
                pass
    
    async def campaigns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            campaigns = await campaign_model.get_active_campaigns()
            
            if not campaigns:
                msg = "📋 No active campaigns available right now.\n\n💡 Check back later for new earning opportunities!"
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(msg)
                else:
                    await update.message.reply_text(msg)
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
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(campaigns_msg, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(campaigns_msg, reply_markup=reply_markup, parse_mode="Markdown")
                
        except Exception as e:
            logger.error(f"Error in campaigns command: {e}")
            error_msg = "❌ Error loading campaigns. Please try again."
            try:
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(error_msg)
                else:
                    await update.message.reply_text(error_msg)
            except:
                pass
    
    async def referral_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            if not user:
                await update.message.reply_text("❌ User not found. Please /start first.")
                return
            
            referral_amount = await settings_model.get_setting("referral_amount") or 10
            bot_username = (await self.bot.get_me()).username
            referral_link = f"https://t.me/{bot_username}?start={user_id}"
            
            referral_msg = f"""👥 **Referral Program**

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

💡 **Tip:** Share in groups and social media to earn more!"""
            
            keyboard = [
                [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}")],
                [InlineKeyboardButton("🔄 Refresh Stats", callback_data="referral")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
                
        except Exception as e:
            logger.error(f"Error in referral command: {e}")
            try:
                await update.message.reply_text("❌ Error loading referral info. Please try again.")
            except:
                pass
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            help_msg = """🆘 **Bot Help**

**Available Commands:**
• /start - Main menu
• /wallet - Check your balance
• /campaigns - View available tasks
• /referral - Your referral program
• /help - Show this help

**How to Earn:**
1. 📋 Complete campaigns for instant rewards
2. 👥 Refer friends and earn bonus
3. 💸 Withdraw when you reach minimum amount

**Need Support?**
Contact our admin team for assistance."""
            
            await update.message.reply_text(help_msg, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Error in help command: {e}")
    
    async def show_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE, campaign: dict):
        try:
            campaign_msg = f"""🎯 **{campaign['title']}**

💰 **Reward:** ₹{campaign['reward']:.2f}
📝 **Description:**
{campaign['description']}

**Instructions:**
{campaign.get('instructions', 'Complete the task as described.')}"""
            
            if campaign.get('image_url'):
                campaign_msg += f"\n\n🖼️ **Reference Image:** [View]({campaign['image_url']})"
            
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
                
        except Exception as e:
            logger.error(f"Error showing campaign: {e}")
            try:
                await update.callback_query.edit_message_text("❌ Error loading campaign details.")
            except:
                pass
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            
            data = query.data
            user_id = update.effective_user.id
            
            logger.info(f"Button pressed: {data} by user {user_id}")
            
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
                else:
                    await query.edit_message_text("❌ Campaign not found or no longer available.")
            elif data.startswith("upload_"):
                campaign_id = data.replace("upload_", "")
                context.user_data["waiting_for_screenshot"] = campaign_id
                await query.edit_message_text(
                    "📸 **Upload Screenshot**\n\nPlease send a screenshot of your completed task.\n\n⚠️ Make sure the screenshot clearly shows task completion.",
                    parse_mode="Markdown"
                )
            elif data.startswith("complete_"):
                campaign_id = data.replace("complete_", "")
                await self.complete_campaign(update, context, campaign_id)
            elif data == "withdraw":
                await self.show_withdrawal_options(update, context)
            else:
                await query.answer("⚠️ Unknown action. Please try again.")
                
        except Exception as e:
            logger.error(f"Error in button handler: {e}")
            try:
                await query.answer("❌ An error occurred. Please try again.", show_alert=True)
            except:
                pass
    
    async def handle_screenshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            
            if "waiting_for_screenshot" not in context.user_data:
                await update.message.reply_text("❌ Please select a campaign first before uploading screenshots.\n\nUse /campaigns to see available tasks.")
                return
            
            campaign_id = context.user_data["waiting_for_screenshot"]
            campaign = await campaign_model.get_campaign(campaign_id)
            
            if not campaign:
                await update.message.reply_text("❌ Campaign not found or no longer available.")
                del context.user_data["waiting_for_screenshot"]
                return
            
            # Save screenshot for admin approval
            photo = update.message.photo[-1]  # Get highest resolution
            file_id = photo.file_id
            
            # Create submission record
            if db.client is not None:
                try:
                    submission = {
                        "user_id": user_id,
                        "campaign_id": campaign_id,
                        "screenshot_file_id": file_id,
                        "status": "pending",
                        "submitted_at": datetime.utcnow(),
                        "amount": campaign["reward"],
                        "campaign_title": campaign["title"]
                    }
                    
                    submission_collection = db.client.walletbot.submissions
                    await submission_collection.insert_one(submission)
                    logger.info(f"Screenshot submitted by user {user_id} for campaign {campaign_id}")
                except Exception as e:
                    logger.error(f"Error saving submission: {e}")
            
            await update.message.reply_text(
                f"✅ **Screenshot submitted successfully!**\n\n"
                f"🎯 Campaign: {campaign['title']}\n"
                f"💰 Reward: ₹{campaign['reward']:.2f}\n\n"
                f"⏳ Your submission is under review. You will be notified once approved!\n\n"
                f"🔄 You can continue with other campaigns while waiting.",
                parse_mode="Markdown"
            )
            
            # Clear waiting state
            del context.user_data["waiting_for_screenshot"]
            
        except Exception as e:
            logger.error(f"Error handling screenshot: {e}")
            try:
                await update.message.reply_text("❌ Error processing screenshot. Please try again.")
            except:
                pass
    
    async def complete_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE, campaign_id: str):
        try:
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
                    f"💳 Check your wallet balance with /wallet\n"
                    f"📋 Continue with more campaigns to earn more!",
                    parse_mode="Markdown"
                )
            else:
                await update.callback_query.answer("❌ Error processing reward! Please try again.", show_alert=True)
                
        except Exception as e:
            logger.error(f"Error completing campaign: {e}")
            try:
                await update.callback_query.answer("❌ Error processing completion!", show_alert=True)
            except:
                pass
    
    async def show_withdrawal_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            if not user:
                await update.callback_query.edit_message_text("❌ User not found.")
                return
            
            min_withdrawal = await settings_model.get_setting("min_withdrawal") or 6
            balance = user.get("wallet_balance", 0)
            
            if balance < min_withdrawal:
                await update.callback_query.edit_message_text(
                    f"❌ **Insufficient Balance**\n\n"
                    f"💰 Your Balance: ₹{balance:.2f}\n"
                    f"🎯 Minimum Withdrawal: ₹{min_withdrawal}\n"
                    f"📈 Need: ₹{min_withdrawal - balance:.2f} more\n\n"
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
                f"💰 Available Balance: ₹{balance:.2f}\n"
                f"🎯 Minimum Withdrawal: ₹{min_withdrawal}\n\n"
                f"Choose your preferred withdrawal method:\n\n"
                f"⚠️ Withdrawals are processed within 24-48 hours.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Error showing withdrawal options: {e}")
            try:
                await update.callback_query.edit_message_text("❌ Error loading withdrawal options.")
            except:
                pass
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            text = update.message.text
            user_id = update.effective_user.id
            
            logger.info(f"Message from user {user_id}: {text[:50]}...")
            
            # Default response
            await update.message.reply_text(
                "👋 Hi! Use the menu buttons or commands to navigate:\n\n"
                "• /start - Main menu\n"
                "• /wallet - Check your balance\n"
                "• /campaigns - View available tasks\n"
                "• /referral - Your referral program\n"
                "• /help - Show help\n\n"
                "💡 Use the buttons in messages for easier navigation!"
            )
        except Exception as e:
            logger.error(f"Error handling message: {e}")

# Initialize bot with error handling
wallet_bot = None
try:
    wallet_bot = WalletBot()
    logger.info(f"Bot initialization status: {wallet_bot.initialized}")
except Exception as e:
    logger.error(f"Failed to initialize bot: {e}")

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
        # Check if application is properly initialized
        if not wallet_bot or wallet_bot.application is None:
            logger.error("Application not available")
            return {"status": "error", "message": "Application not available"}
        
        # Check if application is initialized
        if not hasattr(wallet_bot.application, '_initialized') or not wallet_bot.application._initialized:
            logger.error("Application not initialized")
            return {"status": "error", "message": "Application not initialized"}
        
        # Process the update
        telegram_update = Update.de_json(update, wallet_bot.bot)
        if telegram_update:
            await wallet_bot.application.process_update(telegram_update)
            logger.debug(f"Update processed successfully: {telegram_update.update_id}")
            return {"status": "ok"}
        else:
            logger.warning("Failed to parse telegram update")
            return {"status": "error", "message": "Invalid update format"}
            
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

# Health check endpoints
@app.get("/health")
async def health_check():
    app_initialized = False
    bot_initialized = False
    
    if wallet_bot and wallet_bot.application is not None:
        app_initialized = hasattr(wallet_bot.application, '_initialized') and wallet_bot.application._initialized
    
    if wallet_bot and wallet_bot.bot is not None:
        bot_initialized = hasattr(wallet_bot.bot, '_initialized') and wallet_bot.bot._initialized
    
    status = {
        "status": "healthy",
        "service": "wallet-bot",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": db.connected,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "telegram_app_initialized": app_initialized,
        "telegram_bot_object_initialized": bot_initialized,
        "version": "1.0.0"
    }
    return status

@app.get("/")
async def root():
    return {
        "message": "Telegram Wallet Bot API",
        "status": "running",
        "version": "1.0.0",
        "mongodb_status": "connected" if db.connected else "disconnected",
        "bot_status": "initialized" if wallet_bot and wallet_bot.initialized else "error",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health",
            "admin": "/api/admin/*"
        }
    }

# Admin Panel APIs
@app.get("/api/admin/dashboard")
async def get_dashboard(admin: str = Depends(authenticate_admin)):
    """Get admin dashboard stats"""
    try:
        if db.client is None:
            return {
                "users_count": 0,
                "active_campaigns": 0,
                "pending_submissions": 0,
                "total_withdrawals": 0,
                "status": "database_disconnected"
            }
            
        users_count = await db.client.walletbot.users.count_documents({})
        active_campaigns = await db.client.walletbot.campaigns.count_documents({"is_active": True})
        pending_submissions = await db.client.walletbot.submissions.count_documents({"status": "pending"})
        total_withdrawals = await db.client.walletbot.transactions.count_documents({"type": "withdrawal"})
        
        # Calculate total wallet balance
        pipeline = [
            {"$group": {"_id": None, "total_balance": {"$sum": "$wallet_balance"}}}
        ]
        result = await db.client.walletbot.users.aggregate(pipeline).to_list(length=1)
        total_balance = result[0]["total_balance"] if result else 0
        
        return {
            "users_count": users_count,
            "active_campaigns": active_campaigns,
            "pending_submissions": pending_submissions,
            "total_withdrawals": total_withdrawals,
            "total_wallet_balance": total_balance,
            "status": "ok"
        }
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/users")
async def get_users(skip: int = 0, limit: int = 100, admin: str = Depends(authenticate_admin)):
    """Get all users with pagination"""
    try:
        if db.client is None:
            return {"users": [], "total": 0, "status": "database_disconnected"}
            
        total = await db.client.walletbot.users.count_documents({})
        cursor = db.client.walletbot.users.find({}).sort("created_at", -1).skip(skip).limit(limit)
        users = await cursor.to_list(length=None)
        
        # Convert ObjectId to string for JSON serialization
        for user in users:
            user["_id"] = str(user["_id"])
            # Add formatted date
            user["created_at_formatted"] = user.get("created_at", datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
        
        return {"users": users, "total": total, "status": "ok"}
    except Exception as e:
        logger.error(f"Get users error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
    try:
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
        logger.info(f"Campaign created by admin: {title}")
        return {"status": "success", "campaign_id": campaign_id}
    except Exception as e:
        logger.error(f"Create campaign error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/campaigns")
async def get_campaigns(admin: str = Depends(authenticate_admin)):
    """Get all campaigns"""
    try:
        campaigns = await campaign_model.get_active_campaigns()
        
        # Convert ObjectId to string and add formatted dates
        for campaign in campaigns:
            campaign["_id"] = str(campaign["_id"])
            campaign["created_at_formatted"] = campaign.get("created_at", datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
        
        return {"campaigns": campaigns, "status": "ok"}
    except Exception as e:
        logger.error(f"Get campaigns error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/submissions")
async def get_submissions(status: str = "pending", admin: str = Depends(authenticate_admin)):
    """Get submissions by status"""
    try:
        if db.client is None:
            return {"submissions": [], "status": "database_disconnected"}
        
        query = {"status": status} if status != "all" else {}
        cursor = db.client.walletbot.submissions.find(query).sort("submitted_at", -1)
        submissions = await cursor.to_list(length=None)
        
        # Get user and campaign details
        for submission in submissions:
            submission["_id"] = str(submission["_id"])
            submission["campaign_id"] = str(submission["campaign_id"])
            submission["submitted_at_formatted"] = submission.get("submitted_at", datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
            
            # Get user info
            user = await user_model.get_user(submission["user_id"])
            submission["user_name"] = user["first_name"] if user else "Unknown"
            submission["user_username"] = user.get("username", "N/A") if user else "N/A"
            
            # Get campaign info
            campaign = await campaign_model.get_campaign(submission["campaign_id"])
            submission["campaign_title"] = campaign["title"] if campaign else "Unknown"
        
        return {"submissions": submissions, "status": "ok"}
    except Exception as e:
        logger.error(f"Get submissions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/submissions/{submission_id}/approve")
async def approve_submission(submission_id: str, admin: str = Depends(authenticate_admin)):
    """Approve a submission"""
    try:
        if db.client is None:
            raise HTTPException(status_code=500, detail="Database not connected")
            
        submission = await db.client.walletbot.submissions.find_one({"_id": ObjectId(submission_id)})
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        # Add money to user's wallet
        success = await user_model.add_to_wallet(
            submission["user_id"],
            submission["amount"],
            "campaign",
            f"Screenshot approved: {submission.get('campaign_title', 'Campaign')}"
        )
        
        if success:
            # Update submission status
            await db.client.walletbot.submissions.update_one(
                {"_id": ObjectId(submission_id)},
                {"$set": {"status": "approved", "approved_at": datetime.utcnow(), "approved_by": admin}}
            )
            
            # Notify user via Telegram
            try:
                if wallet_bot and wallet_bot.bot is not None:
                    await wallet_bot.bot.send_message(
                        submission["user_id"],
                        f"✅ **Screenshot Approved!**\n\n"
                        f"🎯 Campaign: {submission.get('campaign_title', 'Campaign')}\n"
                        f"💰 ₹{submission['amount']:.2f} has been added to your wallet!\n\n"
                        f"💳 Check your balance with /wallet\n"
                        f"📋 Continue with more campaigns to earn more!",
                        parse_mode="Markdown"
                    )
            except Exception as notify_error:
                logger.warning(f"Could not notify user {submission['user_id']}: {notify_error}")
            
            logger.info(f"Submission approved by {admin}: {submission_id}")
            return {"status": "approved"}
        else:
            raise HTTPException(status_code=500, detail="Error processing approval")
    except Exception as e:
        logger.error(f"Approve submission error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/submissions/{submission_id}/reject")
async def reject_submission(submission_id: str, reason: str = Form(...), admin: str = Depends(authenticate_admin)):
    """Reject a submission"""
    try:
        if db.client is None:
            raise HTTPException(status_code=500, detail="Database not connected")
            
        submission = await db.client.walletbot.submissions.find_one({"_id": ObjectId(submission_id)})
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        # Update submission status
        await db.client.walletbot.submissions.update_one(
            {"_id": ObjectId(submission_id)},
            {"$set": {
                "status": "rejected", 
                "rejection_reason": reason, 
                "rejected_at": datetime.utcnow(),
                "rejected_by": admin
            }}
        )
        
        # Notify user via Telegram
        try:
            if wallet_bot and wallet_bot.bot is not None:
                await wallet_bot.bot.send_message(
                    submission["user_id"],
                    f"❌ **Screenshot Rejected**\n\n"
                    f"🎯 Campaign: {submission.get('campaign_title', 'Campaign')}\n"
                    f"**Reason:** {reason}\n\n"
                    f"💡 Please review the task requirements and try again with a proper screenshot.\n"
                    f"📋 You can resubmit for this campaign.",
                    parse_mode="Markdown"
                )
        except Exception as notify_error:
            logger.warning(f"Could not notify user {submission['user_id']}: {notify_error}")
        
        logger.info(f"Submission rejected by {admin}: {submission_id} - {reason}")
        return {"status": "rejected"}
    except Exception as e:
        logger.error(f"Reject submission error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/settings")
async def get_settings(admin: str = Depends(authenticate_admin)):
    """Get all settings"""
    try:
        settings = {}
        
        if db.client is not None:
            # Get all settings
            cursor = db.client.walletbot.settings.find({})
            async for setting in cursor:
                settings[setting["key"]] = setting["value"]
        
        # Set defaults if not exists
        default_settings = {
            "min_withdrawal": 6,
            "referral_amount": 10,
            "welcome_message": "🎉 Welcome to Cashback Wallet Bot!\n\n💰 Earn money by completing simple tasks\n💳 Instant payments to your wallet\n👥 Refer friends and earn bonus\n📱 Easy withdrawal system\n\nClick the buttons below to get started:",
            "force_channels": [],
            "payment_gateway_api": "",
            "support_username": "",
            "bot_status": "active"
        }
        
        for key, value in default_settings.items():
            if key not in settings:
                settings[key] = value
        
        return {"settings": settings, "status": "ok"}
    except Exception as e:
        logger.error(f"Get settings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/settings")
async def update_settings(settings_data: dict, admin: str = Depends(authenticate_admin)):
    """Update settings"""
    try:
        updated_count = 0
        for key, value in settings_data.items():
            success = await settings_model.update_setting(key, value)
            if success:
                updated_count += 1
        
        logger.info(f"Settings updated by {admin}: {updated_count} settings")
        return {"status": "updated", "updated_count": updated_count}
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Campaign management
@app.delete("/api/admin/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, admin: str = Depends(authenticate_admin)):
    """Delete/deactivate a campaign"""
    try:
        if db.client is None:
            raise HTTPException(status_code=500, detail="Database not connected")
        
        result = await campaign_model.update_campaign(campaign_id, {"is_active": False})
        if result:
            logger.info(f"Campaign deactivated by {admin}: {campaign_id}")
            return {"status": "deactivated"}
        else:
            raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Delete campaign error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# User management
@app.post("/api/admin/users/{user_id}/add_balance")
async def add_user_balance(
    user_id: int, 
    amount: float = Form(...), 
    description: str = Form(...), 
    admin: str = Depends(authenticate_admin)
):
    """Add balance to user wallet"""
    try:
        success = await user_model.add_to_wallet(user_id, amount, "admin_add", f"Admin bonus: {description}")
        if success:
            logger.info(f"Balance added by {admin}: ₹{amount} to user {user_id}")
            return {"status": "success", "message": f"₹{amount} added to user wallet"}
        else:
            raise HTTPException(status_code=404, detail="User not found or error processing")
    except Exception as e:
        logger.error(f"Add balance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup and shutdown events (FINAL FIXED VERSION WITH ALL FIXES)
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up Wallet Bot API...")
    
    # Connect to MongoDB
    await connect_to_mongo()
    
    # Initialize and start the Telegram Application
    if wallet_bot and wallet_bot.application is not None:
        try:
            # Initialize the Bot object first (CRITICAL FIX)
            await wallet_bot.bot.initialize()
            logger.info("Telegram Bot initialized successfully")
            
            # Initialize the application
            await wallet_bot.application.initialize()
            logger.info("Telegram Application initialized successfully")
            
            # Start the application
            await wallet_bot.application.start()
            logger.info("Telegram Application started successfully")
            
            # Set webhook if URL is provided
            if WEBHOOK_URL and wallet_bot.bot is not None:
                webhook_url = f"{WEBHOOK_URL}/webhook"
                
                # Delete existing webhook first
                await wallet_bot.bot.delete_webhook(drop_pending_updates=True)
                await asyncio.sleep(1)
                
                # Set new webhook
                result = await wallet_bot.bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=["message", "callback_query"],
                    drop_pending_updates=True
                )
                
                if result:
                    logger.info(f"Webhook set successfully: {webhook_url}")
                    
                    # Verify webhook
                    webhook_info = await wallet_bot.bot.get_webhook_info()
                    logger.info(f"Webhook verified: {webhook_info.url}")
                else:
                    logger.warning("Failed to set webhook")
            else:
                logger.warning("WEBHOOK_URL not set - bot will not receive updates")
                
        except Exception as e:
            logger.error(f"Error during application startup: {e}")
    else:
        logger.error("Wallet bot not properly initialized")
    
    logger.info("Startup completed")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Wallet Bot API...")
    
    # Properly shutdown the Telegram Application
    if wallet_bot and wallet_bot.application is not None:
        try:
            # Remove webhook
            if wallet_bot.bot is not None:
                await wallet_bot.bot.delete_webhook()
                logger.info("Webhook removed")
            
            # Stop and shutdown application
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            
            # Shutdown bot (ADDED)
            if wallet_bot.bot is not None:
                await wallet_bot.bot.shutdown()
            logger.info("Telegram Application and Bot shutdown completed")
            
        except Exception as e:
            logger.warning(f"Error during application shutdown: {e}")
    
    # Close MongoDB connection
    await close_mongo_connection()
    
    logger.info("Shutdown completed")

# Run the application
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
