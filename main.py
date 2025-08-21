# ============================================================
#  CHUNK 1 / 12 – IMPORTS, GLOBALS, APP SETUP
# ============================================================

# -------------------- Standard Library ----------------------
import os
import sys
import asyncio
import secrets
import hashlib
import base64
import uuid
import json
import io
import zipfile
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple

# -------------------- Third-Party ---------------------------
import aiofiles  # file I/O
from fastapi import (
    FastAPI, HTTPException, Depends, Request,
    File, UploadFile, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import (
    HTMLResponse, JSONResponse, FileResponse, StreamingResponse
)
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient

from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler as TGCallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import BadRequest

# Optional: psutil for detailed health (guarded usage later)
try:
    import psutil  # noqa: F401
except Exception:
    psutil = None

# -------------------- Logging -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wallet-bot")

# -------------------- Environment / Constants ---------------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "REPLACE_ME")
ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

MONGODB_URL: str = os.getenv(
    "MONGODB_URL",
    "mongodb://localhost:27017/walletbot"
)

RENDER_EXTERNAL_URL: str = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://example.com"
)

PORT: int = int(os.getenv("PORT", 8000))

# ------------- Emoji Map (safe Unicode characters) ----------
EMOJI: Dict[str, str] = {
    "check": "✅", "cross": "❌", "pending": "⏳", "warn": "⚠️",
    "lock": "🔒", "wallet": "💰", "gift": "🎁", "gear": "⚙️",
    "chart": "📊", "rocket": "🚀", "camera": "📷", "bank": "🏦",
    "bell": "🔔", "star": "⭐", "download": "⬇️", "upload": "⬆️",
    "shield": "🛡️"
}

# ---------------- Directory Structure -----------------------
os.makedirs("uploads/screenshots", exist_ok=True)
os.makedirs("uploads/campaign_images", exist_ok=True)
os.makedirs("uploads/admin_images", exist_ok=True)
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

