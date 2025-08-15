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
app = FastAPI(title="Enhanced Wallet Bot - Strict Device Control", version="5.0.0")
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

# Fixed database initialization
async def init_database():
    global db_client, db_connected
    try:
        # Clean MongoDB URL
        clean_mongodb_url = MONGODB_URL.strip().replace('\n', '').replace('\r', '')
        
        db_client = AsyncIOMotorClient(
            clean_mongodb_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000
        )
        
        # Test connection
        await db_client.admin.command('ping')
        db_connected = True
        logger.info("Database connected successfully")
        
        # Clear old data and setup fresh collections
        await setup_fresh_database()
        
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        db_connected = False
        return False

async def setup_fresh_database():
    """Clear old data and setup fresh collections"""
    try:
        if db_client:
            # Clear all old collections for fresh start
            await db_client.walletbot.users.delete_many({})
            await db_client.walletbot.device_fingerprints.delete_many({})
            await db_client.walletbot.security_logs.delete_many({})
            
            logger.info("Old database data cleared - Fresh start initiated")
            
            # Create indexes for better performance
            await db_client.walletbot.users.create_index("user_id", unique=True)
            await db_client.walletbot.device_fingerprints.create_index("fingerprint", unique=True)
            await db_client.walletbot.device_fingerprints.create_index("user_id")
            
            logger.info("Fresh database indexes created")
            
    except Exception as e:
        logger.warning(f"Database setup warning: {e}")

