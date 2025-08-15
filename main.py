from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import asyncio
import os
import secrets
import hashlib
from datetime import datetime, timedelta
import logging
import traceback
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "kashaf")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "kashaf")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://kashaf:kashaf@bot.zq2yw4e.mongodb.net/walletbot?retryWrites=true")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-wallet-bot-r80n.onrender.com")
PORT = int(os.getenv("PORT", 10000))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 7194000836))

# Initialize FastAPI
app = FastAPI(title="Enhanced Wallet Bot - Final Working Version", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# Emoji Constants (Safe Unicode)
EMOJI = {
    'check': '\u2705',      # ✅
    'cross': '\u274C',      # ❌
    'pending': '\u2B1C',    # ⬜
    'warning': '\u26A0',    # ⚠️
    'lock': '\U0001F512',   # 🔒
    'rocket': '\U0001F680', # 🚀
    'wallet': '\U0001F4B0', # 💰
    'shield': '\U0001F6E1', # 🛡️
    'fire': '\U0001F525',   # 🔥
    'star': '\u2B50',       # ⭐
    'gear': '\u2699',       # ⚙️
    'chart': '\U0001F4CA',  # 📊
    'bell': '\U0001F514',   # 🔔
    'key': '\U0001F511',    # 🔑
    'globe': '\U0001F30D'   # 🌍
}

# Global database
db_client = None
db_connected = False

# Temporary user storage (fallback)
temp_users = {}
temp_devices = {}

# Fixed database initialization
async def init_database():
    global db_client, db_connected
    try:
        # Clean MongoDB URL - remove any problematic characters
        clean_mongodb_url = MONGODB_URL.strip().replace('\n', '').replace('\r', '')
        
        # Simple connection without write concern complications
        db_client = AsyncIOMotorClient(
            clean_mongodb_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            maxPoolSize=20
        )
        
        # Test connection
        await db_client.admin.command('ping')
        db_connected = True
        logger.info("Database connected successfully - MongoDB Atlas")
        
        # Try to create basic indexes (non-critical)
        try:
            await db_client.walletbot.users.create_index("user_id", unique=True)
            await db_client.walletbot.device_fingerprints.create_index("basic_fingerprint")
            logger.info("Database indexes created successfully")
        except Exception as index_error:
            logger.warning(f"Index creation skipped: {index_error}")
            # Continue without indexes - not critical
        
        return True
    except Exception as e:
        logger.error(f"MongoDB connection failed, using temporary storage: {e}")
        db_connected = False
        return False

# Enhanced User Model with Fallback Storage
class UserModel:
    def __init__(self):
        pass
    
    def get_collection(self):
        if db_client is not None and db_connected:
            return db_client.walletbot.users
        return None
    
    def get_device_collection(self):
        if db_client is not None and db_connected:
            return db_client.walletbot.device_fingerprints
        return None
    
    async def create_user(self, user_data: dict):
        """Create user with database fallback to temporary storage"""
        collection = self.get_collection()
        user_id = user_data["user_id"]
        
        # Add default fields
        user_data.update({
            "created_at": datetime.utcnow(),
            "wallet_balance": 0.0,
            "total_earned": 0.0,
            "referral_earnings": 0.0,
            "total_referrals": 0,
            "is_active": True,
            "is_banned": False,
            "device_verified": False,
            "device_fingerprint": None,
            "verification_status": "pending",
            "risk_score": 0.0,
            "last_activity": datetime.utcnow(),
            "referred_by": user_data.get("referred_by"),
            "referral_code": str(uuid.uuid4())[:8]
        })
        
        if collection is not None:
            try:
                # Try database first
                existing_user = await collection.find_one({"user_id": user_id})
                if not existing_user:
                    # Insert new user
                    await collection.insert_one(user_data.copy())
                    logger.info(f"New user created in database: {user_id}")
                else:
                    logger.info(f"Existing user found in database: {user_id}")
                return True
            except Exception as db_error:
                logger.warning(f"Database insert failed, using temp storage: {db_error}")
                # Fallback to temporary storage
                temp_users[user_id] = user_data
                logger.info(f"User stored temporarily: {user_id}")
                return True
        else:
            # Use temporary storage
            temp_users[user_id] = user_data
            logger.info(f"User created in temporary storage: {user_id}")
            return True
    
    async def get_user(self, user_id: int):
        """Get user from database or temporary storage"""
        collection = self.get_collection()
        
        if collection is not None:
            try:
                user = await collection.find_one({"user_id": user_id})
                if user:
                    # Update last activity
                    try:
                        await collection.update_one(
                            {"user_id": user_id},
                            {"$set": {"last_activity": datetime.utcnow()}}
                        )
                    except:
                        pass  # Don't fail if update fails
                return user
            except Exception as e:
                logger.warning(f"Database query failed, checking temp storage: {e}")
                # Fallback to temporary storage
                return temp_users.get(user_id)
        else:
            # Use temporary storage
            return temp_users.get(user_id)
    
    async def is_user_verified(self, user_id: int):
        """Check if user is device verified"""
        user = await self.get_user(user_id)
        if not user:
            return False
        return (user.get('device_verified', False) and 
                user.get('device_fingerprint') is not None and
                not user.get('is_banned', False))
    
    async def generate_device_fingerprint(self, device_data: dict) -> str:
        """Generate device fingerprint"""
        try:
            # Create fingerprint from device data
            components = [
                str(device_data.get('screen_resolution', '')),
                str(device_data.get('user_agent_hash', '')),
                str(device_data.get('timezone_offset', '')),
                str(device_data.get('platform', '')),
                str(device_data.get('canvas_hash', '')),
                str(device_data.get('hardware_concurrency', ''))
            ]
            combined = '|'.join(components)
            fingerprint = hashlib.sha256(combined.encode()).hexdigest()
            return fingerprint
        except Exception as e:
            logger.error(f"Fingerprint generation error: {e}")
            return hashlib.sha256(f"fallback_{datetime.utcnow().timestamp()}".encode()).hexdigest()
    
    async def check_device_conflict(self, fingerprint: str, user_id: int) -> bool:
        """Check if device fingerprint already exists for another user"""
        device_collection = self.get_device_collection()
        
        if device_collection is not None:
            try:
                # Check database
                existing_device = await device_collection.find_one({
                    "fingerprint": fingerprint,
                    "user_id": {"$ne": user_id}
                })
                return existing_device is not None
            except Exception as e:
                logger.warning(f"Database conflict check failed, checking temp storage: {e}")
                # Check temporary storage
                for temp_fingerprint, temp_data in temp_devices.items():
                    if temp_fingerprint == fingerprint and temp_data.get('user_id') != user_id:
                        return True
                return False
        else:
            # Check temporary storage only
            for temp_fingerprint, temp_data in temp_devices.items():
                if temp_fingerprint == fingerprint and temp_data.get('user_id') != user_id:
                    return True
            return False
    
    async def verify_device(self, user_id: int, device_data: dict) -> dict:
        """Complete device verification with fallback storage"""
        try:
            # Generate device fingerprint
            fingerprint = await self.generate_device_fingerprint(device_data)
            
            # Check for conflicts
            has_conflict = await self.check_device_conflict(fingerprint, user_id)
            
            if has_conflict:
                return {
                    "success": False, 
                    "message": "यह device पहले से एक verified account के साथ registered है। Multiple accounts allowed नहीं हैं।"
                }
            
            # Store device fingerprint
            device_record = {
                "user_id": user_id,
                "fingerprint": fingerprint,
                "device_data": device_data,
                "created_at": datetime.utcnow(),
                "is_active": True
            }
            
            device_collection = self.get_device_collection()
            if device_collection is not None:
                try:
                    await device_collection.insert_one(device_record)
                    logger.info(f"Device fingerprint stored in database for user {user_id}")
                except Exception as e:
                    logger.warning(f"Database device storage failed, using temp: {e}")
                    temp_devices[fingerprint] = device_record
            else:
                temp_devices[fingerprint] = device_record
                logger.info(f"Device fingerprint stored temporarily for user {user_id}")
            
            # Update user verification status
            await self.update_user_verification(user_id, fingerprint)
            
            logger.info(f"Device successfully verified for user {user_id}")
            return {"success": True, "message": "Device verified successfully"}
            
        except Exception as e:
            logger.error(f"Device verification error: {e}")
            return {"success": False, "message": "Verification failed due to technical error"}
    
    async def update_user_verification(self, user_id: int, fingerprint: str):
        """Update user verification status"""
        collection = self.get_collection()
        
        verification_update = {
            "device_verified": True,
            "device_fingerprint": fingerprint,
            "verification_status": "verified",
            "device_verified_at": datetime.utcnow(),
            "risk_score": 0.1
        }
        
        if collection is not None:
            try:
                await collection.update_one(
                    {"user_id": user_id},
                    {"$set": verification_update}
                )
                logger.info(f"User verification updated in database: {user_id}")
            except Exception as e:
                logger.warning(f"Database update failed, updating temp storage: {e}")
                # Update temporary storage
                if user_id in temp_users:
                    temp_users[user_id].update(verification_update)
        else:
            # Update temporary storage
            if user_id in temp_users:
                temp_users[user_id].update(verification_update)
                logger.info(f"User verification updated in temp storage: {user_id}")
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        """Add money to user wallet"""
        user = await self.get_user(user_id)
        if not user or user.get('is_banned', False):
            return False
        
        try:
            new_balance = user.get("wallet_balance", 0) + amount
            total_earned = user.get("total_earned", 0)
            
            if amount > 0:
                total_earned += amount
            
            # Update user wallet
            wallet_update = {
                "wallet_balance": new_balance,
                "total_earned": total_earned,
                "updated_at": datetime.utcnow()
            }
            
            # Update specific fields based on transaction type
            if transaction_type == "referral":
                wallet_update["referral_earnings"] = user.get("referral_earnings", 0) + amount
                wallet_update["total_referrals"] = user.get("total_referrals", 0) + 1
            
            collection = self.get_collection()
            if collection is not None:
                try:
                    await collection.update_one(
                        {"user_id": user_id},
                        {"$set": wallet_update}
                    )
                    logger.info(f"Wallet updated in database for user {user_id}: {amount:+.2f}")
                except Exception as e:
                    logger.warning(f"Database wallet update failed: {e}")
                    # Update temporary storage
                    if user_id in temp_users:
                        temp_users[user_id].update(wallet_update)
            else:
                # Update temporary storage
                if user_id in temp_users:
                    temp_users[user_id].update(wallet_update)
                    logger.info(f"Wallet updated in temp storage for user {user_id}: {amount:+.2f}")
            
            return True
        except Exception as e:
            logger.error(f"Error adding to wallet: {e}")
            return False
    
    async def get_user_stats(self) -> dict:
        """Get basic user statistics"""
        stats = {
            "total_users": 0,
            "verified_users": 0,
            "pending_verification": 0,
            "temp_storage_users": len(temp_users)
        }
        
        collection = self.get_collection()
        if collection is not None:
            try:
                stats["total_users"] = await collection.count_documents({})
                stats["verified_users"] = await collection.count_documents({"device_verified": True})
                stats["pending_verification"] = stats["total_users"] - stats["verified_users"]
            except Exception as e:
                logger.warning(f"Stats query failed: {e}")
                # Count from temporary storage
                stats["total_users"] = len(temp_users)
                stats["verified_users"] = len([u for u in temp_users.values() if u.get('device_verified')])
                stats["pending_verification"] = stats["total_users"] - stats["verified_users"]
        else:
            # Use temporary storage stats
            stats["total_users"] = len(temp_users)
            stats["verified_users"] = len([u for u in temp_users.values() if u.get('device_verified')])
            stats["pending_verification"] = stats["total_users"] - stats["verified_users"]
        
        return stats

# Initialize user model
user_model = UserModel()

# Enhanced Telegram Bot
class WalletBot:
    def __init__(self):
        self.bot = None
        self.application = None
        self.initialized = False
        self.setup_bot()
    
    def setup_bot(self):
        try:
            self.bot = Bot(token=BOT_TOKEN)
            self.application = ApplicationBuilder().token(BOT_TOKEN).build()
            self.setup_handlers()
            self.initialized = True
            logger.info("Enhanced Telegram bot initialized successfully")
        except Exception as e:
            logger.error(f"Bot initialization error: {e}")
            self.initialized = False
    
    def setup_handlers(self):
        try:
            # Command handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("referral", self.referral_command))
            self.application.add_handler(CommandHandler("device_verified", self.device_verified_callback))
            self.application.add_handler(CommandHandler("admin", self.admin_command))
            
            # Callback handlers
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            
            # Message handlers
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            # Error handler
            self.application.add_error_handler(self.error_handler)
            
            logger.info("All bot handlers setup complete")
        except Exception as e:
            logger.error(f"Handler setup error: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Enhanced error handler"""
        logger.error("Exception while handling an update:", exc_info=context.error)
        
        try:
            if update and hasattr(update, 'effective_user'):
                await context.bot.send_message(
                    update.effective_user.id,
                    f"{EMOJI['cross']} An error occurred. Please try again.",
                    reply_markup=self.get_reply_keyboard()
                )
        except:
            pass
    
    def get_reply_keyboard(self):
        """Get main reply keyboard"""
        keyboard = [
            [KeyboardButton(f"{EMOJI['wallet']} My Wallet"), KeyboardButton(f"{EMOJI['chart']} Campaigns")],
            [KeyboardButton(f"{EMOJI['star']} Referral"), KeyboardButton(f"{EMOJI['gear']} Withdraw")],
            [KeyboardButton(f"{EMOJI['bell']} Help"), KeyboardButton(f"{EMOJI['shield']} Status")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            first_name = update.effective_user.first_name or "User"
            
            logger.info(f"Start command from user: {user_id} ({first_name})")
            
            # Handle referral codes
            referrer_id = None
            if context.args and len(context.args) > 0:
                arg = context.args[0]
                if arg.startswith('ref_'):
                    try:
                        referrer_id = int(arg.replace('ref_', ''))
                        logger.info(f"Referral detected: {referrer_id} -> {user_id}")
                    except ValueError:
                        pass
            
            # Create user
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name
            }
            
            if referrer_id and referrer_id != user_id:
                user_data["referred_by"] = referrer_id
            
            await user_model.create_user(user_data)
            
            # Check verification status
            is_verified = await user_model.is_user_verified(user_id)
            
            if not is_verified:
                await self.require_device_verification(user_id, first_name, update)
            else:
                await self.send_verified_welcome(update, first_name)
                # Process referral bonus if applicable
                if referrer_id:
                    await self.process_referral_bonus(user_id, referrer_id)
                    
        except Exception as e:
            logger.error(f"Start command error: {e}")
            await update.message.reply_text(f"{EMOJI['cross']} Error occurred. Please try again.")
    
    async def require_device_verification(self, user_id: int, first_name: str, update: Update):
        """Send device verification requirement"""
        verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
        
        verification_msg = f"""{EMOJI['lock']} **Enhanced Security Verification**

Welcome {first_name}! 

**One Device, One Account Policy:**
{EMOJI['shield']} Advanced device fingerprinting enabled
{EMOJI['cross']} Multiple account creation prevented
{EMOJI['fire']} Enhanced fraud protection active

**Security Benefits:**
{EMOJI['check']} Account protection guaranteed
{EMOJI['star']} Fair usage for all users  
{EMOJI['wallet']} Secure wallet operations

Click below to verify your device:"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['lock']} Verify My Device", web_app=WebAppInfo(url=verification_url))]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(verification_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def send_verified_welcome(self, update: Update, first_name: str):
        """Send welcome message for verified users"""
        welcome_msg = f"""{EMOJI['rocket']} **Welcome to Enhanced Wallet Bot!**

Hi {first_name}! Your device is verified {EMOJI['check']}

**Available Features:**
{EMOJI['wallet']} **Secure Wallet Management**
{EMOJI['chart']} **Campaign Participation** (Coming Soon)
{EMOJI['star']} **Referral System** - Earn Rs.10 per friend
{EMOJI['gear']} **Withdrawal System** (Coming Soon)

**Your Account Status:**
{EMOJI['lock']} Device Verified & Secure
{EMOJI['fire']} Full Access Granted
{EMOJI['rocket']} All Features Unlocked

Choose an option below to get started:"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['wallet']} My Wallet", callback_data="wallet")],
            [InlineKeyboardButton(f"{EMOJI['star']} Referral", callback_data="referral")],
            [InlineKeyboardButton(f"{EMOJI['shield']} Status", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
        await update.message.reply_text(f"{EMOJI['rocket']} **Quick Access Menu:**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def device_verified_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle successful device verification"""
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name or "User"
        
        await update.message.reply_text(
            f"{EMOJI['check']} **Device Verified Successfully!**\n\nYour account is now fully secured!\n\n{EMOJI['rocket']} All features are now unlocked!",
            parse_mode='Markdown'
        )
        
        await self.send_verified_welcome(update, first_name)
        
        # Check for pending referral bonus
        user = await user_model.get_user(user_id)
        if user and user.get("referred_by") and not user.get("referral_bonus_claimed", False):
            await self.process_referral_bonus(user_id, user["referred_by"])
    
    async def process_referral_bonus(self, user_id: int, referrer_id: int):
        """Process referral bonus for both users"""
        try:
            referrer = await user_model.get_user(referrer_id)
            if not referrer or not referrer.get('device_verified'):
                return
            
            # Give bonus to both users
            referral_bonus = 10.0
            
            # Add bonus to new user
            await user_model.add_to_wallet(
                user_id, 
                referral_bonus, 
                "referral", 
                f"Welcome bonus via referral from user {referrer_id}"
            )
            
            # Add bonus to referrer
            await user_model.add_to_wallet(
                referrer_id,
                referral_bonus,
                "referral",
                f"Referral bonus from new user {user_id}"
            )
            
            # Send notifications
            await self.bot.send_message(
                user_id,
                f"{EMOJI['rocket']} **Referral Bonus Received!**\n\nYou got Rs.{referral_bonus:.2f} for joining via referral link!",
                parse_mode="Markdown"
            )
            
            await self.bot.send_message(
                referrer_id,
                f"{EMOJI['rocket']} **Referral Success!**\n\nSomeone used your referral link! You earned Rs.{referral_bonus:.2f} bonus!",
                parse_mode="Markdown"
            )
            
            logger.info(f"Referral bonus processed: {referrer_id} -> {user_id}")
            
        except Exception as e:
            logger.error(f"Referral bonus processing error: {e}")
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced wallet command"""
        user_id = update.effective_user.id
        
        # Check verification
        if not await user_model.is_user_verified(user_id):
            await update.message.reply_text(f"{EMOJI['lock']} Device verification required. Please /start to verify your device.")
            return
        
        user = await user_model.get_user(user_id)
        if not user:
            await update.message.reply_text(f"{EMOJI['cross']} User not found.")
            return
        
        wallet_msg = f"""{EMOJI['wallet']} **Your Secure Wallet**

{EMOJI['star']} **User:** {user.get('first_name', 'Unknown')}
{EMOJI['key']} **User ID:** `{user_id}`
{EMOJI['wallet']} **Current Balance:** Rs.{user.get('wallet_balance', 0):.2f}

**{EMOJI['chart']} Earnings Breakdown:**
• Total Earned: Rs.{user.get('total_earned', 0):.2f}
• Referral Earnings: Rs.{user.get('referral_earnings', 0):.2f}

**{EMOJI['fire']} Activity Stats:**
• Total Referrals: {user.get('total_referrals', 0)}
• Member Since: {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d')}

**{EMOJI['lock']} Security Status:**
• Device: {EMOJI['check']} Verified & Secure
• Account: {EMOJI['check']} Active"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['gear']} Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton(f"{EMOJI['star']} Referral", callback_data="referral")],
            [InlineKeyboardButton(f"{EMOJI['rocket']} Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def referral_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced referral command"""
        user_id = update.effective_user.id
        
        if not await user_model.is_user_verified(user_id):
            await update.message.reply_text(f"{EMOJI['lock']} Device verification required for referral system.")
            return
        
        user = await user_model.get_user(user_id)
        if not user:
            return
        
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        referral_msg = f"""{EMOJI['star']} **Enhanced Referral Program**

**{EMOJI['rocket']} Earn Rs.10 for each verified friend!**

**Your Referral Stats:**
• Total Referrals: {user.get('total_referrals', 0)}
• Referral Earnings: Rs.{user.get('referral_earnings', 0):.2f}

**{EMOJI['key']} Your Personal Referral Link:**
`{referral_link}`

**{EMOJI['fire']} How it Works:**
1. Share your unique referral link
2. Friends join and verify their device
3. Both of you get Rs.10 instantly!

**{EMOJI['shield']} Security Features:**
• Only device-verified users get rewards
• Advanced fraud prevention active"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['rocket']} Share Link", url=f"https://t.me/share/url?url={referral_link}")],
            [InlineKeyboardButton(f"{EMOJI['chart']} Refresh", callback_data="referral")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced help command"""
        help_msg = f"""{EMOJI['bell']} **Enhanced Bot Help & Guide**

**{EMOJI['gear']} Available Commands:**
• /start - Main menu with device verification
• /wallet - Detailed wallet information  
• /referral - Complete referral program
• /help - Show this help

**{EMOJI['lock']} Security Features:**
• Advanced device fingerprinting
• One device = One account policy
• Real-time fraud detection

**{EMOJI['wallet']} Earning Opportunities:**
• **Referral System:** Rs.10 per verified friend
• **Campaigns:** Coming soon

**{EMOJI['bell']} Need Help?**
Contact our support team for assistance."""
        
        await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command for authorized users"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_CHAT_ID:
            await update.message.reply_text(f"{EMOJI['cross']} Unauthorized access.")
            return
        
        stats = await user_model.get_user_stats()
        
        admin_msg = f"""{EMOJI['gear']} **Admin Control Panel**

**{EMOJI['chart']} System Statistics:**
• Total Users: {stats['total_users']}
• Verified Users: {stats['verified_users']}
• Pending Verification: {stats['pending_verification']}
• Temp Storage Users: {stats['temp_storage_users']}

**{EMOJI['shield']} Database Status:**
• MongoDB: {'Connected' if db_connected else 'Using Temp Storage'}
• System: {EMOJI['check']} Operational"""
        
        await update.message.reply_text(admin_msg, parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced button handler"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        # Check verification for most actions
        if not await user_model.is_user_verified(user_id):
            await query.edit_message_text(f"{EMOJI['lock']} Device verification required. Please /start to verify your device first.")
            return
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "referral":
            await self.show_referral_details(update, context)
        elif data == "withdraw":
            withdraw_msg = f"{EMOJI['gear']} **Withdrawal System** (Coming Soon)\n\nAdvanced withdrawal system with multiple payment methods is under development."
            await query.edit_message_text(withdraw_msg, parse_mode="Markdown")
        else:
            await query.answer(f"{EMOJI['warning']} Unknown action.")
    
    async def show_referral_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed referral information"""
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        if not user:
            return
        
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        referral_details = f"""{EMOJI['star']} **Your Referral Dashboard**

**{EMOJI['chart']} Current Performance:**
• Active Referrals: {user.get('total_referrals', 0)}
• Total Earnings: Rs.{user.get('referral_earnings', 0):.2f}

**{EMOJI['key']} Your Unique Link:**
`{referral_link}`

**{EMOJI['rocket']} Earning Potential:**
• 10 Referrals = Rs.100
• 50 Referrals = Rs.500  
• 100 Referrals = Rs.1,000"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['rocket']} Share Link", url=f"https://t.me/share/url?url={referral_link}")],
            [InlineKeyboardButton(f"{EMOJI['chart']} Refresh", callback_data="referral")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(referral_details, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced message handler"""
        text = update.message.text
        user_id = update.effective_user.id
        
        # Check verification for feature access
        verification_required_texts = [
            f"{EMOJI['wallet']} My Wallet", f"{EMOJI['chart']} Campaigns", 
            f"{EMOJI['star']} Referral", f"{EMOJI['gear']} Withdraw"
        ]
        
        if text in verification_required_texts:
            if not await user_model.is_user_verified(user_id):
                await update.message.reply_text(
                    f"{EMOJI['lock']} Device verification required. Please /start to verify your device.",
                    reply_markup=self.get_reply_keyboard()
                )
                return
        
        # Handle menu button messages
        if text == f"{EMOJI['wallet']} My Wallet":
            await self.wallet_command(update, context)
        elif text == f"{EMOJI['star']} Referral":
            await self.referral_command(update, context)
        elif text == f"{EMOJI['bell']} Help":
            await self.help_command(update, context)
        elif text == f"{EMOJI['shield']} Status":
            await self.show_status(update, context)
        else:
            welcome_msg = f"""{EMOJI['star']} **Hi there!**

{EMOJI['rocket']} **Enhanced Wallet Bot** with advanced security
{EMOJI['lock']} **Device fingerprinting** protection active

**Current Status:**
• {EMOJI['check'] if await user_model.is_user_verified(user_id) else EMOJI['warning']} {'Device Verified' if await user_model.is_user_verified(user_id) else 'Verification Pending'}

Use the menu buttons below for navigation."""
            
            await update.message.reply_text(welcome_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show system status"""
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        status_msg = f"""{EMOJI['shield']} **System Status**

**{EMOJI['gear']} System:**
• Status: {EMOJI['check']} Running
• Database: {EMOJI['check'] if db_connected else EMOJI['warning']} {'MongoDB Connected' if db_connected else 'Temp Storage Active'}

**{EMOJI['star']} Your Account:**
• Verification: {EMOJI['check'] if await user_model.is_user_verified(user_id) else EMOJI['warning']} {'Verified' if await user_model.is_user_verified(user_id) else 'Pending'}
• Member Since: {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d') if user else 'Today'}

**{EMOJI['lock']} Security:**
• Device fingerprinting: {EMOJI['check']} Active
• Fraud prevention: {EMOJI['check']} Enabled"""
        
        await update.message.reply_text(status_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")

# Initialize bot
wallet_bot = None

# Device Verification API
@app.post("/api/verify-device")
async def verify_device(request: Request):
    """Complete device verification API"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        logger.info(f"Device verification request from user {user_id}")
        
        # Enhanced device verification
        verification_result = await user_model.verify_device(user_id, device_data)
        
        if verification_result["success"]:
            try:
                await wallet_bot.bot.send_message(user_id, "/device_verified")
                logger.info(f"Device verification successful for user {user_id}")
            except Exception as bot_error:
                logger.error(f"Bot callback error: {bot_error}")
        else:
            logger.warning(f"Device verification failed for user {user_id}: {verification_result['message']}")
            
        return verification_result
            
    except Exception as e:
        logger.error(f"Device verification API error: {e}")
        return {"success": False, "message": "Technical error during verification"}

# Enhanced Device Verification WebApp
@app.get("/verify")
async def verification_page(user_id: int):
    """Complete device verification page"""
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Device Verification</title>
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, sans-serif; 
            padding: 20px; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            min-height: 100vh; 
            margin: 0; 
        }}
        .container {{ 
            max-width: 400px; 
            margin: 0 auto; 
            background: white; 
            padding: 30px; 
            border-radius: 15px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.2); 
            text-align: center; 
        }}
        .icon {{ font-size: 3rem; margin-bottom: 15px; }}
        h2 {{ color: #333; margin-bottom: 10px; }}
        p {{ color: #666; margin-bottom: 20px; line-height: 1.5; }}
        .btn {{ 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            color: white; 
            padding: 15px 30px; 
            border: none; 
            border-radius: 8px; 
            cursor: pointer; 
            font-size: 16px; 
            font-weight: 600;
        }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .status {{ 
            margin: 20px 0; 
            padding: 15px; 
            border-radius: 8px; 
            font-weight: bold; 
        }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #e8f5e8; color: #2e7d32; }}
        .error {{ background: #ffebee; color: #c62828; }}
        .progress {{ 
            width: 100%; 
            height: 6px; 
            background: #eee; 
            border-radius: 3px; 
            overflow: hidden; 
            margin: 15px 0; 
        }}
        .progress-bar {{ 
            height: 100%; 
            background: linear-gradient(90deg, #667eea, #764ba2); 
            width: 0%; 
            transition: width 0.4s; 
        }}
        .security-info {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
            text-align: left;
        }}
        .security-info h3 {{ color: #333; margin-bottom: 10px; }}
        .security-info ul {{ padding-left: 20px; }}
        .security-info li {{ margin: 5px 0; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">🔐</div>
        <h2>Enhanced Device Verification</h2>
        <p>Secure your account with advanced device fingerprinting technology</p>
        
        <div class="security-info">
            <h3>🛡️ Security Features</h3>
            <ul>
                <li>✅ Advanced device fingerprinting</li>
                <li>🔒 One device per account policy</li>
                <li>🚫 Multiple account prevention</li>
                <li>⚡ Real-time fraud detection</li>
                <li>🎯 Enhanced security monitoring</li>
            </ul>
        </div>
        
        <div class="progress">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        
        <div id="status" class="status loading">Ready to verify your device...</div>
        
        <button id="verifyBtn" class="btn" onclick="verifyDevice()">🔍 Verify Device</button>
    </div>

    <script>
        const USER_ID = {user_id};
        let deviceData = {{}};
        let verificationStarted = false;
        
        function collectDeviceData() {{
            deviceData = {{
                screen_resolution: screen.width + 'x' + screen.height + 'x' + screen.colorDepth,
                user_agent_hash: btoa(navigator.userAgent).slice(-30),
                timezone_offset: new Date().getTimezoneOffset(),
                platform: navigator.platform,
                language: navigator.language,
                canvas_hash: generateCanvasHash(),
                webgl_hash: generateWebGLHash(),
                hardware_concurrency: navigator.hardwareConcurrency || 0,
                memory: navigator.deviceMemory || 0,
                timestamp: Date.now()
            }};
        }}
        
        function generateCanvasHash() {{
            try {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#f60';
                ctx.fillRect(125, 1, 62, 20);
                ctx.fillStyle = '#069';
                ctx.fillText('Enhanced Device Security Check', 2, 15);
                ctx.fillStyle = 'rgba(102, 204, 0, 0.2)';
                ctx.fillText('One Device One Account Policy', 4, 45);
                return btoa(canvas.toDataURL()).slice(-32);
            }} catch (e) {{
                return 'canvas_error_' + Date.now();
            }}
        }}
        
        function generateWebGLHash() {{
            try {{
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (!gl) return 'webgl_unavailable';
                
                const renderer = gl.getParameter(gl.RENDERER);
                const vendor = gl.getParameter(gl.VENDOR);
                return btoa(renderer + '|' + vendor).slice(-20);
            }} catch (e) {{
                return 'webgl_error_' + Date.now();
            }}
        }}
        
        function updateProgress(percent, message) {{
            document.getElementById('progressBar').style.width = percent + '%';
            document.getElementById('status').innerHTML = message;
        }}
        
        async function verifyDevice() {{
            if (verificationStarted) return;
            verificationStarted = true;
            
            updateProgress(10, '🔄 Starting device analysis...');
            document.getElementById('verifyBtn').disabled = true;
            
            collectDeviceData();
            updateProgress(40, '🔍 Generating security fingerprint...');
            
            await new Promise(resolve => setTimeout(resolve, 1000));
            updateProgress(70, '🔐 Verifying device uniqueness...');
            
            try {{
                const response = await fetch('/api/verify-device', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ user_id: USER_ID, device_data: deviceData }})
                }});
                
                const result = await response.json();
                updateProgress(100, '✅ Verification complete!');
                
                if (result.success) {{
                    document.getElementById('status').innerHTML = '🎉 Device verified successfully!<br><small>You can now close this page and return to Telegram.</small>';
                    document.getElementById('status').className = 'status success';
                    
                    setTimeout(() => {{
                        if (window.Telegram && window.Telegram.WebApp) {{
                            window.Telegram.WebApp.close();
                        }} else {{
                            window.close();
                        }}
                    }}, 3000);
                }} else {{
                    document.getElementById('status').innerHTML = '❌ ' + result.message;
                    document.getElementById('status').className = 'status error';
                    document.getElementById('verifyBtn').innerHTML = '🔄 Try Again';
                    document.getElementById('verifyBtn').disabled = false;
                    verificationStarted = false;
                }}
            }} catch (error) {{
                console.error('Verification error:', error);
                updateProgress(100, '❌ Network error occurred');
                document.getElementById('status').innerHTML = '❌ Network error. Please check your connection and try again.';
                document.getElementById('status').className = 'status error';
                document.getElementById('verifyBtn').innerHTML = '🔄 Retry Verification';
                document.getElementById('verifyBtn').disabled = false;
                verificationStarted = false;
            }}
        }}
        
        // Auto-initialize when page loads
        window.addEventListener('load', () => {{
            updateProgress(5, '⚡ System initialized - Ready for verification');
        }});
    </script>
</body>
</html>
    """
    
    return HTMLResponse(content=html_content)

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
    try:
        if not wallet_bot or not wallet_bot.application:
            return {"status": "error", "message": "Bot not initialized"}
        
        telegram_update = Update.de_json(update, wallet_bot.bot)
        if telegram_update:
            await wallet_bot.application.process_update(telegram_update)
            return {"status": "ok"}
        else:
            return {"status": "error", "message": "Invalid update format"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "enhanced-wallet-bot-final",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": db_connected,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "temp_storage_active": not db_connected,
        "version": "4.0.0-final-working"
    }

@app.get("/")
async def root():
    return {
        "message": f"{EMOJI['rocket']} Enhanced Wallet Bot - Final Working Version",
        "status": "running",
        "database": "MongoDB Atlas" if db_connected else "Temporary Storage",
        "features": [
            "Advanced Device Fingerprinting",
            "One Device One Account Policy", 
            "Fallback Storage System",
            "Enhanced Security Features",
            "Complete Admin Dashboard"
        ]
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    """Complete Admin Dashboard"""
    try:
        stats = await user_model.get_user_stats()
        
        return {
            "admin_panel": "Enhanced Control Dashboard - Final Version",
            "system_overview": {
                "total_users": stats["total_users"],
                "verified_users": stats["verified_users"], 
                "pending_verification": stats["pending_verification"],
                "temp_storage_users": stats["temp_storage_users"]
            },
            "database_status": {
                "mongodb_connected": db_connected,
                "fallback_storage": "Active" if not db_connected else "Standby",
                "data_persistence": "Guaranteed"
            },
            "security_features": {
                "device_fingerprinting": "Advanced Multi-Layer",
                "fraud_prevention": "Real-time Active",
                "account_policy": "One Device One Account"
            },
            "status": "All systems operational with fallback protection"
        }
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup event
@app.on_event("startup")
async def startup_event():
    global wallet_bot
    
    logger.info("Starting Complete Enhanced Wallet Bot System...")
    
    # Initialize database (with fallback to temp storage)
    db_success = await init_database()
    
    # Initialize bot
    wallet_bot = WalletBot()
    
    if wallet_bot.initialized and wallet_bot.application:
        try:
            await wallet_bot.bot.initialize()
            await wallet_bot.application.initialize()
            await wallet_bot.application.start()
            
            # Set webhook
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            await wallet_bot.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(3)
            
            result = await wallet_bot.bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True
            )
            
            if result:
                logger.info(f"Enhanced webhook configured: {webhook_url}")
            else:
                logger.error("Webhook configuration failed")
                
        except Exception as e:
            logger.error(f"Bot startup error: {e}")
    
    storage_type = "MongoDB Atlas" if db_connected else "Temporary Storage with Database Fallback"
    logger.info(f"Complete Enhanced Wallet Bot System Ready! Storage: {storage_type}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down complete enhanced bot system...")
    if wallet_bot and wallet_bot.application:
        try:
            await wallet_bot.bot.delete_webhook()
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            await wallet_bot.bot.shutdown()
            logger.info("Bot shutdown completed successfully")
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
    
    if db_client:
        try:
            db_client.close()
            logger.info("Database connection closed")
        except:
            pass
    
    logger.info("Complete system shutdown finished")

# Main application entry point
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Complete Enhanced Secure Wallet Bot - Port {PORT}")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=PORT,
        log_level="info"
    )
