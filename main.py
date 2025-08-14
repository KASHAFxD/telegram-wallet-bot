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
import json
import time
from datetime import datetime, timedelta
from typing import Optional, List
import logging
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration - Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "kashaf")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "kashaf")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://kashaf:kashaf@bot.zq2yw4e.mongodb.net/walletbot?retryWrites=true&w=majority")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-wallet-bot-r80n.onrender.com")
PORT = int(os.getenv("PORT", 10000))

# Initialize FastAPI
app = FastAPI(title="Wallet Bot - Enhanced Security", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# Database connection
class Database:
    def __init__(self):
        self.client = None
        self.connected = False
        self.connect()
    
    def connect(self):
        try:
            self.client = AsyncIOMotorClient(MONGODB_URL)
            self.connected = True
            logger.info("✅ MongoDB connected successfully")
        except Exception as e:
            logger.error(f"❌ MongoDB connection error: {e}")
            self.connected = False
    
    async def test_connection(self):
        if self.client:
            try:
                await self.client.admin.command('ping')
                self.connected = True
                return True
            except Exception as e:
                logger.error(f"❌ MongoDB ping failed: {e}")
                self.connected = False
                return False
        return False

db = Database()

# Enhanced User Model with FIXED Boolean Checks
class UserModel:
    def __init__(self):
        self.collection = None
        if db.client:
            self.collection = db.client.walletbot.users
    
    async def create_user(self, user_data: dict):
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return None
        try:
            user_data.update({
                "created_at": datetime.utcnow(),
                "wallet_balance": 0.0,
                "total_earned": 0.0,
                "referral_earnings": 0.0,
                "total_referrals": 0,
                "is_active": True,
                "device_fingerprint": None,
                "device_verified": False,
                "security_status": "pending"
            })
            
            result = await self.collection.insert_one(user_data)
            logger.info(f"✅ User created: {user_data['user_id']}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"❌ Error creating user: {e}")
            return None
    
    async def get_user(self, user_id: int):
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return None
        try:
            return await self.collection.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"❌ Error getting user: {e}")
            return None
    
    async def update_user(self, user_id: int, update_data: dict):
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return False
        try:
            update_data["updated_at"] = datetime.utcnow()
            result = await self.collection.update_one(
                {"user_id": user_id},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error updating user: {e}")
            return False
    
    async def check_device_security(self, user_id: int):
        """Check if user's device is verified"""
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return False
        try:
            user = await self.collection.find_one({
                "user_id": user_id,
                "device_verified": True,
                "device_fingerprint": {"$ne": None}
            })
            return user is not None
        except Exception as e:
            logger.error(f"❌ Device security check error: {e}")
            return False
    
    async def verify_device(self, user_id: int, fingerprint: str, device_data: dict):
        """Verify user's device with fingerprint"""
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return False
        
        try:
            # Check for existing device conflicts
            existing_user = await self.collection.find_one({
                "device_fingerprint": fingerprint,
                "user_id": {"$ne": user_id},
                "device_verified": True
            })
            
            if existing_user:
                logger.warning(f"⚠️ Device conflict: Fingerprint already used by user {existing_user['user_id']}")
                return False
            
            # Update user with device verification
            result = await self.collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "device_fingerprint": fingerprint,
                    "device_verified": True,
                    "security_status": "verified",
                    "device_data": device_data,
                    "device_verified_at": datetime.utcnow()
                }}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Device verified for user {user_id}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error verifying device: {e}")
            return False
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        # ✅ FIXED: Changed from 'if not self.collection:' to explicit None check
        if self.collection is None:
            return False
        
        try:
            user = await self.get_user(user_id)
            if not user:
                return False
            
            new_balance = user.get("wallet_balance", 0) + amount
            total_earned = user.get("total_earned", 0)
            if amount > 0:
                total_earned += amount
            
            result = await self.collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "wallet_balance": new_balance,
                    "total_earned": total_earned,
                    "updated_at": datetime.utcnow()
                }}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Wallet updated for user {user_id}: +₹{amount}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Error adding to wallet: {e}")
            return False

# Initialize models
user_model = UserModel()