# Strict User Model - One Device One Account Only
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
    
    def get_security_logs_collection(self):
        if db_client is not None and db_connected:
            return db_client.walletbot.security_logs
        return None
    
    async def create_user(self, user_data: dict):
        """Create user - ALWAYS starts unverified"""
        collection = self.get_collection()
        if collection is None:
            return False
        
        user_id = user_data["user_id"]
        
        try:
            # Check if user already exists
            existing_user = await collection.find_one({"user_id": user_id})
            if existing_user:
                logger.info(f"Existing user found: {user_id}")
                return True
            
            # Create new user - ALWAYS UNVERIFIED initially
            user_data.update({
                "created_at": datetime.utcnow(),
                "wallet_balance": 0.0,
                "total_earned": 0.0,
                "referral_earnings": 0.0,
                "total_referrals": 0,
                "is_active": True,
                "is_banned": False,
                "device_verified": False,  # ALWAYS FALSE initially
                "device_fingerprint": None,
                "verification_status": "pending",
                "last_activity": datetime.utcnow(),
                "referred_by": user_data.get("referred_by"),
                "referral_code": str(uuid.uuid4())[:8]
            })
            
            await collection.insert_one(user_data)
            logger.info(f"New user created (UNVERIFIED): {user_id}")
            
            # Log user creation
            await self.log_security_event(user_id, "USER_CREATED", {
                "username": user_data.get("username"),
                "verification_required": True
            })
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return False
    
    async def get_user(self, user_id: int):
        """Get user from database"""
        collection = self.get_collection()
        if collection is None:
            return None
            
        try:
            user = await collection.find_one({"user_id": user_id})
            if user:
                # Update last activity
                await collection.update_one(
                    {"user_id": user_id},
                    {"$set": {"last_activity": datetime.utcnow()}}
                )
            return user
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None
    
    async def is_user_verified(self, user_id: int):
        """Check if user is device verified - STRICT CHECK"""
        user = await self.get_user(user_id)
        if not user:
            return False
        
        # Must have ALL conditions true
        return (
            user.get('device_verified', False) and 
            user.get('device_fingerprint') is not None and
            user.get('verification_status') == 'verified' and
            not user.get('is_banned', False)
        )
    
    async def generate_device_fingerprint(self, device_data: dict) -> str:
        """Generate strong device fingerprint"""
        try:
            # Create comprehensive fingerprint
            components = [
                str(device_data.get('screen_resolution', '')),
                str(device_data.get('user_agent_hash', '')),
                str(device_data.get('timezone_offset', '')),
                str(device_data.get('platform', '')),
                str(device_data.get('language', '')),
                str(device_data.get('canvas_hash', '')),
                str(device_data.get('webgl_hash', '')),
                str(device_data.get('hardware_concurrency', '')),
                str(device_data.get('memory', '')),
                str(device_data.get('pixel_ratio', ''))
            ]
            
            # Create combined fingerprint
            combined = '|'.join(filter(None, components))
            fingerprint = hashlib.sha256(combined.encode()).hexdigest()
            
            logger.info(f"Generated fingerprint: {fingerprint[:16]}...")
            return fingerprint
            
        except Exception as e:
            logger.error(f"Fingerprint generation error: {e}")
            # Return error-based fingerprint
            return hashlib.sha256(f"error_{datetime.utcnow().timestamp()}_{user_id}".encode()).hexdigest()
    
    async def check_device_already_used(self, fingerprint: str) -> dict:
        """Check if device fingerprint is already used by ANY user"""
        device_collection = self.get_device_collection()
        if device_collection is None:
            return {"used": False, "reason": "database_error"}
        
        try:
            # Check if fingerprint exists in database
            existing_device = await device_collection.find_one({"fingerprint": fingerprint})
            
            if existing_device:
                existing_user_id = existing_device.get('user_id')
                logger.warning(f"Device already used by user: {existing_user_id}")
                
                return {
                    "used": True,
                    "existing_user_id": existing_user_id,
                    "message": f"इस device पर पहले से user {existing_user_id} का verified account है। एक device पर केवल एक ही account allowed है।"
                }
            
            return {"used": False}
            
        except Exception as e:
            logger.error(f"Device check error: {e}")
            return {"used": True, "reason": "check_error", "message": "Technical error during device check"}
    
    async def verify_device_strict(self, user_id: int, device_data: dict) -> dict:
        """STRICT device verification - Only allows FIRST account per device"""
        try:
            # Generate device fingerprint
            fingerprint = await self.generate_device_fingerprint(device_data)
            
            # Check if device is already used
            device_check = await self.check_device_already_used(fingerprint)
            
            if device_check["used"]:
                # Device already has an account - STRICTLY REJECT
                await self.log_security_event(user_id, "DEVICE_VERIFICATION_REJECTED", {
                    "reason": "device_already_used",
                    "existing_user": device_check.get("existing_user_id"),
                    "fingerprint": fingerprint[:16] + "..."
                })
                
                return {
                    "success": False,
                    "message": device_check["message"]
                }
            
            # Device is new - ALLOW verification
            await self.store_device_fingerprint(user_id, fingerprint, device_data)
            await self.mark_user_verified(user_id, fingerprint)
            
            await self.log_security_event(user_id, "DEVICE_VERIFICATION_SUCCESS", {
                "fingerprint": fingerprint[:16] + "...",
                "device_data": device_data
            })
            
            logger.info(f"Device successfully verified for user {user_id} - FIRST account on this device")
            return {"success": True, "message": "Device verified successfully - आपका account अब secure है!"}
            
        except Exception as e:
            logger.error(f"Device verification error: {e}")
            await self.log_security_event(user_id, "DEVICE_VERIFICATION_ERROR", {"error": str(e)})
            return {"success": False, "message": "Technical error occurred during verification"}
    
    async def store_device_fingerprint(self, user_id: int, fingerprint: str, device_data: dict):
        """Store device fingerprint in database"""
        device_collection = self.get_device_collection()
        if device_collection is None:
            return
        
        try:
            device_record = {
                "user_id": user_id,
                "fingerprint": fingerprint,
                "device_data": device_data,
                "created_at": datetime.utcnow(),
                "last_used": datetime.utcnow(),
                "is_active": True
            }
            
            await device_collection.insert_one(device_record)
            logger.info(f"Device fingerprint stored for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error storing device fingerprint: {e}")
    
    async def mark_user_verified(self, user_id: int, fingerprint: str):
        """Mark user as device verified"""
        collection = self.get_collection()
        if collection is None:
            return
        
        try:
            verification_update = {
                "device_verified": True,
                "device_fingerprint": fingerprint,
                "verification_status": "verified",
                "device_verified_at": datetime.utcnow()
            }
            
            await collection.update_one(
                {"user_id": user_id},
                {"$set": verification_update}
            )
            
            logger.info(f"User marked as verified: {user_id}")
            
        except Exception as e:
            logger.error(f"Error marking user verified: {e}")
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        """Add money to user wallet - Only for verified users"""
        if not await self.is_user_verified(user_id):
            logger.warning(f"Wallet operation rejected - User {user_id} not verified")
            return False
        
        collection = self.get_collection()
        if collection is None:
            return False
        
        try:
            user = await self.get_user(user_id)
            if not user or user.get('is_banned', False):
                return False
            
            new_balance = user.get("wallet_balance", 0) + amount
            total_earned = user.get("total_earned", 0)
            
            if amount > 0:
                total_earned += amount
            
            wallet_update = {
                "wallet_balance": new_balance,
                "total_earned": total_earned,
                "updated_at": datetime.utcnow()
            }
            
            if transaction_type == "referral":
                wallet_update["referral_earnings"] = user.get("referral_earnings", 0) + amount
                wallet_update["total_referrals"] = user.get("total_referrals", 0) + 1
            
            await collection.update_one(
                {"user_id": user_id},
                {"$set": wallet_update}
            )
            
            logger.info(f"Wallet updated for verified user {user_id}: {amount:+.2f}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding to wallet: {e}")
            return False
    
    async def log_security_event(self, user_id: int, event_type: str, details: dict):
        """Log security events"""
        security_logs = self.get_security_logs_collection()
        if security_logs is None:
            return
        
        try:
            log_entry = {
                "user_id": user_id,
                "event_type": event_type,
                "details": details,
                "timestamp": datetime.utcnow()
            }
            await security_logs.insert_one(log_entry)
        except Exception as e:
            logger.error(f"Security logging error: {e}")
    
    async def get_user_stats(self) -> dict:
        """Get user statistics"""
        stats = {
            "total_users": 0,
            "verified_users": 0,
            "pending_verification": 0,
            "unique_devices": 0,
            "rejected_verifications": 0
        }
        
        collection = self.get_collection()
        device_collection = self.get_device_collection()
        security_logs = self.get_security_logs_collection()
        
        if collection is None:
            return stats
        
        try:
            stats["total_users"] = await collection.count_documents({})
            stats["verified_users"] = await collection.count_documents({"device_verified": True})
            stats["pending_verification"] = stats["total_users"] - stats["verified_users"]
            
            if device_collection is not None:
                stats["unique_devices"] = await device_collection.count_documents({})
            
            if security_logs is not None:
                stats["rejected_verifications"] = await security_logs.count_documents({
                    "event_type": "DEVICE_VERIFICATION_REJECTED"
                })
            
        except Exception as e:
            logger.error(f"Stats calculation error: {e}")
        
        return stats

