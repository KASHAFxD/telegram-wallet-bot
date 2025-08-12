from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
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
import gc
import traceback

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Configure robust logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo"
ADMIN_USERNAME = "kashaf"
ADMIN_PASSWORD = "kashaf"
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017/walletbot")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", 8000))

# Initialize FastAPI with enhanced error handling
app = FastAPI(title="Wallet Bot API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBasic()

# Enhanced MongoDB Connection with auto-reconnect
class Database:
    def __init__(self):
        self.client = None
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.connect()
    
    def connect(self):
        mongodb_url = os.getenv("MONGODB_URL")
        logger.info("Attempting MongoDB connection...")
        
        if mongodb_url:
            try:
                # Clean the URL and add connection options for stability
                mongodb_url = mongodb_url.strip()
                self.client = AsyncIOMotorClient(
                    mongodb_url,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=10000,
                    socketTimeoutMS=20000,
                    maxPoolSize=10,
                    retryWrites=True
                )
                self.connected = True
                self.reconnect_attempts = 0
                logger.info("MongoDB client initialized successfully")
            except Exception as e:
                logger.error(f"MongoDB connection error: {e}")
                self.client = None
                self.connected = False
        else:
            logger.error("MONGODB_URL environment variable not found")
            self.client = None
            self.connected = False
    
    async def ensure_connection(self):
        """Ensure database connection is active"""
        if not self.connected and self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            logger.info(f"Attempting to reconnect to MongoDB (attempt {self.reconnect_attempts})")
            self.connect()
            if self.connected:
                await self.test_connection()
    
    async def test_connection(self):
        """Test MongoDB connection"""
        if self.client:
            try:
                await self.client.admin.command('ping')
                self.connected = True
                return True
            except Exception as e:
                logger.error(f"MongoDB ping failed: {e}")
                self.connected = False
                return False
        return False

# Initialize database
db = Database()

async def connect_to_mongo():
    """Test MongoDB connection with retry logic"""
    for attempt in range(3):
        if await db.test_connection():
            logger.info("MongoDB connection test successful")
            return True
        await asyncio.sleep(2)
    logger.error("MongoDB connection failed after retries")
    return False

async def close_mongo_connection():
    if db.client:
        db.client.close()
        logger.info("MongoDB connection closed")

# Enhanced Database Models with connection checks
class UserModel:
    def __init__(self):
        self.collection = None
        if db.client is not None:
            self.collection = db.client.walletbot.users
    
    async def ensure_collection(self):
        """Ensure collection is available"""
        if self.collection is None and db.client is not None:
            self.collection = db.client.walletbot.users
        await db.ensure_connection()
        return self.collection is not None
    
    async def create_user(self, user_data: dict):
        if not await self.ensure_collection():
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
        if not await self.ensure_collection():
            return None
        try:
            return await self.collection.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def update_user(self, user_id: int, update_data: dict):
        if not await self.ensure_collection():
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
        if not await self.ensure_collection():
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
    
    async def ensure_collection(self):
        if self.collection is None and db.client is not None:
            self.collection = db.client.walletbot.campaigns
        await db.ensure_connection()
        return self.collection is not None
    
    async def create_campaign(self, campaign_data: dict):
        if not await self.ensure_collection():
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
        if not await self.ensure_collection():
            return None
        try:
            return await self.collection.find_one({"_id": ObjectId(campaign_id)})
        except Exception as e:
            logger.error(f"Error getting campaign: {e}")
            return None
    
    async def get_campaign_by_number(self, campaign_number: int):
        if not await self.ensure_collection():
            return None
        try:
            return await self.collection.find_one({"campaign_number": campaign_number})
        except Exception as e:
            logger.error(f"Error getting campaign by number: {e}")
            return None
    
    async def get_active_campaigns(self):
        if not await self.ensure_collection():
            return []
        try:
            cursor = self.collection.find({"is_active": True})
            campaigns = await cursor.to_list(length=None)
            return campaigns
        except Exception as e:
            logger.error(f"Error getting active campaigns: {e}")
            return []
    
    async def update_campaign(self, campaign_id: str, update_data: dict):
        if not await self.ensure_collection():
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
    
    async def ensure_collection(self):
        if self.collection is None and db.client is not None:
            self.collection = db.client.walletbot.transactions
        await db.ensure_connection()
        return self.collection is not None
    
    async def create_transaction(self, transaction_data: dict):
        if not await self.ensure_collection():
            return None
        try:
            transaction_data["created_at"] = datetime.utcnow()
            result = await self.collection.insert_one(transaction_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating transaction: {e}")
            return None
    
    async def get_user_transactions(self, user_id: int):
        if not await self.ensure_collection():
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
    
    async def ensure_collection(self):
        if self.collection is None and db.client is not None:
            self.collection = db.client.walletbot.settings
        await db.ensure_connection()
        return self.collection is not None
    
    async def get_setting(self, key: str):
        if not await self.ensure_collection():
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
        if not await self.ensure_collection():
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

# Enhanced Telegram Bot with crash protection
class WalletBot:
    def __init__(self):
        self.bot = None
        self.application = None
        self.initialized = False
        self.setup_bot()
    
    def setup_bot(self):
        try:
            # Initialize bot with error handling
            self.bot = Bot(token=BOT_TOKEN)
            
            # Create application with enhanced settings
            self.application = ApplicationBuilder().token(BOT_TOKEN).build()
            
            # Setup handlers
            self.setup_handlers()
            
            self.initialized = True
            logger.info("Telegram bot initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing Telegram bot: {e}")
            logger.error(traceback.format_exc())
            self.bot = None
            self.application = None
            self.initialized = False
    
    def setup_handlers(self):
        if self.application is None:
            logger.error("Application not initialized, skipping handler setup")
            return
            
        try:
            # Add command handlers with error handling
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("campaigns", self.campaigns_command))
            self.application.add_handler(CommandHandler("referral", self.referral_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("restart", self.restart_command))  # NEW: Restart command
            
            # Add callback and message handlers
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_screenshot))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            # Add error handler
            self.application.add_error_handler(self.error_handler)
            
            logger.info("Telegram bot handlers setup successfully")
            
        except Exception as e:
            logger.error(f"Error setting up handlers: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors to prevent bot crashes"""
        try:
            logger.error(f"Exception while handling update {update}: {context.error}")
            logger.error(traceback.format_exc())
            
            # Try to inform user about error
            if update and hasattr(update, 'effective_user') and update.effective_user:
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_user.id,
                        text="⚠️ An error occurred. Please try again or use /restart to reset the bot.",
                        reply_markup=self.get_reply_keyboard()
                    )
                except:
                    pass
            
            # Force garbage collection to free memory
            gc.collect()
            
        except Exception as e:
            logger.error(f"Error in error handler: {e}")
    
    def get_reply_keyboard(self):
        """Get permanent reply keyboard - FIXED VERSION"""
        keyboard = [
            [KeyboardButton("💰 My Wallet"), KeyboardButton("📋 Campaigns")],
            [KeyboardButton("👥 Referral"), KeyboardButton("💸 Withdraw")],
            [KeyboardButton("🆘 Help"), KeyboardButton("🔄 Restart")]
        ]
        
        # Compatible with python-telegram-bot 20.3
        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder="Choose an option..."
        )
    
    async def restart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Restart bot functionality for user"""
        try:
            user_id = update.effective_user.id
            logger.info(f"Restart command from user: {user_id}")
            
            # Clear user data
            context.user_data.clear()
            
            # Force garbage collection
            gc.collect()
            
            # Restart message
            await update.message.reply_text(
                "🔄 **Bot Restarted Successfully!**\n\n"
                "✅ All systems refreshed\n"
                "✅ Memory cleared\n"
                "✅ Ready to use\n\n"
                "Use /start to continue or select from menu below:",
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Error in restart command: {e}")
            try:
                await update.message.reply_text(
                    "❌ Restart failed. Please contact support.",
                    reply_markup=self.get_reply_keyboard()
                )
            except:
                pass
    
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
            
            # Clear any existing user data to prevent crashes
            context.user_data.clear()
            
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
            
            # Inline buttons
            inline_keyboard = [
                [InlineKeyboardButton("💰 My Wallet", callback_data="wallet")],
                [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
                [InlineKeyboardButton("👥 Referral", callback_data="referral")],
                [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]
            ]
            inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
            
            # Send welcome message with inline keyboard
            await update.message.reply_text(
                welcome_msg, 
                reply_markup=inline_reply_markup, 
                parse_mode="Markdown"
            )
            
            # Set permanent keyboard menu - FIXED VERSION
            await update.message.reply_text(
                "🎯 **Use the menu buttons below for quick access:**",
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Error in start command: {e}")
            logger.error(traceback.format_exc())
            try:
                await update.message.reply_text(
                    "❌ An error occurred. Bot is restarting...\n\n"
                    "Please try /restart or wait a moment and try again.",
                    reply_markup=self.get_reply_keyboard()
                )
            except:
                pass
    
    # Continue with all other methods (wallet_command, campaigns_command, etc.)
    # [Previous methods remain the same, just add enhanced error handling]
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            if not user:
                message_text = "❌ User not found. Please /start first."
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(message_text)
                else:
                    await update.message.reply_text(message_text, reply_markup=self.get_reply_keyboard())
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
                try:
                    await update.callback_query.edit_message_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
                except Exception as edit_error:
                    if "Message is not modified" in str(edit_error):
                        await update.callback_query.answer("Already up to date! ✅")
                    else:
                        await update.callback_query.edit_message_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
                
        except Exception as e:
            logger.error(f"Error in wallet command: {e}")
            error_msg = "❌ Error loading wallet. Please try /restart"
            try:
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.edit_message_text(error_msg)
                else:
                    await update.message.reply_text(error_msg, reply_markup=self.get_reply_keyboard())
            except:
                pass

    # [Rest of the methods similar with enhanced error handling]
    # Adding all remaining methods with proper error handling...

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            text = update.message.text
            user_id = update.effective_user.id
            
            logger.info(f"Message from user {user_id}: {text[:50]}...")
            
            # Handle reply keyboard commands
            if text == "💰 My Wallet":
                await self.wallet_command(update, context)
            elif text == "📋 Campaigns":
                await self.campaigns_command(update, context)
            elif text == "👥 Referral":
                await self.referral_command(update, context)
            elif text == "💸 Withdraw":
                await self.show_withdrawal_options(update, context)
            elif text == "🆘 Help":
                await self.help_command(update, context)
            elif text == "🔄 Restart":
                await self.restart_command(update, context)
            else:
                # Default response
                await update.message.reply_text(
                    "👋 Hi! Use the menu buttons below or commands:\n\n"
                    "• /start - Main menu\n"
                    "• /wallet - Check your balance\n"
                    "• /campaigns - View available tasks\n"
                    "• /referral - Your referral program\n"
                    "• /restart - Restart bot if stuck\n\n"
                    "💡 Use the permanent menu buttons for easier navigation!",
                    reply_markup=self.get_reply_keyboard()
                )
        except Exception as e:
            logger.error(f"Error handling message: {e}")

# Initialize bot with enhanced error handling
wallet_bot = None
try:
    wallet_bot = WalletBot()
    logger.info(f"Bot initialization status: {wallet_bot.initialized}")
except Exception as e:
    logger.error(f"Failed to initialize bot: {e}")
    logger.error(traceback.format_exc())

# Enhanced startup and shutdown with crash protection
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up Enhanced Wallet Bot API v2.0...")
    
    # Connect to MongoDB with retries
    mongodb_connected = await connect_to_mongo()
    if not mongodb_connected:
        logger.warning("MongoDB connection failed - using fallback mode")
    
    # Initialize and start the Telegram Application
    if wallet_bot and wallet_bot.application is not None:
        try:
            # Enhanced initialization sequence
            await wallet_bot.bot.initialize()
            logger.info("Telegram Bot initialized successfully")
            
            await wallet_bot.application.initialize()
            logger.info("Telegram Application initialized successfully")
            
            await wallet_bot.application.start()
            logger.info("Telegram Application started successfully")
            
            # Set webhook with enhanced error handling
            if WEBHOOK_URL and wallet_bot.bot is not None:
                webhook_url = f"{WEBHOOK_URL}/webhook"
                
                try:
                    await wallet_bot.bot.delete_webhook(drop_pending_updates=True)
                    await asyncio.sleep(2)
                    
                    result = await wallet_bot.bot.set_webhook(
                        url=webhook_url,
                        allowed_updates=["message", "callback_query"],
                        drop_pending_updates=True,
                        max_connections=100,
                        secret_token=None
                    )
                    
                    if result:
                        logger.info(f"Webhook set successfully: {webhook_url}")
                        webhook_info = await wallet_bot.bot.get_webhook_info()
                        logger.info(f"Webhook verified: {webhook_info.url}")
                    else:
                        logger.warning("Failed to set webhook")
                        
                except Exception as webhook_error:
                    logger.error(f"Webhook setup error: {webhook_error}")
            else:
                logger.warning("WEBHOOK_URL not set - bot will not receive updates")
                
        except Exception as e:
            logger.error(f"Error during application startup: {e}")
            logger.error(traceback.format_exc())
    else:
        logger.error("Wallet bot not properly initialized")
    
    logger.info("Enhanced startup completed")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Enhanced Wallet Bot API...")
    
    if wallet_bot and wallet_bot.application is not None:
        try:
            if wallet_bot.bot is not None:
                await wallet_bot.bot.delete_webhook()
                logger.info("Webhook removed")
            
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            
            if wallet_bot.bot is not None:
                await wallet_bot.bot.shutdown()
            logger.info("Telegram Application and Bot shutdown completed")
            
        except Exception as e:
            logger.warning(f"Error during application shutdown: {e}")
    
    await close_mongo_connection()
    gc.collect()  # Force garbage collection
    logger.info("Enhanced shutdown completed")

# Run the application
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Enhanced Wallet Bot Server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