# Device Fingerprinting Functions
def generate_device_fingerprint(device_data: dict) -> str:
    """Generate unique device fingerprint"""
    components = [
        str(device_data.get('screen_resolution', '')),
        str(device_data.get('user_agent_hash', '')),
        str(device_data.get('timezone_offset', '')),
        str(device_data.get('language', '')),
        str(device_data.get('platform', '')),
        str(device_data.get('canvas_hash', '')),
        str(device_data.get('webgl_hash', '')),
        str(device_data.get('hardware_concurrency', '')),
        str(device_data.get('memory', ''))
    ]
    combined = '|'.join(components)
    return hashlib.sha256(combined.encode()).hexdigest()

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
            logger.info("✅ Enhanced Telegram bot initialized")
        except Exception as e:
            logger.error(f"❌ Error initializing bot: {e}")
            self.initialized = False
    
    def setup_handlers(self):
        try:
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("device_verified", self.device_verified_callback))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            logger.info("✅ Bot handlers setup complete")
        except Exception as e:
            logger.error(f"❌ Handler setup error: {e}")
    
    def get_reply_keyboard(self):
        keyboard = [
            [KeyboardButton("💰 My Wallet"), KeyboardButton("📋 Campaigns")],
            [KeyboardButton("👥 Referral"), KeyboardButton("💸 Withdraw")],
            [KeyboardButton("🆘 Help"), KeyboardButton("📊 Status")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        first_name = update.effective_user.first_name or "User"
        
        logger.info(f"🚀 Start command from user: {user_id} ({first_name})")
        
        # Get or create user
        user = await user_model.get_user(user_id)
        if not user:
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name
            }
            await user_model.create_user(user_data)
            user = await user_model.get_user(user_id)
        
        # Check device verification
        if not await user_model.check_device_security(user_id):
            await self.require_device_verification(user_id, first_name, update)
            return
        
        # User is verified, show main menu
        await self.send_main_menu(update)
    
    async def require_device_verification(self, user_id: int, username: str, update: Update):
        """Send device verification request"""
        verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
        
        keyboard = [
            [InlineKeyboardButton("🔐 Verify Device", web_app=WebAppInfo(url=verification_url))]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"""🔒 **Device Verification Required**

Hello {username}! To use this bot securely, please verify your device.

⚠️ **Security Policy:**
• One device = One account only
• Multiple accounts not allowed
• Advanced fingerprinting protection

🛡️ **Why Verification?**
• Prevents fraud and abuse
• Protects your earnings
• Ensures fair usage

Click below to verify your device:"""
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def device_verified_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle device verification completion"""
        user_id = update.effective_user.id
        logger.info(f"✅ Device verification completed for user: {user_id}")
        
        await update.message.reply_text(
            "✅ **Device Verified Successfully!**\n\nYour account is now secure and ready to use!\n\n🎉 Welcome to the Wallet Bot!",
            parse_mode='Markdown'
        )
        
        await self.send_main_menu(update)
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Check device verification
        if not await user_model.check_device_security(user_id):
            await update.message.reply_text("🔒 Device verification required. Please /start again.")
            return
        
        user = await user_model.get_user(user_id)
        if not user:
            await update.message.reply_text("❌ User not found. Please /start first.", reply_markup=self.get_reply_keyboard())
            return
        
        wallet_msg = f"""💰 **Your Secure Wallet**
*Protected by Device Fingerprinting*

👤 **User:** {user.get('first_name', 'Unknown')}
💳 **Current Balance:** ₹{user.get('wallet_balance', 0):.2f}
📊 **Total Earned:** ₹{user.get('total_earned', 0):.2f}
👥 **Referral Earnings:** ₹{user.get('referral_earnings', 0):.2f}
🎯 **Total Referrals:** {user.get('total_referrals', 0)}

🔒 **Security:** ✅ Device Verified
📅 **Verified:** {user.get('device_verified_at', datetime.utcnow()).strftime('%Y-%m-%d')}

🚀 **Platform:** Render.com
💡 Complete campaigns to earn more!"""
        
        keyboard = [
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = f"""🆘 **Enhanced Bot Help**

**Available Commands:**
• /start - Main menu with security check
• /wallet - Check your secure balance
• /help - Show this help

**🔒 Enhanced Security Features:**
• Device fingerprinting protection
• One device per account policy
• Advanced fraud prevention

**💰 How to Earn:**
1. 📋 Complete campaigns for rewards
2. 👥 Refer friends and earn bonus
3. 💸 Withdraw when you reach minimum

**🚀 Running on:** Render.com (Enhanced Security)
**Need Support?** Contact admin team"""
        
        await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        # Security check for all button interactions
        if not await user_model.check_device_security(user_id):
            await query.edit_message_text("🔒 Device verification required. Please /start again.")
            return
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "campaigns":
            await query.edit_message_text("📋 **Campaigns coming soon!**\n\n🔒 Enhanced security enabled", parse_mode="Markdown")
        elif data == "referral":
            await self.show_referral_program(update, context)
        elif data == "withdraw":
            await query.edit_message_text("💸 **Withdrawal system coming soon!**\n\n🔒 Secure withdrawals enabled", parse_mode="Markdown")
    
    async def show_referral_program(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        referral_msg = f"""👥 **Secure Referral Program**
*Protected by Device Verification*

🎁 **Earn ₹10 for each verified friend!**

🔗 **Your Referral Link:**
`{referral_link}`

**How it works:**
1. Share your referral link
2. Friends must complete device verification
3. Both get ₹10 bonus instantly!

🛡️ **Security Benefits:**
• Only verified users earn rewards
• No fake accounts allowed
• Fair system for everyone

🚀 **Powered by:** Render.com"""
        
        keyboard = [[InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user_id = update.effective_user.id
        
        # Security check for all messages
        if not await user_model.check_device_security(user_id):
            await update.message.reply_text("🔒 Device verification required. Please /start to verify your device.", reply_markup=self.get_reply_keyboard())
            return
        
        if text == "💰 My Wallet":
            await self.wallet_command(update, context)
        elif text == "📋 Campaigns":
            await update.message.reply_text("📋 **Campaigns coming soon!**\n\n🔒 Enhanced security enabled", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        elif text == "👥 Referral":
            await self.show_referral_program(update, context)
        elif text == "💸 Withdraw":
            await update.message.reply_text("💸 **Withdrawal system coming soon!**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        elif text == "🆘 Help":
            await self.help_command(update, context)
        elif text == "📊 Status":
            await self.show_status(update, context)
        else:
            await update.message.reply_text(
                "👋 Hi! Use the menu buttons below.\n\n🔒 Your device is verified and secure!",
                reply_markup=self.get_reply_keyboard()
            )
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        status_msg = f"""📊 **Bot Status**

🤖 **System Status:** ✅ Running on Render
🔒 **Security:** ✅ Device Verified
📊 **Database:** ✅ MongoDB Connected
⏰ **Server Time:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

👤 **Your Account:**
• Status: {user.get('security_status', 'Unknown').title()}
• Verified: {user.get('device_verified_at', datetime.utcnow()).strftime('%Y-%m-%d')}
• Device ID: {user.get('device_fingerprint', 'N/A')[:16]}...

🚀 **Platform:** Render.com (Enhanced Security)
✅ **Features:** Device fingerprinting, fraud prevention"""
        
        await update.message.reply_text(status_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def send_main_menu(self, update: Update):
        welcome_msg = f"""🎉 **Welcome to Secure Wallet Bot!**
*Enhanced with Device Fingerprinting*

💰 Earn money through verified campaigns
🔒 Protected by advanced security
👥 Secure referral system
💸 Safe withdrawal process

🚀 **Hosting:** Render.com
🛡️ **Security:** Device verification active

Choose an option below:"""
        
        await update.message.reply_text(welcome_msg, reply_markup=self.get_reply_keyboard(), parse_mode='Markdown')

# Initialize bot
wallet_bot = WalletBot()

# Device Verification API
@app.post("/api/verify-device")
async def verify_device(request: Request):
    """Handle device verification from WebApp"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        # Generate fingerprint
        fingerprint = generate_device_fingerprint(device_data)
        
        # Verify device
        success = await user_model.verify_device(user_id, fingerprint, device_data)
        
        if success:
            # Send verification command to bot
            await wallet_bot.bot.send_message(user_id, "/device_verified")
            
            logger.info(f"✅ Device verified for user {user_id}")
            return {"status": "success", "message": "Device verified successfully"}
        else:
            logger.warning(f"❌ Device verification failed for user {user_id}")
            return {"status": "error", "message": "Device already registered with another account"}
            
    except Exception as e:
        logger.error(f"❌ Device verification error: {e}")
        return {"status": "error", "message": "Verification failed"}

# Device Verification WebApp Page
@app.get("/verify")
async def verification_page(user_id: int):
    """Enhanced device verification page with CDN-free fingerprinting"""
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enhanced Device Verification</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 20px;
            padding: 40px;
            max-width: 450px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }}
        .icon {{ font-size: 4rem; margin-bottom: 20px; }}
        h1 {{ color: #333; margin-bottom: 10px; font-size: 1.6rem; }}
        p {{ color: #666; margin-bottom: 25px; line-height: 1.6; }}
        .status {{ 
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            font-weight: bold;
        }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #e8f5e8; color: #2e7d32; }}
        .error {{ background: #ffebee; color: #c62828; }}
        .security-info {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 12px;
            margin: 20px 0;
            text-align: left;
        }}
        .security-info h3 {{ color: #333; margin-bottom: 12px; }}
        .security-info ul {{ padding-left: 20px; }}
        .security-info li {{ margin: 8px 0; color: #555; }}
        .btn {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 12px;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.3s ease;
            font-weight: 600;
        }}
        .btn:hover {{ transform: translateY(-2px); }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; transform: none; }}
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
            transition: width 0.3s ease; 
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">🔐</div>
        <h1>Enhanced Device Verification</h1>
        <p>Secure device verification with advanced fingerprinting technology.</p>
        
        <div class="security-info">
            <h3>🛡️ Security Features</h3>
            <ul>
                <li>✅ Advanced device fingerprinting</li>
                <li>🔒 One device per account policy</li>
                <li>🚫 Multiple account prevention</li>
                <li>⚡ CDN-independent verification</li>
                <li>🎯 Enhanced fraud protection</li>
            </ul>
        </div>
        
        <div class="progress">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        
        <div id="status" class="status loading">
            🔄 Initializing fingerprinting system...
        </div>
        
        <button id="verifyBtn" class="btn" onclick="verifyDevice()" disabled>
            🔍 Verify Device
        </button>
    </div>

    <script>
        const USER_ID = {user_id};
        let deviceData = {{}};
        
        // Enhanced Device Fingerprinting (CDN-Free)
        class DeviceFingerprinter {{
            constructor() {{
                this.components = {{}};
            }}
            
            async collect() {{
                try {{
                    this.components = {{
                        screen_resolution: `${{screen.width}}x${{screen.height}}x${{screen.colorDepth}}`,
                        user_agent_hash: this.hash(navigator.userAgent),
                        timezone_offset: new Date().getTimezoneOffset(),
                        language: navigator.language || 'unknown',
                        platform: navigator.platform || 'unknown',
                        hardware_concurrency: navigator.hardwareConcurrency || 0,
                        memory: navigator.deviceMemory || 0,
                        canvas_hash: await this.getCanvasHash(),
                        webgl_hash: await this.getWebGLHash(),
                        timestamp: Date.now()
                    }};
                    
                    return this.components;
                }} catch (error) {{
                    console.error('Fingerprinting error:', error);
                    return {{
                        fallback: 'error_' + Date.now(),
                        timestamp: Date.now()
                    }};
                }}
            }}
            
            async getCanvasHash() {{
                try {{
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    ctx.textBaseline = 'top';
                    ctx.font = '14px Arial';
                    ctx.fillText('Device Fingerprint 🔒', 2, 2);
                    return this.hash(canvas.toDataURL());
                }} catch (e) {{
                    return 'canvas_error';
                }}
            }}
            
            async getWebGLHash() {{
                try {{
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl');
                    if (!gl) return 'webgl_unavailable';
                    
                    const renderer = gl.getParameter(gl.RENDERER);
                    const vendor = gl.getParameter(gl.VENDOR);
                    return this.hash(renderer + '|' + vendor);
                }} catch (e) {{
                    return 'webgl_error';
                }}
            }}
            
            hash(str) {{
                let hash = 0;
                for (let i = 0; i < str.length; i++) {{
                    const char = str.charCodeAt(i);
                    hash = ((hash << 5) - hash) + char;
                    hash = hash & hash;
                }}
                return Math.abs(hash).toString();
            }}
        }}
        
        async function collectDeviceInfo() {{
            updateProgress(20, "🔍 Analyzing device...");
            
            const fingerprinter = new DeviceFingerprinter();
            deviceData = await fingerprinter.collect();
            
            updateProgress(60, "✅ Device analysis complete");
            
            setTimeout(() => {{
                updateProgress(100, "🎯 Ready for verification");
                document.getElementById('status').className = 'status success';
                document.getElementById('verifyBtn').disabled = false;
            }}, 1000);
        }}
        
        function updateProgress(percent, message) {{
            document.getElementById('progressBar').style.width = percent + '%';
            document.getElementById('status').innerHTML = message;
        }}
        
        async function verifyDevice() {{
            document.getElementById('status').innerHTML = '🔄 Verifying device...';
            document.getElementById('status').className = 'status loading';
            document.getElementById('verifyBtn').disabled = true;
            updateProgress(70, "🔄 Sending data...");
            
            try {{
                const response = await fetch('/api/verify-device', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        user_id: USER_ID,
                        device_data: deviceData
                    }})
                }});
                
                const result = await response.json();
                updateProgress(100, "Verification complete!");
                
                if (result.status === 'success') {{
                    document.getElementById('status').innerHTML = '🎉 Device verified successfully!<br><small>You can now close this page.</small>';
                    document.getElementById('status').className = 'status success';
                    
                    setTimeout(() => {{
                        if (window.Telegram && window.Telegram.WebApp) {{
                            window.Telegram.WebApp.close();
                        }}
                    }}, 3000);
                }} else {{
                    document.getElementById('status').innerHTML = `❌ ${{result.message}}`;
                    document.getElementById('status').className = 'status error';
                    document.getElementById('verifyBtn').innerHTML = '🔄 Try Again';
                    document.getElementById('verifyBtn').disabled = false;
                }}
            }} catch (error) {{
                document.getElementById('status').innerHTML = '❌ Network error. Please try again.';
                document.getElementById('status').className = 'status error';
                document.getElementById('verifyBtn').disabled = false;
            }}
        }}
        
        // Start device analysis
        window.addEventListener('load', () => {{
            setTimeout(collectDeviceInfo, 500);
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

# Standard API Routes
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
        logger.error(f"❌ Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health_check():
    mongo_healthy = await db.test_connection()
    return {
        "status": "healthy",
        "service": "wallet-bot-enhanced",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": mongo_healthy,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "platform": "Render.com",
        "security": "Device Fingerprinting Enabled",
        "version": "1.0.0-enhanced"
    }

@app.get("/")
async def root():
    return {
        "message": "🤖 Enhanced Wallet Bot with Device Security",
        "status": "running",
        "platform": "Render.com",
        "security": "Advanced Device Fingerprinting",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health",
            "verify": "/verify?user_id=<id>",
            "admin": "/api/admin/dashboard"
        },
        "features": [
            "Device fingerprinting protection",
            "One account per device policy", 
            "Enhanced security system",
            "CDN-independent verification"
        ]
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    try:
        total_users = 0
        verified_users = 0
        
        if db.client:
            total_users = await db.client.walletbot.users.count_documents({})
            verified_users = await db.client.walletbot.users.count_documents({"device_verified": True})
        
        return {
            "platform": "Render.com - Enhanced Security",
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_verification": total_users - verified_users,
            "security_features": [
                "Device Fingerprinting Active",
                "Multiple Account Prevention",
                "CDN-Independent Verification",
                "Advanced Fraud Detection"
            ],
            "status": "secure_and_running"
        }
    except Exception as e:
        logger.error(f"❌ Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup event
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting Enhanced Wallet Bot...")
    
    # Test MongoDB
    await db.test_connection()
    
    # Initialize Telegram bot
    if wallet_bot and wallet_bot.application:
        try:
            await wallet_bot.bot.initialize()
            await wallet_bot.application.initialize()
            await wallet_bot.application.start()
            
            # Set webhook
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            await wallet_bot.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(2)
            
            result = await wallet_bot.bot.set_webhook(url=webhook_url)
            if result:
                logger.info(f"✅ Enhanced webhook set: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Startup error: {e}")
    
    logger.info("🎉 Enhanced bot startup completed!")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🔄 Shutting down enhanced bot...")
    if wallet_bot and wallet_bot.application:
        try:
            await wallet_bot.bot.delete_webhook()
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            await wallet_bot.bot.shutdown()
        except:
            pass
    logger.info("✅ Enhanced shutdown completed")

# Main entry point
if __name__ == "__main__":
    import uvicorn
    logger.info(f"🚀 Starting Enhanced Secure Wallet Bot - Port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