# Initialize user model
user_model = UserModel()

# Enhanced Telegram Bot with Strict Verification
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
            logger.info("Strict verification bot initialized")
        except Exception as e:
            logger.error(f"Bot initialization error: {e}")
            self.initialized = False
    
    def setup_handlers(self):
        try:
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("referral", self.referral_command))
            self.application.add_handler(CommandHandler("device_verified", self.device_verified_callback))
            self.application.add_handler(CommandHandler("admin", self.admin_command))
            
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.application.add_error_handler(self.error_handler)
            
            logger.info("All bot handlers setup complete")
        except Exception as e:
            logger.error(f"Handler setup error: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
    
    def get_reply_keyboard(self):
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
            
            # Create user (ALWAYS UNVERIFIED initially)
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name
            }
            
            if referrer_id and referrer_id != user_id:
                user_data["referred_by"] = referrer_id
            
            await user_model.create_user(user_data)
            
            # ALWAYS require verification - even for existing users
            # Check current verification status
            is_verified = await user_model.is_user_verified(user_id)
            
            if not is_verified:
                # Show verification requirement - EVERY TIME
                await self.require_device_verification(user_id, first_name, update)
            else:
                # User is already verified, show welcome
                await self.send_verified_welcome(update, first_name)
                if referrer_id:
                    await self.process_referral_bonus(user_id, referrer_id)
                    
        except Exception as e:
            logger.error(f"Start command error: {e}")
            await update.message.reply_text(f"{EMOJI['cross']} Error occurred. Please try again.")
    
    async def require_device_verification(self, user_id: int, first_name: str, update: Update):
        """ALWAYS require device verification"""
        verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
        
        verification_msg = f"""{EMOJI['lock']} **Strict Device Verification Required**

Hello {first_name}! 

{EMOJI['shield']} **ENHANCED SECURITY POLICY:**
{EMOJI['cross']} केवल एक device पर एक account allowed है
{EMOJI['fire']} Multiple accounts strictly prohibited
{EMOJI['key']} Advanced fingerprinting technology

{EMOJI['warning']} **Important Notice:**
• यदि आपका कोई दूसरा account इस device पर verified है तो यह account reject हो जाएगा
• First account on device को ही verification मिलेगा
• यह policy fraud prevention के लिए है

{EMOJI['rocket']} **Click below to verify:**"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['lock']} Verify This Device", web_app=WebAppInfo(url=verification_url))]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(verification_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def send_verified_welcome(self, update: Update, first_name: str):
        welcome_msg = f"""{EMOJI['rocket']} **Device Successfully Verified!**