# -------------------- FastAPI app ---------------------------
app = FastAPI(
    title="Enterprise Wallet Bot – Single-File Build",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # consider restricting in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
basic_auth = HTTPBasic()

# -------------------- Global Runtime Objects ---------------
db_client: Optional[AsyncIOMotorClient] = None
db_connected: bool = False

class WalletBotHolder:
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.application: Optional[Application] = None
        self.initialized: bool = False
        self.webhook_set: bool = False

wallet_bot = WalletBotHolder()

# Small safe debug summary (avoid printing secrets)
logger.info("--- STARTUP ENV SUMMARY ---")
logger.info(f"ADMIN_USERNAME set: {bool(ADMIN_USERNAME)}")
logger.info(f"ADMIN_PASSWORD set: {bool(ADMIN_PASSWORD)}")
logger.info(f"ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
logger.info(f"BOT_TOKEN configured: {BOT_TOKEN != 'REPLACE_ME'}")
logger.info(f"MONGODB_URL present: {bool(MONGODB_URL)}")
logger.info(f"RENDER_EXTERNAL_URL: {RENDER_EXTERNAL_URL}")
logger.info("---------------------------")







# ============================================================
#  CHUNK 2 / 12 – DB INIT, DEFAULT SETTINGS, SECURITY, SAFE SEND
# ============================================================

# -------------------- Database Connection -------------------
async def init_database() -> bool:
    """Initialize MongoDB connection with proper error handling"""
    global db_client, db_connected
    try:
        clean_url = MONGODB_URL.strip().replace('\n', '').replace('\r', '')
        db_client = AsyncIOMotorClient(
            clean_url,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000
        )
        await db_client.admin.command('ping')
        db_connected = True
        logger.info("✅ Database connected successfully")

        await setup_database_collections()
        await setup_default_bot_settings()
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        db_connected = False
        return False

async def setup_database_collections():
    """Create indexes and collections structure"""
    if not db_client:
        return
    try:
        db = db_client.walletbot
        await db.users.create_index("user_id", unique=True)
        await db.device_fingerprints.create_index("fingerprint", unique=True)
        await db.campaigns.create_index("campaign_id", unique=True)
        await db.gift_codes.create_index("code", unique=True)
        await db.withdrawal_requests.create_index("request_id", unique=True)
        await db.transactions.create_index("transaction_id", unique=True)
        await db.api_keys.create_index("api_key", unique=True)
        await db.force_join_channels.create_index("channel_id", unique=True)
        logger.info("✅ Database collections and indexes created")
    except Exception as e:
        logger.warning(f"⚠️ Database setup warning: {e}")

async def setup_default_bot_settings():
    """Setup default configuration for bot"""
    if not db_client:
        return
    try:
        settings_collection = db_client.walletbot.bot_settings
        existing = await settings_collection.find_one({"type": "main_config"})
        if not existing:
            default_config = {
                "type": "main_config",
                "screenshot_reward": 5.0,
                "min_withdrawal": 10.0,
                "referral_bonus": 10.0,
                "payment_mode": "manual",  # manual or automatic
                "force_join_channels": [],
                "payment_gateways": {
                    "razorpay": {"enabled": False, "api_key": "", "api_secret": ""},
                    "paytm": {"enabled": False, "api_key": "", "merchant_id": ""},
                    "upi": {"enabled": True, "api_key": ""}
                },
                "button_texts": {
                    "earning_apps": "🎯 Earning Apps",
                    "gift_codes": "🎁 Get Gift Codes",
                    "monthly_campaigns": "📅 Monthly Campaigns",
                    "withdraw": "💰 Withdraw",
                    "balance_check": "💳 Check Balance"
                },
                "button_responses": {
                    "earning_apps": {
                        "text": "🎯 **Earning Apps Section**\n\nHere you can find the best earning applications and opportunities!",
                        "image_url": "",
                        "requires_channel_join": False
                    },
                    "gift_codes": {
                        "text": "🎁 **Gift Codes Section**\n\nRedeem exclusive gift codes here!",
                        "image_url": "",
                        "requires_channel_join": True
                    },
                    "monthly_campaigns": {
                        "text": "📅 **Monthly Campaigns**\n\nCheck out this month's special campaigns!",
                        "image_url": "",
                        "requires_channel_join": True
                    },
                    "balance_check": {
                        "text": "💳 **Balance Check**\n\nYour current wallet balance and statistics.",
                        "image_url": "",
                        "requires_channel_join": False
                    }
                },
                "button_order": ["earning_apps", "gift_codes", "monthly_campaigns", "balance_check", "withdraw"],
                "created_at": datetime.utcnow()
            }
            await settings_collection.insert_one(default_config)
            logger.info("✅ Default bot settings created")
    except Exception as e:
        logger.error(f"❌ Default settings creation error: {e}")

# -------------------- Security & Auth Helpers ---------------
def create_simple_token(data: Dict[str, Any]) -> str:
    """Create simple base64 token (JWT-free implementation)"""
    import time
    payload = {
        "data": data,
        "exp": int(time.time()) + (24 * 60 * 60),
        "iat": int(time.time())
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()

def verify_simple_token(token: str) -> Dict[str, Any]:
    """Verify simple token and return data"""
    try:
        import time
        payload = json.loads(base64.b64decode(token.encode()).decode())
        if payload.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="Token expired")
        return payload.get("data", {})
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def authenticate_admin(credentials: HTTPBasicCredentials = Depends(basic_auth)) -> str:
    """Admin authentication for API endpoints"""
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"}
        )
    return credentials.username

# -------------------- Telegram Message Helpers -------------
async def safe_edit_message(query, text: str, reply_markup=None, parse_mode=None):
    """Safely edit Telegram message to avoid 'Message not modified' errors"""
    try:
        return await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug("Message not modified (ignored)")
            return None
        else:
            logger.error(f"BadRequest in safe_edit_message: {e}")
            raise e
    except Exception as e:
        logger.error(f"Unexpected error in safe_edit_message: {e}")
        return None

async def safe_send_message(bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode=None):
    """Safely send Telegram message with error handling"""
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Error sending message to {chat_id}: {e}")
        return None









# ============================================================
#  CHUNK 3 / 12 – USER MODEL, ATOMIC UPDATES, WALLET, TXNS
# ============================================================

class EnhancedUserModel:
    """User management with device security & wallet operations"""
    def __init__(self):
        self.collection_cache: Dict[str, Any] = {}

    def get_collection(self, name: str):
        """Get MongoDB collection with caching"""
        if not db_client or not db_connected:
            logger.warning(f"Database not connected - collection '{name}' unavailable")
            return None
        if name not in self.collection_cache:
            self.collection_cache[name] = getattr(db_client.walletbot, name)
        return self.collection_cache[name]

    # ---------- Atomic update helpers ----------
    async def update_fields(self, user_id: int, set_fields: Optional[Dict[str, Any]] = None,
                            inc_fields: Optional[Dict[str, Any]] = None) -> bool:
        """Atomic $set and $inc"""
        col = self.get_collection('users')
        if col is None:
            return False
        update_doc: Dict[str, Any] = {"$set": {"updated_at": datetime.utcnow()}}
        if set_fields:
            update_doc["$set"].update(set_fields)
        if inc_fields:
            update_doc["$inc"] = inc_fields
        try:
            res = await col.update_one({"user_id": user_id}, update_doc)
            return res.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Atomic update error for user {user_id}: {e}")
            return False

    # ==================== USER CREATION & MANAGEMENT ====================
    async def create_user(self, user_data: Dict[str, Any]) -> bool:
        """Create new user - starts unverified"""
        collection = self.get_collection('users')
        if collection is None:
            return False
        user_id = user_data["user_id"]
        try:
            existing_user = await collection.find_one({"user_id": user_id})
            if existing_user:
                logger.info(f"User {user_id} already exists")
                return True
            new_user = {
                "user_id": user_id,
                "username": user_data.get("username", "Unknown"),
                "first_name": user_data.get("first_name", "User"),
                "last_name": user_data.get("last_name", ""),
                "created_at": datetime.utcnow(),
                "last_activity": datetime.utcnow(),

                # DEVICE SECURITY
                "device_verified": False,
                "device_fingerprint": None,
                "verification_status": "pending",
                "device_verified_at": None,

                # WALLET SYSTEM
                "wallet_balance": 0.0,
                "total_earned": 0.0,
                "referral_earnings": 0.0,
                "gift_code_earnings": 0.0,
                "withdrawal_total": 0.0,
                "pending_withdrawals": 0.0,

                # REFERRAL
                "referred_by": user_data.get("referred_by"),
                "referral_code": str(uuid.uuid4())[:8].upper(),
                "total_referrals": 0,
                "active_referrals": 0,

                # ACCOUNT STATUS
                "is_active": True,
                "is_banned": False,
                "ban_reason": None,
                "warning_count": 0,

                # CAMPAIGN STATS
                "campaigns_completed": 0,
                "screenshots_submitted": 0,
                "screenshots_approved": 0,
                "screenshots_rejected": 0,

                # GIFT CODE STATS
                "gift_codes_redeemed": 0,

                # PREFERENCES
                "notification_enabled": True,
                "language": "en"
            }
            await collection.insert_one(new_user)
            logger.info(f"✅ New user created (UNVERIFIED): {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Error creating user {user_id}: {e}")
            return False

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user data with activity update"""
        collection = self.get_collection('users')
        if collection is None:
            return None
        try:
            user = await collection.find_one({"user_id": user_id})
            if user:
                await collection.update_one(
                    {"user_id": user_id},
                    {"$set": {"last_activity": datetime.utcnow()}}
                )
            return user
        except Exception as e:
            logger.error(f"❌ Error getting user {user_id}: {e}")
            return None

    async def update_user(self, user_id: int, update_data: Dict[str, Any]) -> bool:
        """Update user data (simple $set only)"""
        return await self.update_fields(user_id, set_fields=update_data, inc_fields=None)

    # ==================== DEVICE SECURITY SYSTEM ====================
    async def is_user_verified(self, user_id: int) -> bool:
        """STRICT device verification check"""
        user = await self.get_user(user_id)
        if not user:
            return False
        return (
            bool(user.get('device_verified')) and
            (user.get('device_fingerprint') is not None) and
            user.get('verification_status') == 'verified' and
            not user.get('is_banned', False) and
            user.get('is_active', True)
        )

    async def generate_device_fingerprint(self, device_data: Dict[str, Any]) -> str:
        """Generate unique device fingerprint"""
        try:
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
                str(device_data.get('touch_support', '')),
                str(device_data.get('color_depth', '')),
                str(device_data.get('screen_orientation', ''))
            ]
            combined = '|'.join(filter(None, components))
            fingerprint = hashlib.sha256(combined.encode('utf-8')).hexdigest()
            logger.info(f"📱 Generated device fingerprint: {fingerprint[:16]}...")
            return fingerprint
        except Exception as e:
            logger.error(f"❌ Fingerprint generation error: {e}")
            fallback = hashlib.sha256(
                f"error_{datetime.utcnow().timestamp()}".encode()
            ).hexdigest()
            return fallback

    async def check_device_already_used(self, fingerprint: str) -> Dict[str, Any]:
        """Check if device is already registered"""
        device_collection = self.get_collection('device_fingerprints')
        if device_collection is None:
            return {"used": False, "reason": "database_error"}
        try:
            existing_device = await device_collection.find_one({"fingerprint": fingerprint})
            if existing_device:
                existing_user_id = existing_device.get('user_id')
                logger.warning(f"🚫 Device already used by user: {existing_user_id}")
                return {
                    "used": True,
                    "existing_user_id": existing_user_id,
                    "message": f"इस device पर पहले से user {existing_user_id} का verified account है। एक device पर केवल एक ही account allowed है।"
                }
            return {"used": False}
        except Exception as e:
            logger.error(f"❌ Device check error: {e}")
            return {"used": False, "reason": "check_error", "message": "Temporary verification check issue"}

    async def store_device_fingerprint(self, user_id: int, fingerprint: str, device_data: Dict[str, Any]):
        """Store device fingerprint in database"""
        device_collection = self.get_collection('device_fingerprints')
        if device_collection is None:
            return
        try:
            device_record = {
                "user_id": user_id,
                "fingerprint": fingerprint,
                "device_data": device_data,
                "created_at": datetime.utcnow(),
                "last_used": datetime.utcnow(),
                "is_active": True,
                "verification_ip": device_data.get('ip_address', 'unknown'),
                "user_agent": device_data.get('user_agent', 'unknown')
            }
            await device_collection.insert_one(device_record)
            logger.info(f"📱 Device fingerprint stored for user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error storing device fingerprint for user {user_id}: {e}")

    async def mark_user_verified(self, user_id: int, fingerprint: str):
        """Mark user as device verified"""
        await self.update_fields(
            user_id,
            set_fields={
                "device_verified": True,
                "device_fingerprint": fingerprint,
                "verification_status": "verified",
                "device_verified_at": datetime.utcnow()
            },
            inc_fields=None
        )

    async def verify_device_strict(self, user_id: int, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """STRICT device verification - ONE DEVICE = ONE ACCOUNT"""
        try:
            logger.info(f"🔐 Starting device verification for user {user_id}")
            fingerprint = await self.generate_device_fingerprint(device_data)
            device_check = await self.check_device_already_used(fingerprint)
            if device_check.get("used"):
                logger.warning(f"🚫 Device verification REJECTED for user {user_id}")
                return {"success": False, "message": device_check.get("message", "Device already used")}
            await self.store_device_fingerprint(user_id, fingerprint, device_data)
            await self.mark_user_verified(user_id, fingerprint)
            logger.info(f"✅ Device verification SUCCESS for user {user_id}")
            return {"success": True, "message": "Device verified successfully! आपका account अब secure है और सभी features unlock हो गए हैं।"}
        except Exception as e:
            logger.error(f"❌ Device verification error for user {user_id}: {e}")
            return {"success": False, "message": "Technical error occurred during device verification"}

    # ==================== WALLET OPERATIONS ====================
    async def record_transaction(self, user_id: int, amount: float, transaction_type: str, description: str):
        """Record transaction in history"""
        collection = self.get_collection('transactions')
        if collection is None:
            return
        try:
            transaction = {
                'transaction_id': str(uuid.uuid4()),
                'user_id': user_id,
                'amount': amount,
                'type': transaction_type,
                'description': description,
                'timestamp': datetime.utcnow(),
                'status': 'completed'
            }
            await collection.insert_one(transaction)
        except Exception as e:
            logger.error(f"❌ Transaction recording error: {e}")

    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str) -> bool:
        """Add amount to user wallet with transaction metadata"""
        # Only verified users allowed to earn/spend
        if not await self.is_user_verified(user_id):
            logger.warning(f"Wallet operation denied for unverified user {user_id}")
            return False
        col = self.get_collection('users')
        if col is None:
            return False
        try:
            user = await self.get_user(user_id)
            if not user or user.get('is_banned', False):
                logger.warning(f"Wallet operation denied for banned or missing user {user_id}")
                return False

            # Build atomic updates
            inc_fields: Dict[str, float] = {"wallet_balance": amount}
            set_fields: Dict[str, Any] = {}

            # Only count as earned on positive credits
            if amount > 0:
                inc_fields["total_earned"] = amount

            if transaction_type == 'referral' and amount > 0:
                inc_fields["referral_earnings"] = amount
                inc_fields["total_referrals"] = 1
            elif transaction_type == 'campaign' and amount > 0:
                inc_fields["campaigns_completed"] = 1
            elif transaction_type == 'gift_code' and amount > 0:
                inc_fields["gift_code_earnings"] = amount
                inc_fields["gift_codes_redeemed"] = 1

            # Prevent negative balance
            new_balance = (user.get('wallet_balance', 0.0) + amount)
            if new_balance < 0:
                logger.warning(f"Insufficient balance for user {user_id}: {user.get('wallet_balance', 0)} < {abs(amount)}")
                return False

            ok = await self.update_fields(user_id, set_fields=set_fields, inc_fields=inc_fields)
            if not ok:
                return False

            await self.record_transaction(user_id, amount, transaction_type, description)
            logger.info(f"💰 Wallet updated: User {user_id}, Amount {amount:+.2f}, Type {transaction_type}")
            return True
        except Exception as e:
            logger.error(f"❌ Wallet update error for user {user_id}: {e}")
            return False

    async def get_wallet_balance(self, user_id: int) -> float:
        """Get user's current wallet balance"""
        user = await self.get_user(user_id)
        if not user:
            return 0.0
        return float(user.get('wallet_balance', 0.0))

    async def subtract_from_wallet(self, user_id: int, amount: float, transaction_type: str, description: str) -> bool:
        """Subtract amount from wallet (for withdrawals)"""
        if amount <= 0:
            return False
        # Ensure sufficient balance
        user = await self.get_user(user_id)
        if not user:
            return False
        current_balance = float(user.get('wallet_balance', 0.0))
        if current_balance < amount:
            logger.warning(f"Insufficient balance for user {user_id}: {current_balance} < {amount}")
            return False
        # Deduct without increasing total_earned
        inc_fields = {"wallet_balance": -amount}
        ok = await self.update_fields(user_id, inc_fields=inc_fields, set_fields=None)
        if not ok:
            return False
        await self.record_transaction(user_id, -amount, transaction_type, description)
        return True

    # ==================== WITHDRAWAL OPERATIONS ====================
    async def can_withdraw(self, user_id: int) -> Dict[str, Any]:
        """Check if user can apply for withdrawal with detailed response"""
        user = await self.get_user(user_id)
        if not user:
            return {"can_withdraw": False, "reason": "User not found"}
        if not await self.is_user_verified(user_id):
            return {"can_withdraw": False, "reason": "Device not verified"}
        if user.get('is_banned', False):
            return {"can_withdraw": False, "reason": "Account banned"}

        # Get bot settings for minimum withdrawal
        settings = await self.get_bot_settings()
        min_withdrawal = float(settings.get('min_withdrawal', 10.0))
        balance = float(user.get('wallet_balance', 0.0))
        if balance < min_withdrawal:
            return {
                "can_withdraw": False,
                "reason": f"Minimum withdrawal is Rs.{min_withdrawal}",
                "current_balance": balance
            }

        # Check daily withdrawal limit
        withdrawal_collection = self.get_collection('withdrawal_requests')
        if withdrawal_collection:
            since_midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            pending_today = await withdrawal_collection.count_documents({
                'user_id': user_id,
                'request_time': {'$gte': since_midnight},
                'status': 'pending'
            })
            if pending_today > 0:
                return {"can_withdraw": False, "reason": "One withdrawal request per day allowed"}

        return {"can_withdraw": True, "max_amount": balance}

    async def record_withdrawal_request(self, user_id: int, amount: float, payment_method: str, payment_details: Dict[str, Any]) -> Dict[str, Any]:
        """Record new withdrawal request"""
        collection = self.get_collection('withdrawal_requests')
        if collection is None:
            return {"success": False, "message": "Database error"}
        try:
            can_withdraw_check = await self.can_withdraw(user_id)
            if not can_withdraw_check["can_withdraw"]:
                return {"success": False, "message": can_withdraw_check["reason"]}

            request_id = str(uuid.uuid4())[:8].upper()
            withdrawal_doc = {
                'request_id': request_id,
                'user_id': user_id,
                'amount': float(amount),
                'payment_method': payment_method,
                'payment_details': payment_details,
                'status': 'pending',
                'request_time': datetime.utcnow(),
                'processed_time': None,
                'admin_notes': ""
            }
            await collection.insert_one(withdrawal_doc)

            await self.update_fields(user_id, set_fields=None, inc_fields={"pending_withdrawals": float(amount)})
            logger.info(f"💸 New withdrawal request: {request_id} (User {user_id}, Amount Rs.{amount})")
            return {"success": True, "request_id": request_id}
        except Exception as e:
            logger.error(f"❌ Withdrawal request error for user {user_id}: {e}")
            return {"success": False, "message": "Technical error occurred"}

    # ==================== CAMPAIGN OPERATIONS WRAPPERS ====================
    async def get_campaigns(self, status: str = None, user_id: int = None) -> List[Dict[str, Any]]:
        collection = self.get_collection('campaigns')
        if collection is None:
            return []
        try:
            query: Dict[str, Any] = {}
            if status:
                query["status"] = status
            if user_id:
                query["target_users"] = {"$in": [user_id, "all"]}
            campaigns = await collection.find(query).sort("created_at", -1).to_list(100)
            return campaigns
        except Exception as e:
            logger.error(f"❌ Error getting campaigns: {e}")
            return []

    async def get_campaign_by_id(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        collection = self.get_collection('campaigns')
        if collection is None:
            return None
        try:
            return await collection.find_one({"campaign_id": campaign_id})
        except Exception as e:
            logger.error(f"❌ Error getting campaign {campaign_id}: {e}")
            return None

    # ==================== SETTINGS ====================
    async def get_bot_settings(self) -> Dict[str, Any]:
        collection = self.get_collection('bot_settings')
        if collection is None:
            return {}
        try:
            settings = await collection.find_one({"type": "main_config"})
            return settings if settings else {}
        except Exception as e:
            logger.error(f"❌ Error getting bot settings: {e}")
            return {}

    async def update_bot_settings(self, updates: Dict[str, Any]) -> bool:
        collection = self.get_collection('bot_settings')
        if collection is None:
            return False
        try:
            updates['updated_at'] = datetime.utcnow()
            await collection.update_one(
                {"type": "main_config"},
                {"$set": updates},
                upsert=True
            )
            logger.info("⚙️ Bot settings updated")
            return True
        except Exception as e:
            logger.error(f"❌ Error updating bot settings: {e}")
            return False

# Initialize user model
user_model = EnhancedUserModel()













# ============================================================
#  CHUNK 4 / 12 – GIFT CODES, CAMPAIGNS, SCREENSHOTS
# ============================================================

class GiftCodeManager:
    """Manage gift codes creation, validation and redemption"""

    def __init__(self, user_model_instance: EnhancedUserModel):
        self.user_model = user_model_instance

    async def create_gift_codes(self, amount: float, quantity: int, expiry_days: int = 30) -> List[str]:
        collection = self.user_model.get_collection('gift_codes')
        if collection is None:
            return []
        try:
            codes = []
            expiry_date = datetime.utcnow() + timedelta(days=expiry_days)
            for _ in range(quantity):
                code = f"GIFT{uuid.uuid4().hex[:8].upper()}"
                gift_doc = {
                    'code': code,
                    'amount': float(amount),
                    'created_at': datetime.utcnow(),
                    'expires_at': expiry_date,
                    'is_used': False,
                    'used_by': None,
                    'used_at": None,
                    'max_uses': 1,
                    'current_uses': 0
                }
                try:
                    await collection.insert_one(gift_doc)
                    codes.append(code)
                except Exception:
                    continue
            logger.info(f"🎁 Created {len(codes)} gift codes worth Rs.{amount} each")
            return codes
        except Exception as e:
            logger.error(f"❌ Gift code creation error: {e}")
            return []

    async def redeem_gift_code(self, user_id: int, code: str) -> Dict[str, Any]:
        if not await self.user_model.is_user_verified(user_id):
            return {"success": False, "message": "Device verification required"}
        collection = self.user_model.get_collection('gift_codes')
        if collection is None:
            return {"success": False, "message": "Service unavailable"}
        try:
            gift_code = await collection.find_one({"code": code.upper()})
            if not gift_code:
                return {"success": False, "message": "Invalid gift code"}
            if gift_code['is_used'] or gift_code['current_uses'] >= gift_code['max_uses']:
                return {"success": False, "message": "Gift code already used"}
            if datetime.utcnow() > gift_code['expires_at']:
                return {"success": False, "message": "Gift code expired"}
            if gift_code.get('used_by') == user_id:
                return {"success": False, "message": "You already redeemed this code"}

            await collection.update_one(
                {"code": code.upper()},
                {
                    "$set": {
                        "is_used": True,
                        "used_by": user_id,
                        "used_at": datetime.utcnow(),
                    },
                    "$inc": {"current_uses": 1}
                }
            )
            amount = float(gift_code['amount'])
            ok = await self.user_model.add_to_wallet(
                user_id, amount, "gift_code", f"Gift code redeemed: {code}"
            )
            if not ok:
                # rollback code usage if wallet failed for any reason
                await collection.update_one(
                    {"code": code.upper()},
                    {
                        "$set": {
                            "is_used": False,
                            "used_by": None,
                            "used_at": None,
                        },
                        "$inc": {"current_uses": -1}
                    }
                )
                return {"success": False, "message": "Failed to credit wallet, try again"}
            logger.info(f"🎁 Gift code redeemed: {code} by user {user_id} (Rs.{amount})")
            return {"success": True, "amount": amount, "message": f"Rs.{amount} added to your wallet!"}
        except Exception as e:
            logger.error(f"❌ Gift code redemption error: {e}")
            return {"success": False, "message": "Technical error occurred"}

class CampaignManager:
    """Complete campaign management with admin controls"""
    def __init__(self, user_model_instance: EnhancedUserModel):
        self.user_model = user_model_instance

    async def create_campaign(self, campaign_data: Dict[str, Any]) -> Dict[str, Any]:
        collection = self.user_model.get_collection('campaigns')
        if collection is None:
            return {"success": False, "message": "Database error"}
        try:
            campaign_id = f"CAMP{uuid.uuid4().hex[:8].upper()}"
            campaign_doc = {
                'campaign_id': campaign_id,
                'name': campaign_data.get('name', ''),
                'description': campaign_data.get('description', ''),
                'url': campaign_data.get('url', ''),
                'image_url': campaign_data.get('image_url', ''),
                'caption': campaign_data.get('caption', ''),
                'reward_amount': float(campaign_data.get('reward_amount', 5.0)),
                'requires_screenshot': campaign_data.get('requires_screenshot', False),
                'status': 'active',
                'created_at': datetime.utcnow(),
                'created_by': 'admin',
                'total_submissions': 0,
                'approved_submissions': 0,
                'rejected_submissions': 0,
                'max_participants': int(campaign_data.get('max_participants', 0)),
                'current_participants': 0,
                'start_date': campaign_data.get('start_date', datetime.utcnow()),
                'end_date': campaign_data.get('end_date'),
                'category': campaign_data.get('category', 'general'),
                'priority': campaign_data.get('priority', 'normal'),
                'instructions': campaign_data.get('instructions', ''),
                'auto_approve': campaign_data.get('auto_approve', False)
            }
            await collection.insert_one(campaign_doc)
            logger.info(f"📊 New campaign created: {campaign_id} - {campaign_data.get('name')}")
            return {"success": True, "campaign_id": campaign_id}
        except Exception as e:
            logger.error(f"❌ Campaign creation error: {e}")
            return {"success": False, "message": "Technical error occurred"}

    async def update_campaign(self, campaign_id: str, updates: Dict[str, Any]) -> bool:
        collection = self.user_model.get_collection('campaigns')
        if collection is None:
            return False
        try:
            updates['updated_at'] = datetime.utcnow()
            res = await collection.update_one({"campaign_id": campaign_id}, {"$set": updates})
            if res.modified_count > 0:
                logger.info(f"📝 Campaign updated: {campaign_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Campaign update error: {e}")
            return False

    async def delete_campaign(self, campaign_id: str) -> bool:
        return await self.update_campaign(campaign_id, {"status": "deleted"})

    async def get_active_campaigns(self, limit: int = 50) -> List[Dict[str, Any]]:
        collection = self.user_model.get_collection('campaigns')
        if collection is None:
            return []
        try:
            campaigns = await collection.find({
                "status": "active",
                "$or": [
                    {"end_date": {"$exists": False}},
                    {"end_date": {"$gte": datetime.utcnow()}}
                ]
            }).sort("priority", -1).limit(limit).to_list(limit)
            return campaigns
        except Exception as e:
            logger.error(f"❌ Error getting active campaigns: {e}")
            return []

    async def get_campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        campaigns_col = self.user_model.get_collection('campaigns')
        screenshots_col = self.user_model.get_collection('screenshots')
        if not campaigns_col or not screenshots_col:
            return {}
        try:
            campaign = await campaigns_col.find_one({"campaign_id": campaign_id})
            if not campaign:
                return {}
            screenshot_stats = await screenshots_col.aggregate([
                {"$match": {"campaign_id": campaign_id}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}}
            ]).to_list(10)
            stats = {
                "campaign_id": campaign_id,
                "name": campaign.get('name', ''),
                "total_submissions": campaign.get('total_submissions', 0),
                "approved": 0,
                "rejected": 0,
                "pending": 0,
                "reward_paid": 0.0
            }
            for stat in screenshot_stats:
                status = stat['_id']
                count = stat['count']
                if status == 'approved':
                    stats['approved'] = count
                    stats['reward_paid'] = count * float(campaign.get('reward_amount', 0.0))
                elif status == 'rejected':
                    stats['rejected'] = count
                elif status == 'pending':
                    stats['pending'] = count
            return stats
        except Exception as e:
            logger.error(f"❌ Error getting campaign stats: {e}")
            return {}

    async def can_user_participate(self, user_id: int, campaign_id: str) -> Dict[str, Any]:
        if not await self.user_model.is_user_verified(user_id):
            return {"can_participate": False, "reason": "Device verification required"}
        campaign = await self.user_model.get_campaign_by_id(campaign_id)
        if not campaign:
            return {"can_participate": False, "reason": "Campaign not found"}
        if campaign.get('status') != 'active':
            return {"can_participate": False, "reason": "Campaign not active"}

        end_date = campaign.get('end_date')
        if end_date and datetime.utcnow() > end_date:
            return {"can_participate": False, "reason": "Campaign has ended"}

        max_participants = int(campaign.get('max_participants', 0))
        if max_participants > 0:
            current_participants = int(campaign.get('current_participants', 0))
            if current_participants >= max_participants:
                return {"can_participate": False, "reason": "Campaign is full"}

        screenshots_collection = self.user_model.get_collection('screenshots')
        if screenshots_collection is not None:
            existing = await screenshots_collection.find_one({
                "user_id": user_id,
                "campaign_id": campaign_id
            })
            if existing:
                return {"can_participate": False, "reason": "You already participated in this campaign"}
        return {"can_participate": True}

class ScreenshotManager:
    """Handle screenshot uploads, approvals, and file management"""

    def __init__(self, user_model_instance: EnhancedUserModel):
        self.user_model = user_model_instance
        self.upload_dir = "uploads/screenshots"
        os.makedirs(self.upload_dir, exist_ok=True)

    async def save_screenshot_file(self, file_content: bytes, user_id: int, campaign_id: str) -> Dict[str, Any]:
        """Save uploaded screenshot file"""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{user_id}_{campaign_id}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
            file_path = os.path.join(self.upload_dir, filename)
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(file_content)
            file_size = len(file_content)
            logger.info(f"📷 Screenshot saved: {filename} ({file_size} bytes)")
            return {"success": True, "file_path": file_path, "filename": filename, "file_size": file_size}
        except Exception as e:
            logger.error(f"❌ Screenshot save error: {e}")
            return {"success": False, "message": "Failed to save file"}

    async def process_screenshot_submission(self, user_id: int, campaign_id: str, file_content: bytes) -> Dict[str, Any]:
        """Process complete screenshot submission"""
        try:
            campaign_manager = CampaignManager(self.user_model)
            participation_check = await campaign_manager.can_user_participate(user_id, campaign_id)
            if not participation_check["can_participate"]:
                return {"success": False, "message": participation_check["reason"]}

            file_result = await self.save_screenshot_file(file_content, user_id, campaign_id)
            if not file_result["success"]:
                return file_result

            submission_id = str(uuid.uuid4())[:8].upper()
            screenshots_col = self.user_model.get_collection('screenshots')
            if screenshots_col is None:
                return {"success": False, "message": "Database error"}

            screenshot_doc = {
                'submission_id': submission_id,
                'user_id': user_id,
                'campaign_id': campaign_id,
                'file_path': file_result["file_path"],
                'file_size': file_result["file_size"],
                'status': 'pending',
                'submitted_at': datetime.utcnow(),
                'reviewed_at': None,
                'admin_notes': ""
            }
            await screenshots_col.insert_one(screenshot_doc)

            # update stats
            await self.user_model.update_fields(user_id, set_fields=None, inc_fields={"screenshots_submitted": 1})
            campaigns_collection = self.user_model.get_collection('campaigns')
            if campaigns_collection is not None:
                await campaigns_collection.update_one(
                    {"campaign_id": campaign_id},
                    {"$inc": {"total_submissions": 1, "current_participants": 1}}
                )

            logger.info(f"📷 Screenshot submission processed: User {user_id}, Campaign {campaign_id}")
            return {"success": True, "submission_id": submission_id, "message": "Screenshot submitted successfully! It will be reviewed soon."}
        except Exception as e:
            logger.error(f"❌ Screenshot submission processing error: {e}")
            return {"success": False, "message": "Technical error occurred"}

    async def get_pending_screenshots(self, limit: int = 50) -> List[Dict[str, Any]]:
        collection = self.user_model.get_collection('screenshots')
        if collection is None:
            return []
        try:
            screenshots = await collection.find({"status": "pending"}).sort("submitted_at", 1).limit(limit).to_list(limit)
            enriched = []
            for sc in screenshots:
                user = await self.user_model.get_user(sc['user_id'])
                campaign = await self.user_model.get_campaign_by_id(sc['campaign_id'])
                enriched_screenshot = {
                    **sc,
                    "user_name": (user.get('first_name', 'Unknown') if user else 'Unknown'),
                    "campaign_name": (campaign.get('name', 'Unknown') if campaign else 'Unknown'),
                    "reward_amount": (campaign.get('reward_amount', 0.0) if campaign else 0.0)
                }
                enriched.append(enriched_screenshot)
            return enriched
        except Exception as e:
            logger.error(f"❌ Error getting pending screenshots: {e}")
            return []

    async def approve_screenshot(self, submission_id: str, admin_notes: str = "") -> Dict[str, Any]:
        collection = self.user_model.get_collection('screenshots')
        if collection is None:
            return {"success": False, "message": "Database error"}
        try:
            screenshot = await collection.find_one({"submission_id": submission_id})
            if not screenshot:
                return {"success": False, "message": "Screenshot not found"}
            if screenshot['status'] != 'pending':
                return {"success": False, "message": "Screenshot already processed"}

            campaign = await self.user_model.get_campaign_by_id(screenshot['campaign_id'])
            if not campaign:
                return {"success": False, "message": "Campaign not found"}
            reward_amount = float(campaign.get('reward_amount', 5.0))

            await collection.update_one(
                {"submission_id": submission_id},
                {"$set": {"status": "approved", "reviewed_at": datetime.utcnow(), "admin_notes": admin_notes}}
            )

            await self.user_model.add_to_wallet(
                screenshot['user_id'],
                reward_amount,
                "campaign",
                f"Screenshot approved for campaign: {campaign['name']}"
            )
            await self.user_model.update_fields(screenshot['user_id'], set_fields=None, inc_fields={"screenshots_approved": 1})

            campaigns_collection = self.user_model.get_collection('campaigns')
            if campaigns_collection is not None:
                await campaigns_collection.update_one(
                    {"campaign_id": screenshot['campaign_id']},
                    {"$inc": {"approved_submissions": 1}}
                )
            logger.info(f"✅ Screenshot approved: {submission_id} (User {screenshot['user_id']}, Reward Rs.{reward_amount})")
            return {"success": True, "reward_amount": reward_amount, "message": "Screenshot approved and user rewarded"}
        except Exception as e:
            logger.error(f"❌ Screenshot approval error: {e}")
            return {"success": False, "message": "Technical error occurred"}

    async def reject_screenshot(self, submission_id: str, admin_notes: str = "") -> Dict[str, Any]:
        collection = self.user_model.get_collection('screenshots')
        if collection is None:
            return {"success": False, "message": "Database error"}
        try:
            screenshot = await collection.find_one({"submission_id": submission_id})
            if not screenshot:
                return {"success": False, "message": "Screenshot not found"}
            if screenshot['status'] != 'pending':
                return {"success": False, "message": "Screenshot already processed"}

            await collection.update_one(
                {"submission_id": submission_id},
                {"$set": {"status": "rejected", "reviewed_at": datetime.utcnow(), "admin_notes": admin_notes}}
            )

            await self.user_model.update_fields(screenshot['user_id'], set_fields=None, inc_fields={"screenshots_rejected": 1})

            campaigns_collection = self.user_model.get_collection('campaigns')
            if campaigns_collection is not None:
                await campaigns_collection.update_one(
                    {"campaign_id": screenshot['campaign_id']},
                    {"$inc": {"rejected_submissions": 1}}
                )
            logger.info(f"❌ Screenshot rejected: {submission_id} (User {screenshot['user_id']})")
            return {"success": True, "message": "Screenshot rejected"}
        except Exception as e:
            logger.error(f"❌ Screenshot rejection error: {e}")
            return {"success": False, "message": "Technical error occurred"}

    async def bulk_approve_screenshots(self, submission_ids: List[str]) -> Dict[str, Any]:
        results = {"approved": 0, "failed": 0, "total_reward": 0.0}
        for sid in submission_ids:
            result = await self.approve_screenshot(sid, "Bulk approved")
            if result.get("success"):
                results["approved"] += 1
                results["total_reward"] += float(result.get("reward_amount", 0.0))
            else:
                results["failed"] += 1
        logger.info(f"📊 Bulk approval completed: {results['approved']} approved, {results['failed']} failed")
        return results

    async def create_screenshots_zip(self, submission_ids: List[str] = None) -> Optional[str]:
        try:
            collection = self.user_model.get_collection('screenshots')
            if collection is None:
                return None
            query: Dict[str, Any] = {}
            if submission_ids:
                query["submission_id"] = {"$in": submission_ids}
            else:
                week_ago = datetime.utcnow() - timedelta(days=7)
                query = {"status": "approved", "reviewed_at": {"$gte": week_ago}}

            screenshots = await collection.find(query).to_list(1000)
            if not screenshots:
                return None

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"screenshots_{timestamp}.zip"
            zip_path = os.path.join("uploads", zip_filename)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for sc in screenshots:
                    file_path = sc.get('file_path')
                    if file_path and os.path.exists(file_path):
                        user_id = sc['user_id']
                        campaign_id = sc['campaign_id']
                        submission_id = sc['submission_id']
                        zip_filename_in_archive = f"{user_id}_{campaign_id}_{submission_id}.jpg"
                        zipf.write(file_path, zip_filename_in_archive)

            logger.info(f"📦 Screenshots ZIP created: {zip_filename} ({len(screenshots)} files)")
            return zip_path
        except Exception as e:
            logger.error(f"❌ ZIP creation error: {e}")
            return None

# Initialize managers
campaign_manager = CampaignManager(user_model)
screenshot_manager = ScreenshotManager(user_model)
gift_code_manager = GiftCodeManager(user_model)














# ============================================================
#  CHUNK 5 / 12 – PAYMENTS (RAZORPAY/UPI), PAYMENT MANAGER
# ============================================================

class PaymentGateway:
    """Abstracted payment interactions (mockable)"""

    def __init__(self, user_model_instance: EnhancedUserModel):
        self.user_model = user_model_instance

    async def is_enabled(self, gateway_name: str) -> bool:
        settings = await self.user_model.get_bot_settings()
        gateways = settings.get("payment_gateways", {})
        return bool(gateways.get(gateway_name, {}).get("enabled", False))

    async def get_config(self, gateway_name: str) -> Dict[str, Any]:
        settings = await self.user_model.get_bot_settings()
        return settings.get("payment_gateways", {}).get(gateway_name, {})

    # Mock/create payment intent for Razorpay (or similar)
    async def create_razorpay_order(self, amount_rs: float, receipt_id: str) -> Dict[str, Any]:
        if not await self.is_enabled("razorpay"):
            return {"success": False, "message": "Razorpay disabled"}
        config = await self.get_config("razorpay")
        # In production, call Razorpay Orders API using api_key/api_secret
        order_id = f"order_{uuid.uuid4().hex[:12]}"
        return {
            "success": True,
            "order_id": order_id,
            "amount": int(amount_rs * 100),
            "currency": "INR",
            "receipt": receipt_id,
            "api_key": config.get("api_key", "")
        }

    async def verify_razorpay_signature(self, payload: Dict[str, Any]) -> bool:
        # In production, compute signature via HMAC-SHA256 with api_secret
        # Here, simulate a pass-through verification
        return True

    # Simple UPI flow (manual confirmation)
    async def create_upi_request(self, amount_rs: float, note: str) -> Dict[str, Any]:
        if not await self.is_enabled("upi"):
            return {"success": False, "message": "UPI disabled"}
        config = await self.get_config("upi")
        upi_id = config.get("api_key", "")
        req_id = f"upi_{uuid.uuid4().hex[:8]}"
        return {"success": True, "request_id": req_id, "upi_id": upi_id, "amount": amount_rs, "note": note}

class PaymentManager:
    """Handles withdrawals processing and admin settle/reject"""

    def __init__(self, user_model_instance: EnhancedUserModel, gateway: PaymentGateway):
        self.user_model = user_model_instance
        self.gateway = gateway

    async def process_withdrawal(self, request_id: str, approve: bool, admin_notes: str = "") -> Dict[str, Any]:
        withdrawals_col = self.user_model.get_collection('withdrawal_requests')
        if withdrawals_col is None:
            return {"success": False, "message": "Database error"}

        try:
            wr = await withdrawals_col.find_one({"request_id": request_id})
            if not wr:
                return {"success": False, "message": "Withdrawal request not found"}
            if wr.get("status") != "pending":
                return {"success": False, "message": "Already processed"}

            user_id = wr['user_id']
            amount = float(wr['amount'])

            if approve:
                # Deduct from wallet and mark as processed
                ok = await self.user_model.subtract_from_wallet(
                    user_id, amount, "withdrawal", f"Withdrawal processed: {request_id}"
                )
                if not ok:
                    return {"success": False, "message": "Insufficient balance or processing error"}
                await withdrawals_col.update_one(
                    {"request_id": request_id},
                    {"$set": {"status": "approved", "processed_time": datetime.utcnow(), "admin_notes": admin_notes}}
                )
                await self.user_model.update_fields(user_id, set_fields=None, inc_fields={
                    "pending_withdrawals": -amount,
                    "withdrawal_total": amount
                })
                return {"success": True, "message": "Withdrawal approved and settled"}
            else:
                # Reject and remove from pending
                await withdrawals_col.update_one(
                    {"request_id": request_id},
                    {"$set": {"status": "rejected", "processed_time": datetime.utcnow(), "admin_notes": admin_notes}}
                )
                await self.user_model.update_fields(user_id, set_fields=None, inc_fields={"pending_withdrawals": -amount})
                return {"success": True, "message": "Withdrawal rejected"}
        except Exception as e:
            logger.error(f"❌ Withdrawal processing error for {request_id}: {e}")
            return {"success": False, "message": "Technical error during processing"}

# Initialize payments
payment_gateway = PaymentGateway(user_model)
payment_manager = PaymentManager(user_model, payment_gateway)











# ============================================================
#  CHUNK 6 / 12 – TELEGRAM BOT CORE, WEBHOOK, COMMANDS
# ============================================================

def build_main_keyboard() -> ReplyKeyboardMarkup:
    settings = asyncio.get_event_loop().run_until_complete(user_model.get_bot_settings())
    btn_texts = settings.get("button_texts", {})
    order = settings.get("button_order", ["earning_apps", "gift_codes", "monthly_campaigns", "balance_check", "withdraw"])
    text_map = {k: btn_texts.get(k, k.replace('_', ' ').title()) for k in order}
    # 2 columns layout
    rows = []
    row = []
    for i, key in enumerate(order):
        row.append(KeyboardButton(text=f"{text_map[key]}"))
        if (i + 1) % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def ensure_force_join(user_id: int) -> Tuple[bool, str]:
    """Check if user joined required channels; returns (ok, message)"""
    settings = await user_model.get_bot_settings()
    channels = settings.get("force_join_channels", [])
    if not channels:
        return True, ""
    # In production, check chat member status via bot API
    # Here, we simulate pass-through
    return True, ""

async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    await user_model.create_user({
        "user_id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name
    })
    ok, msg = await ensure_force_join(tg_user.id)
    if not ok:
        await safe_send_message(context.bot, tg_user.id, f"{EMOJI['warn']} {msg}")
        return
    kb = build_main_keyboard()
    await safe_send_message(context.bot, tg_user.id, f"{EMOJI['rocket']} Welcome to Wallet Bot!\nUse the menu below.", reply_markup=kb)

async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_send_message(context.bot, update.effective_user.id,
                            "This bot lets you join campaigns, upload screenshots, earn rewards, and withdraw balance.\n"
                            "Use the menu buttons or commands:\n"
                            "/start – Show menu\n"
                            "/verify – Device verification link\n"
                            "/balance – Show wallet balance")

async def on_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bal = await user_model.get_wallet_balance(uid)
    await safe_send_message(context.bot, uid, f"{EMOJI['wallet']} Current balance: Rs.{bal:.2f}")

def build_webapp_button(url_path: str, label: str = "Open") -> ReplyKeyboardMarkup:
    web_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{url_path.lstrip('/')}"
    rows = [[KeyboardButton(text=label, web_app=WebAppInfo(url=web_url))]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    token = create_simple_token({"user_id": uid, "purpose": "device_verification"})
    kb = build_webapp_button(f"verify?token={token}", "Verify Device")
    await safe_send_message(context.bot, uid, f"{EMOJI['shield']} Tap verify to link this device.", reply_markup=kb)

def build_inline_campaign_buttons(camp: Dict[str, Any]) -> InlineKeyboardMarkup:
    buttons = []
    if camp.get("url"):
        buttons.append([InlineKeyboardButton(text="Open Link", url=camp["url"])])
    buttons.append([InlineKeyboardButton(text="Submit Screenshot", callback_data=f"submit::{camp['campaign_id']}")])
    return InlineKeyboardMarkup(buttons)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu button presses by text label."""
    text = (update.message.text or "").strip()
    settings = await user_model.get_bot_settings()
    btn_texts = settings.get("button_texts", {})
    resp_cfg = settings.get("button_responses", {})
    mapping = {btn_texts.get(k): k for k in btn_texts.keys()}

    key = mapping.get(text)
    if not key:
        await safe_send_message(context.bot, update.effective_user.id, "Use the menu buttons to navigate.")
        return

    # Force-join check if required
    cfg = resp_cfg.get(key, {})
    if cfg.get("requires_channel_join"):
        ok, msg = await ensure_force_join(update.effective_user.id)
        if not ok:
            await safe_send_message(context.bot, update.effective_user.id, f"{EMOJI['warn']} {msg}")
            return

    # Handle specific buttons
    if key == "earning_apps" or key == "monthly_campaigns":
        camps = await campaign_manager.get_active_campaigns(limit=10)
        if not camps:
            await safe_send_message(context.bot, update.effective_user.id, "No active campaigns right now.")
            return
        for camp in camps:
            caption = camp.get("caption") or camp.get("description", "")
            await safe_send_message(context.bot, update.effective_user.id,
                                    f"{EMOJI['chart']} {camp['name']}\nReward: Rs.{camp.get('reward_amount', 0.0)}\n{caption}")
            await safe_send_message(context.bot, update.effective_user.id,
                                    "Actions:", reply_markup=None)
            # send inline buttons
            try:
                await context.bot.send_message(
                    chat_id=update.effective_user.id,
                    text="",
                    reply_markup=build_inline_campaign_buttons(camp)
                )
            except Exception:
                pass
        return
    elif key == "gift_codes":
        await safe_send_message(context.bot, update.effective_user.id,
                                "Enter a gift code with: /redeem CODE123")
        return
    elif key == "balance_check":
        await on_balance(update, context)
        return
    elif key == "withdraw":
        await safe_send_message(context.bot, update.effective_user.id,
                                "Use /withdraw AMOUNT UPI_ID to request withdrawal.\nExample: /withdraw 50 user@upi")
        return

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = (query.data or "")
    if data.startswith("submit::"):
        camp_id = data.split("::", 1)[1]
        # Provide web app upload route
        token = create_simple_token({"user_id": update.effective_user.id, "campaign_id": camp_id})
        kb = build_webapp_button(f"upload?token={token}", "Upload Screenshot")
        await safe_edit_message(query, "Open upload to submit your screenshot.", reply_markup=kb)

async def on_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = (update.message.text or "").strip().split()
    if len(parts) != 2:
        await safe_send_message(context.bot, update.effective_user.id, "Usage: /redeem CODE")
        return
    code = parts[1]
    res = await gift_code_manager.redeem_gift_code(update.effective_user.id, code)
    await safe_send_message(context.bot, update.effective_user.id, res.get("message", "Done."))

async def on_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = (update.message.text or "").strip().split()
    if len(parts) < 3:
        await safe_send_message(context.bot, update.effective_user.id, "Usage: /withdraw AMOUNT UPI_ID")
        return
    try:
        amount = float(parts[1])
    except Exception:
        await safe_send_message(context.bot, update.effective_user.id, "Invalid amount.")
        return
    upi_id = parts
    check = await user_model.can_withdraw(update.effective_user.id)
    if not check.get("can_withdraw"):
        await safe_send_message(context.bot, update.effective_user.id, f"Cannot withdraw: {check.get('reason')}")
        return
    res = await user_model.record_withdrawal_request(update.effective_user.id, amount, "upi", {"upi_id": upi_id})
    await safe_send_message(context.bot, update.effective_user.id, res.get("message", f"Request ID: {res.get('request_id', '')}"))

async def init_bot() -> bool:
    """Initialize telegram bot and webhook if configured"""
    try:
        if not BOT_TOKEN or BOT_TOKEN == "REPLACE_ME":
            logger.warning("BOT_TOKEN not configured; bot will not start.")
            return False
        wallet_bot.application = (
            ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
        )
        wallet_bot.application.add_handler(CommandHandler("start", on_start))
        wallet_bot.application.add_handler(CommandHandler("help", on_help))
        wallet_bot.application.add_handler(CommandHandler("balance", on_balance))
        wallet_bot.application.add_handler(CommandHandler("verify", on_verify))
        wallet_bot.application.add_handler(CommandHandler("redeem", on_redeem))
        wallet_bot.application.add_handler(CommandHandler("withdraw", on_withdraw))
        wallet_bot.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        wallet_bot.application.add_handler(TGCallbackQueryHandler(on_callback))

        wallet_bot.bot = Bot(BOT_TOKEN)
        wallet_bot.initialized = True
        logger.info("✅ Telegram bot initialized")
        return True
    except Exception as e:
        logger.error(f"❌ Bot initialization error: {e}")
        return False











# ============================================================
#  CHUNK 7 / 12 – FASTAPI ROUTES: HEALTH, ADMIN, SETTINGS, CAMPAIGNS
# ============================================================

@app.get("/health")
async def health():
    info = {"status": "ok", "time": datetime.utcnow().isoformat(), "db": db_connected}
    try:
        if psutil:
            info.update({
                "cpu_percent": psutil.cpu_percent(interval=None),
                "mem_percent": psutil.virtual_memory().percent
            })
    except Exception:
        pass
    return info

@app.get("/", response_class=HTMLResponse)
async def root_page():
    return """
    <html><body>
    <h2>Wallet Bot Backend</h2>
    <p>Service is running.</p>
    </body></html>
    """

# ------------- Admin-protected endpoints --------------------

@app.get("/admin/settings")
async def get_settings(admin: str = Depends(authenticate_admin)):
    return await user_model.get_bot_settings()

@app.post("/admin/settings")
async def update_settings(payload: Dict[str, Any], admin: str = Depends(authenticate_admin)):
    ok = await user_model.update_bot_settings(payload or {})
    if not ok:
        raise HTTPException(500, "Failed to update settings")
    return {"success": True}

@app.post("/admin/campaigns")
async def admin_create_campaign(payload: Dict[str, Any], admin: str = Depends(authenticate_admin)):
    res = await campaign_manager.create_campaign(payload)
    if not res.get("success"):
        raise HTTPException(500, res.get("message", "Error"))
    return res

@app.patch("/admin/campaigns/{campaign_id}")
async def admin_update_campaign(campaign_id: str, payload: Dict[str, Any], admin: str = Depends(authenticate_admin)):
    ok = await campaign_manager.update_campaign(campaign_id, payload)
    if not ok:
        raise HTTPException(500, "Failed to update campaign")
    return {"success": True}

@app.get("/admin/campaigns")
async def admin_list_campaigns(admin: str = Depends(authenticate_admin)):
    camps = await user_model.get_campaigns()
    return {"campaigns": camps}

@app.get("/admin/screenshots/pending")
async def admin_pending_screenshots(admin: str = Depends(authenticate_admin)):
    items = await screenshot_manager.get_pending_screenshots(limit=100)
    return {"pending": items}

@app.post("/admin/screenshots/{submission_id}/approve")
async def admin_approve_screenshot(submission_id: str, payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    notes = (payload or {}).get("notes", "")
    res = await screenshot_manager.approve_screenshot(submission_id, notes)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Error"))
    return res

@app.post("/admin/screenshots/{submission_id}/reject")
async def admin_reject_screenshot(submission_id: str, payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    notes = (payload or {}).get("notes", "")
    res = await screenshot_manager.reject_screenshot(submission_id, notes)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Error"))
    return res

@app.post("/admin/screenshots/zip")
async def admin_zip_screenshots(payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    ids = (payload or {}).get("submission_ids")
    path = await screenshot_manager.create_screenshots_zip(ids)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "No screenshots to zip")
    return FileResponse(path, filename=os.path.basename(path), media_type="application/zip")

@app.get("/admin/withdrawals/pending")
async def admin_pending_withdrawals(admin: str = Depends(authenticate_admin)):
    col = user_model.get_collection('withdrawal_requests')
    if col is None:
        raise HTTPException(500, "DB unavailable")
    items = await col.find({"status": "pending"}).sort("request_time", 1).to_list(200)
    return {"pending": items}

@app.post("/admin/withdrawals/{request_id}/approve")
async def admin_approve_withdrawal(request_id: str, payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    notes = (payload or {}).get("notes", "")
    res = await payment_manager.process_withdrawal(request_id, approve=True, admin_notes=notes)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Error"))
    return res

@app.post("/admin/withdrawals/{request_id}/reject")
async def admin_reject_withdrawal(request_id: str, payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    notes = (payload or {}).get("notes", "")
    res = await payment_manager.process_withdrawal(request_id, approve=False, admin_notes=notes)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Error"))
    return res















# ============================================================
#  CHUNK 8 / 12 – WEB APP PAGES: VERIFY, UPLOAD
# ============================================================

@app.get("/verify", response_class=HTMLResponse)
async def verify_page(token: str):
    # simple HTML that posts device fingerprint back
    return f"""
    <html><body>
    <h3>Device Verification</h3>
    <p>Click to verify this device with your account.</p>
    <button onclick="start()">Verify</button>
    <script>
    async function start() {{
      const token = "{token}";
      const device = {{
        user_agent: navigator.userAgent,
        language: navigator.language,
        platform: navigator.platform,
        hardware_concurrency: navigator.hardwareConcurrency || 0,
        memory: navigator.deviceMemory || 0,
        color_depth: screen.colorDepth || 0,
        screen_resolution: (screen.width + "x" + screen.height),
        timezone_offset: new Date().getTimezoneOffset(),
        touch_support: ('ontouchstart' in window),
        screen_orientation: (screen.orientation && screen.orientation.type) || ""
      }};
      const res = await fetch('/api/verify_device', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ token, device_data: device }})
      }});
      const j = await res.json();
      alert(j.message || 'Done');
    }}
    </script>
    </body></html>
    """

@app.post("/api/verify_device")
async def api_verify_device(payload: Dict[str, Any]):
    token = payload.get("token")
    device_data = payload.get("device_data", {})
    if not token:
        raise HTTPException(400, "Missing token")
    try:
        data = verify_simple_token(token)
    except HTTPException as e:
        raise e
    user_id = int(data.get("user_id", 0))
    if not user_id:
        raise HTTPException(400, "Invalid token data")
    res = await user_model.verify_device_strict(user_id, device_data)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Verification failed"))
    return res

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(token: str, campaign_id: Optional[str] = None):
    return f"""
    <html><body>
    <h3>Upload Screenshot</h3>
    <input type="file" id="file" accept="image/*"/>
    <button onclick="send()">Submit</button>
    <script>
    async function send() {{
      const token = "{token}";
      const fileInput = document.getElementById('file');
      if (!fileInput.files.length) {{ alert('Select an image'); return; }}
      const file = fileInput.files[0];
      const buf = await file.arrayBuffer();
      const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
      const res = await fetch('/api/upload_screenshot', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          token, filename: file.name, content_b64: b64
        }})
      }});
      const j = await res.json();
      alert(j.message || 'Done');
    }}
    </script>
    </body></html>
    """

@app.post("/api/upload_screenshot")
async def api_upload_screenshot(payload: Dict[str, Any]):
    token = payload.get("token")
    filename = payload.get("filename", "upload.jpg")
    content_b64 = payload.get("content_b64")
    if not token or not content_b64:
        raise HTTPException(400, "Missing data")
    try:
        data = verify_simple_token(token)
    except HTTPException as e:
        raise e
    user_id = int(data.get("user_id", 0))
    campaign_id = data.get("campaign_id")
    if not user_id or not campaign_id:
        raise HTTPException(400, "Invalid token data")
    try:
        content = base64.b64decode(content_b64.encode())
    except Exception:
        raise HTTPException(400, "Invalid image data")
    res = await screenshot_manager.process_screenshot_submission(user_id, campaign_id, content)
    if not res.get("success"):
        raise HTTPException(400, res.get("message", "Upload failed"))
    return res

# Static files (optional for logos/assets)
app.mount("/static", StaticFiles(directory="static"), name="static")









# ============================================================
#  CHUNK 9 / 12 – ADMIN UTIL: GIFT CODES, API KEYS, FORCE JOIN
# ============================================================

@app.post("/admin/giftcodes")
async def admin_create_giftcodes(payload: Dict[str, Any], admin: str = Depends(authenticate_admin)):
    amount = float(payload.get("amount", 5.0))
    qty = int(payload.get("quantity", 1))
    expiry = int(payload.get("expiry_days", 30))
    codes = await gift_code_manager.create_gift_codes(amount, qty, expiry)
    return {"success": True, "codes": codes}

@app.post("/admin/force_join")
async def admin_set_force_join(payload: Dict[str, Any], admin: str = Depends(authenticate_admin)):
    channels = payload.get("channels", [])
    settings = {"force_join_channels": channels}
    ok = await user_model.update_bot_settings(settings)
    if not ok:
        raise HTTPException(500, "Failed to update force join")
    return {"success": True}

@app.post("/admin/api_keys")
async def admin_create_api_key(payload: Dict[str, Any] = None, admin: str = Depends(authenticate_admin)):
    col = user_model.get_collection("api_keys")
    if col is None:
        raise HTTPException(500, "DB unavailable")
    key = uuid.uuid4().hex
    doc = {"api_key": key, "created_at": datetime.utcnow(), "active": True}
    await col.insert_one(doc)
    return {"success": True, "api_key": key}











# ============================================================
#  CHUNK 10 / 12 – STARTUP/SHUTDOWN, RUNNER
# ============================================================

@app.on_event("startup")
async def on_startup():
    ok_db = await init_database()
    ok_bot = await init_bot()
    logger.info(f"Startup: db={ok_db}, bot={ok_bot}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        if wallet_bot.application:
            await wallet_bot.application.shutdown()
        if db_client:
            db_client.close()
    except Exception:
        pass
    logger.info("Shutdown complete")

def run_local():
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)

if __name__ == "__main__":
    run_local()















# ============================================================
#  CHUNK 11 / 12 – MINI ADMIN HTML (OPTIONAL)
# ============================================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_home():
    return """
    <html><body>
    <h3>Admin Panel (Minimal)</h3>
    <ul>
      <li>GET /admin/settings</li>
      <li>POST /admin/settings</li>
      <li>POST /admin/campaigns</li>
      <li>GET /admin/campaigns</li>
      <li>GET /admin/screenshots/pending</li>
      <li>POST /admin/screenshots/{submission_id}/approve</li>
      <li>POST /admin/screenshots/{submission_id}/reject</li>
      <li>GET /admin/withdrawals/pending</li>
      <li>POST /admin/withdrawals/{request_id}/approve</li>
      <li>POST /admin/withdrawals/{request_id}/reject</li>
      <li>POST /admin/giftcodes</li>
      <li>POST /admin/force_join</li>
      <li>POST /admin/api_keys</li>
    </ul>
    </body></html>
    """







# ============================================================
#  CHUNK 12 / 12 – NOTES & QUICK SETUP
# ============================================================
"""
Environment variables required:
- BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
- ADMIN_USERNAME=admin (change)
- ADMIN_PASSWORD=admin123 (change)
- ADMIN_CHAT_ID=0 (optional)
- MONGODB_URL=mongodb://localhost:27017/walletbot
- RENDER_EXTERNAL_URL=https://your-domain.tld
- PORT=8000

Quick run (local):
1) Python 3.10+, install:
   pip install fastapi uvicorn[standard] motor python-telegram-bot aiofiles psutil
2) Set env vars (at least BOT_TOKEN, RENDER_EXTERNAL_URL).
3) python main.py
4) Talk to your bot on Telegram: /start

Key flows:
- Device verify: User taps /verify -> opens /verify webapp -> posts fingerprint -> stored and marks verified.
- Campaigns: Admin creates campaign -> users open, submit screenshots via /upload -> admin approves/rejects.
- Wallet: Credits on approvals/gift code; withdrawals via /withdraw and admin settlement.

Security reminders:
- Replace simple token approach with JWT for production.
- Implement real Razorpay/UPI integrations for live payments.
- Restrict CORS and static exposure for production.
"""