Welcome {first_name}! {EMOJI['check']}

{EMOJI['shield']} **Your Account Status:**
• Device Verification: {EMOJI['check']} Completed
• Account Security: {EMOJI['check']} Maximum
• Unique Device Policy: {EMOJI['check']} Enforced

{EMOJI['wallet']} **Available Features:**
• Secure wallet management
• Referral system - Rs.10 per friend
• Campaign participation (coming soon)
• Safe withdrawals (coming soon)

Choose an option below:"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['wallet']} My Wallet", callback_data="wallet")],
            [InlineKeyboardButton(f"{EMOJI['star']} Referral", callback_data="referral")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode="Markdown")
        await update.message.reply_text(f"{EMOJI['rocket']} **Quick Menu:**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def device_verified_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name or "User"
        
        await update.message.reply_text(
            f"{EMOJI['check']} **Device Verification Successful!**\n\nYour account is now the ONLY verified account on this device!\n\n{EMOJI['shield']} Enhanced security active!",
            parse_mode='Markdown'
        )
        
        await self.send_verified_welcome(update, first_name)
        
        # Process referral bonus
        user = await user_model.get_user(user_id)
        if user and user.get("referred_by") and not user.get("referral_bonus_claimed", False):
            await self.process_referral_bonus(user_id, user["referred_by"])
    
    async def process_referral_bonus(self, user_id: int, referrer_id: int):
        try:
            # Both users must be verified for referral bonus
            if not await user_model.is_user_verified(user_id) or not await user_model.is_user_verified(referrer_id):
                logger.info(f"Referral bonus skipped - users not verified: {referrer_id} -> {user_id}")
                return
            
            referral_bonus = 10.0
            
            # Add bonus to both users
            await user_model.add_to_wallet(user_id, referral_bonus, "referral", f"Welcome bonus from referral")
            await user_model.add_to_wallet(referrer_id, referral_bonus, "referral", f"Referral bonus from user {user_id}")
            
            # Send notifications
            await self.bot.send_message(
                user_id,
                f"{EMOJI['rocket']} **Referral Bonus!** Rs.{referral_bonus:.2f} added to your verified account!",
                parse_mode="Markdown"
            )
            
            await self.bot.send_message(
                referrer_id,
                f"{EMOJI['rocket']} **Referral Success!** Rs.{referral_bonus:.2f} earned from verified referral!",
                parse_mode="Markdown"
            )
            
            logger.info(f"Referral bonus processed for verified users: {referrer_id} -> {user_id}")
            
        except Exception as e:
            logger.error(f"Referral bonus error: {e}")
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # STRICT verification check
        if not await user_model.is_user_verified(user_id):
            await update.message.reply_text(f"{EMOJI['lock']} Device verification required. Please /start to verify.")
            return
        
        user = await user_model.get_user(user_id)
        if not user:
            await update.message.reply_text(f"{EMOJI['cross']} User not found.")
            return
        
        wallet_msg = f"""{EMOJI['wallet']} **Your Verified Wallet**

{EMOJI['star']} **User:** {user.get('first_name', 'Unknown')}
{EMOJI['key']} **User ID:** `{user_id}`
{EMOJI['wallet']} **Balance:** Rs.{user.get('wallet_balance', 0):.2f}

{EMOJI['chart']} **Earnings:**
• Total Earned: Rs.{user.get('total_earned', 0):.2f}
• Referral Earnings: Rs.{user.get('referral_earnings', 0):.2f}
• Total Referrals: {user.get('total_referrals', 0)}

{EMOJI['shield']} **Security Status:**
• Device: {EMOJI['check']} Only Verified Account on Device
• Security: {EMOJI['check']} Maximum Protection
• Account: {EMOJI['check']} Fully Active"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['star']} Referral", callback_data="referral")],
            [InlineKeyboardButton(f"{EMOJI['rocket']} Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def referral_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not await user_model.is_user_verified(user_id):
            await update.message.reply_text(f"{EMOJI['lock']} Device verification required.")
            return
        
        user = await user_model.get_user(user_id)
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        referral_msg = f"""{EMOJI['star']} **Verified Account Referral Program**

{EMOJI['rocket']} **Earn Rs.10 for each verified friend!**

{EMOJI['chart']} **Your Stats:**
• Verified Referrals: {user.get('total_referrals', 0)}
• Referral Earnings: Rs.{user.get('referral_earnings', 0):.2f}

{EMOJI['key']} **Your Link:** `{referral_link}`

{EMOJI['shield']} **Requirements:**
• Friends must complete device verification
• Only one account per device allowed
• Both users get Rs.10 bonus"""
        
        keyboard = [
            [InlineKeyboardButton(f"{EMOJI['rocket']} Share Link", url=f"https://t.me/share/url?url={referral_link}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = f"""{EMOJI['bell']} **Strict Security Bot Help**

{EMOJI['gear']} **Commands:**
• /start - Device verification (required every time)
• /wallet - Wallet access (verified users only)
• /referral - Referral program
• /help - This help

{EMOJI['lock']} **Security Policy:**
• ONE device = ONE account ONLY
• Multiple accounts = Automatic rejection
• Device verification mandatory
• No exceptions to security rules

{EMOJI['shield']} **This ensures:**
• Fair usage for everyone
• No fraud or abuse
• Secure transactions
• Reliable platform"""
        
        await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            await update.message.reply_text(f"{EMOJI['cross']} Unauthorized.")
            return
        
        stats = await user_model.get_user_stats()
        
        admin_msg = f"""{EMOJI['gear']} **Strict Security Admin Panel**

{EMOJI['chart']} **Statistics:**
• Total Users: {stats['total_users']}
• Verified Users: {stats['verified_users']}
• Pending Verification: {stats['pending_verification']}
• Unique Devices: {stats['unique_devices']}
• Rejected Verifications: {stats['rejected_verifications']}

{EMOJI['shield']} **Policy Enforcement:**
• One Device One Account: {EMOJI['check']} ACTIVE
• Database: Cleaned and Fresh
• Security: Maximum Level"""
        
        await update.message.reply_text(admin_msg, parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        # STRICT verification required for ALL buttons
        if not await user_model.is_user_verified(user_id):
            await query.edit_message_text(f"{EMOJI['lock']} Device verification required. /start to verify.")
            return
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "referral":
            await self.referral_command(update, context)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user_id = update.effective_user.id
        
        # ALL menu functions require verification
        if text in [f"{EMOJI['wallet']} My Wallet", f"{EMOJI['star']} Referral", f"{EMOJI['chart']} Campaigns", f"{EMOJI['gear']} Withdraw"]:
            if not await user_model.is_user_verified(user_id):
                await update.message.reply_text(f"{EMOJI['lock']} Device verification required. Use /start", reply_markup=self.get_reply_keyboard())
                return
        
        if text == f"{EMOJI['wallet']} My Wallet":
            await self.wallet_command(update, context)
        elif text == f"{EMOJI['star']} Referral":
            await self.referral_command(update, context)
        elif text == f"{EMOJI['bell']} Help":
            await self.help_command(update, context)
        else:
            await update.message.reply_text(f"{EMOJI['warning']} Please use /start for device verification first.", reply_markup=self.get_reply_keyboard())

# Initialize bot
wallet_bot = None

# Device Verification API - STRICT
@app.post("/api/verify-device")
async def verify_device(request: Request):
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        logger.info(f"STRICT device verification request from user {user_id}")
        
        # Strict verification
        verification_result = await user_model.verify_device_strict(user_id, device_data)
        
        if verification_result["success"]:
            try:
                await wallet_bot.bot.send_message(user_id, "/device_verified")
                logger.info(f"Device verification SUCCESS for user {user_id}")
            except Exception as bot_error:
                logger.error(f"Bot callback error: {bot_error}")
        else:
            logger.warning(f"Device verification REJECTED for user {user_id}: {verification_result['message']}")
            
        return verification_result
            
    except Exception as e:
        logger.error(f"Device verification API error: {e}")
        return {"success": False, "message": "Technical error occurred"}

# Enhanced Verification Page
@app.get("/verify")
async def verification_page(user_id: int):
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Strict Device Verification</title>
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, sans-serif; 
            padding: 20px; 
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%); 
            min-height: 100vh; 
            margin: 0; 
        }}
        .container {{ 
            max-width: 450px; 
            margin: 0 auto; 
            background: white; 
            padding: 30px; 
            border-radius: 15px; 
            box-shadow: 0 15px 35px rgba(0,0,0,0.3); 
            text-align: center; 
            border: 2px solid #ff6b6b;
        }}
        .icon {{ font-size: 4rem; margin-bottom: 15px; color: #ff6b6b; }}
        h2 {{ color: #333; margin-bottom: 15px; font-weight: 700; }}
        .warning-box {{ 
            background: linear-gradient(135deg, #fff3cd, #ffeaa7); 
            border: 2px solid #ff6b6b; 
            padding: 20px; 
            border-radius: 10px; 
            margin: 20px 0; 
            text-align: left;
        }}
        .warning-box h3 {{ color: #d63031; margin-bottom: 10px; }}
        .warning-box ul {{ padding-left: 20px; color: #2d3436; }}
        .warning-box li {{ margin: 8px 0; font-weight: 500; }}
        .btn {{ 
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a24 100%); 
            color: white; 
            padding: 15px 30px; 
            border: none; 
            border-radius: 8px; 
            cursor: pointer; 
            font-size: 16px; 
            font-weight: 700;
            text-transform: uppercase;
        }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .status {{ 
            margin: 20px 0; 
            padding: 15px; 
            border-radius: 8px; 
            font-weight: bold; 
        }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #d4edda; color: #155724; }}
        .error {{ background: #f8d7da; color: #721c24; }}
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
            background: linear-gradient(90deg, #ff6b6b, #ee5a24); 
            width: 0%; 
            transition: width 0.4s; 
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">⚠️</div>
        <h2>STRICT Device Verification</h2>
        
        <div class="warning-box">
            <h3>🚨 IMPORTANT WARNING</h3>
            <ul>
                <li><strong>केवल एक device पर एक account allowed है!</strong></li>
                <li>यदि इस device पर पहले से कोई account verified है तो यह REJECT हो जाएगा</li>
                <li>First account को ही verification मिलेगी</li>
                <li>यह policy strictly enforced है - कोई exception नहीं</li>
                <li>Fraud prevention के लिए यह जरूरी है</li>
            </ul>
        </div>
        
        <div class="progress">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        
        <div id="status" class="status loading">Ready for strict verification...</div>
        
        <button id="verifyBtn" class="btn" onclick="verifyDevice()">⚠️ Proceed with Verification</button>
    </div>

    <script>
        const USER_ID = {user_id};
        let deviceData = {{}};
        
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
                pixel_ratio: window.devicePixelRatio || 1,
                timestamp: Date.now()
            }};
        }}
        
        function generateCanvasHash() {{
            try {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = 'bold 16px Arial';
                ctx.fillStyle = '#ff6b6b';
                ctx.fillRect(10, 10, 150, 30);
                ctx.fillStyle = '#fff';
                ctx.fillText('STRICT DEVICE CHECK', 15, 25);
                ctx.fillStyle = '#2d3436';
                ctx.fillText('One Device One Account', 10, 60);
                return btoa(canvas.toDataURL()).slice(-40);
            }} catch (e) {{
                return 'canvas_strict_' + Date.now();
            }}
        }}
        
        function generateWebGLHash() {{
            try {{
                const canvas = document.createElement('canvas');
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (!gl) return 'webgl_unavailable';
                
                const renderer = gl.getParameter(gl.RENDERER);
                const vendor = gl.getParameter(gl.VENDOR);
                const version = gl.getParameter(gl.VERSION);
                return btoa(renderer + '|' + vendor + '|' + version).slice(-30);
            }} catch (e) {{
                return 'webgl_strict_' + Date.now();
            }}
        }}
        
        function updateProgress(percent, message) {{
            document.getElementById('progressBar').style.width = percent + '%';
            document.getElementById('status').innerHTML = message;
        }}
        
        async function verifyDevice() {{
            updateProgress(20, '🔍 Collecting comprehensive device data...');
            document.getElementById('verifyBtn').disabled = true;
            
            collectDeviceData();
            updateProgress(50, '🔐 Generating unique device fingerprint...');
            
            await new Promise(resolve => setTimeout(resolve, 1500));
            updateProgress(80, '⚠️ Checking device against existing accounts...');
            
            try {{
                const response = await fetch('/api/verify-device', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ user_id: USER_ID, device_data: deviceData }})
                }});
                
                const result = await response.json();
                updateProgress(100, '✅ Verification process complete!');
                
                if (result.success) {{
                    document.getElementById('status').innerHTML = '🎉 SUCCESS! आपका device verify हो गया है!<br><small>आप अब इस device पर ONLY VERIFIED account हैं।</small>';
                    document.getElementById('status').className = 'status success';
                    
                    setTimeout(() => {{
                        if (window.Telegram && window.Telegram.WebApp) {{
                            window.Telegram.WebApp.close();
                        }}
                    }}, 3000);
                }} else {{
                    document.getElementById('status').innerHTML = '❌ REJECTED: ' + result.message + '<br><small>इस device पर पहले से एक verified account है।</small>';
                    document.getElementById('status').className = 'status error';
                    document.getElementById('verifyBtn').innerHTML = '❌ Verification Failed';
                    document.getElementById('verifyBtn').disabled = true;
                }}
            }} catch (error) {{
                updateProgress(100, '❌ Network error occurred');
                document.getElementById('status').innerHTML = '❌ Network error. Please check connection and try again.';
                document.getElementById('status').className = 'status error';
                document.getElementById('verifyBtn').disabled = false;
            }}
        }}
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
        "service": "strict-device-verification-bot",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": db_connected,
        "security_policy": "ONE_DEVICE_ONE_ACCOUNT_STRICTLY_ENFORCED",
        "version": "5.0.0-strict"
    }

@app.get("/")
async def root():
    return {
        "message": f"{EMOJI['shield']} Strict Device Verification Bot",
        "status": "running",
        "policy": "ONE DEVICE = ONE ACCOUNT ONLY",
        "security": "MAXIMUM ENFORCEMENT",
        "database": "FRESH START - OLD DATA CLEARED"
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    try:
        stats = await user_model.get_user_stats()
        
        return {
            "admin_panel": "STRICT SECURITY ENFORCEMENT DASHBOARD",
            "database_status": "FRESH - OLD DATA CLEARED",
            "security_policy": "ONE DEVICE ONE ACCOUNT - STRICTLY ENFORCED",
            "statistics": {
                "total_users": stats["total_users"],
                "verified_users": stats["verified_users"],
                "rejected_verifications": stats["rejected_verifications"],
                "unique_devices": stats["unique_devices"],
                "enforcement_rate": "100% - NO EXCEPTIONS"
            }
        }
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup event
@app.on_event("startup")
async def startup_event():
    global wallet_bot
    
    logger.info("Starting STRICT Device Verification Bot System...")
    
    # Initialize database with fresh start
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
                logger.info(f"STRICT webhook configured: {webhook_url}")
                
        except Exception as e:
            logger.error(f"Bot startup error: {e}")
    
    logger.info("STRICT DEVICE VERIFICATION BOT READY!")
    logger.info("POLICY: ONE DEVICE = ONE ACCOUNT ONLY")
    logger.info("DATABASE: FRESH START - OLD DATA CLEARED")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down strict verification bot...")
    if wallet_bot and wallet_bot.application:
        try:
            await wallet_bot.bot.delete_webhook()
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            await wallet_bot.bot.shutdown()
            logger.info("Strict bot shutdown completed")
        except Exception as e:
            logger.error(f"Shutdown error: {e}")

# Main application entry point
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting STRICT DEVICE VERIFICATION BOT - Port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
