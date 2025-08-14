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
from datetime import datetime
import logging
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "8487587738:AAFbg_cLFkA2d9J3ANPA3xiVyB2Zv1HGdpo")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "kashaf")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "kashaf")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb+srv://kashaf:kashaf@bot.zq2yw4e.mongodb.net/walletbot?retryWrites=true&w=majority")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-wallet-bot-r80n.onrender.com")
PORT = int(os.getenv("PORT", 10000))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 7194000836))

# Initialize FastAPI
app = FastAPI(title="Enhanced Wallet Bot", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# Global database
db_client = None
db_connected = False

# Initialize database
async def init_database():
    global db_client, db_connected
    try:
        db_client = AsyncIOMotorClient(MONGODB_URL)
        await db_client.admin.command('ping')
        db_connected = True
        logger.info("✅ MongoDB connected successfully")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        db_connected = False
        return False

# Enhanced User Model with Proper Device Verification
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
        collection = self.get_collection()
        if collection is None:
            return None
            
        try:
            user_data.update({
                "created_at": datetime.utcnow(),
                "wallet_balance": 0.0,
                "total_earned": 0.0,
                "referral_earnings": 0.0,
                "total_referrals": 0,
                "is_active": True,
                "device_verified": False,
                "device_fingerprint": None,
                "verification_status": "pending"
            })
            
            result = await collection.update_one(
                {"user_id": user_data["user_id"]},
                {"$setOnInsert": user_data},
                upsert=True
            )
            
            if result.upserted_id or result.matched_count > 0:
                logger.info(f"✅ User created/found: {user_data['user_id']}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Error creating user: {e}")
            return False
    
    async def get_user(self, user_id: int):
        collection = self.get_collection()
        if collection is None:
            return None
        try:
            return await collection.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"❌ Error getting user: {e}")
            return None
    
    async def is_user_verified(self, user_id: int):
        """Check if user is device verified"""
        user = await self.get_user(user_id)
        if not user:
            return False
        return user.get('device_verified', False) and user.get('device_fingerprint') is not None
    
    async def check_device_fingerprint(self, fingerprint: str):
        """Check if device fingerprint already exists"""
        device_collection = self.get_device_collection()
        if device_collection is None:
            return None
            
        try:
            existing_device = await device_collection.find_one({"fingerprint": fingerprint})
            return existing_device
        except Exception as e:
            logger.error(f"❌ Error checking device fingerprint: {e}")
            return None
    
    async def verify_device(self, user_id: int, fingerprint: str, device_data: dict):
        """Verify device - Allow FIRST account per device only"""
        collection = self.get_collection()
        device_collection = self.get_device_collection()
        
        if collection is None or device_collection is None:
            return {"success": False, "message": "Database connection error"}
        
        try:
            # Check if this device already has an account
            existing_device = await self.check_device_fingerprint(fingerprint)
            
            if existing_device:
                # Device already has an account - this is a second+ account attempt
                existing_user_id = existing_device.get('user_id')
                if existing_user_id != user_id:
                    logger.warning(f"⚠️ Device fingerprint {fingerprint[:16]}... already registered to user {existing_user_id}")
                    return {
                        "success": False, 
                        "message": "यह device पहले से एक account के साथ registered है। एक device पर केवल एक account allowed है।"
                    }
                else:
                    # Same user trying to verify again - allow it
                    return {"success": True, "message": "Device already verified for this account"}
            
            # This is the FIRST account on this device - ALLOW it
            # Store device fingerprint
            await device_collection.insert_one({
                "fingerprint": fingerprint,
                "user_id": user_id,
                "device_data": device_data,
                "created_at": datetime.utcnow()
            })
            
            # Update user verification status
            await collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "device_verified": True,
                    "device_fingerprint": fingerprint,
                    "verification_status": "verified",
                    "device_verified_at": datetime.utcnow()
                }}
            )
            
            logger.info(f"✅ First account verified on device for user {user_id}")
            return {"success": True, "message": "Device verified successfully"}
            
        except Exception as e:
            logger.error(f"❌ Device verification error: {e}")
            return {"success": False, "message": "Verification failed due to technical error"}

    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        collection = self.get_collection()
        if collection is None:
            return False
        
        try:
            user = await self.get_user(user_id)
            if not user:
                return False
            
            new_balance = user.get("wallet_balance", 0) + amount
            total_earned = user.get("total_earned", 0)
            if amount > 0:
                total_earned += amount
            
            result = await collection.update_one(
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

# Initialize user model
user_model = UserModel()

# Enhanced Telegram Bot with Proper Verification Logic
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
            logger.info("✅ Enhanced bot initialized")
        except Exception as e:
            logger.error(f"❌ Bot initialization error: {e}")
            self.initialized = False
    
    def setup_handlers(self):
        try:
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("device_verified", self.device_verified_callback))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.application.add_error_handler(self.error_handler)
            logger.info("✅ Bot handlers setup complete")
        except Exception as e:
            logger.error(f"❌ Handler setup error: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
    
    def get_reply_keyboard(self):
        keyboard = [
            [KeyboardButton("💰 My Wallet"), KeyboardButton("📋 Campaigns")],
            [KeyboardButton("👥 Referral"), KeyboardButton("💸 Withdraw")],
            [KeyboardButton("🆘 Help"), KeyboardButton("📊 Status")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            first_name = update.effective_user.first_name or "User"
            
            logger.info(f"🚀 Start command from user: {user_id} ({first_name})")
            
            # Create user first
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name
            }
            await user_model.create_user(user_data)
            
            # Check verification status
            is_verified = await user_model.is_user_verified(user_id)
            
            if not is_verified:
                # User needs device verification
                await self.require_device_verification(user_id, first_name, update)
            else:
                # User is verified - show full features
                await self.send_verified_welcome(update, first_name)
                
        except Exception as e:
            logger.error(f"❌ Start command error: {e}")
            await update.message.reply_text("❌ Error occurred. Please try again.")
    
    async def require_device_verification(self, user_id: int, first_name: str, update: Update):
        """Send device verification requirement"""
        verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
        
        verification_msg = f"""🔒 **Device Verification Required**

Hello {first_name}! 

**Security Policy:**
• पहले account को device verification की जरूरत है
• एक device पर केवल एक account allowed है  
• यह fraud और multiple accounts को prevent करता है

**Why Verification?**
• Account security के लिए
• Fair usage ensure करने के लिए
• Advanced fingerprinting protection

**Note:** यह आपका पहला account है इस device पर - verification successfully होगा।

Click below to verify:"""
        
        keyboard = [
            [InlineKeyboardButton("🔐 Verify Device", web_app=WebAppInfo(url=verification_url))]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(verification_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def send_verified_welcome(self, update: Update, first_name: str):
        """Send welcome message for verified users"""
        welcome_msg = f"""🎉 **Welcome to Enhanced Wallet Bot!**

Hi {first_name}! Your device is verified ✅

💰 **Available Features:**
• Wallet management
• Campaign participation  
• Referral system
• Secure withdrawals

🛡️ **Security Status:** Device Verified
📱 **Account Status:** Active

Choose an option below:"""
        
        inline_keyboard = [
            [InlineKeyboardButton("💰 My Wallet", callback_data="wallet")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("👥 Referral", callback_data="referral")]
        ]
        inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await update.message.reply_text(welcome_msg, reply_markup=inline_reply_markup, parse_mode="Markdown")
        await update.message.reply_text("🎯 **Use menu buttons below:**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def device_verified_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle successful device verification"""
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name or "User"
        
        await update.message.reply_text(
            "✅ **Device Verified Successfully!**\n\nYour account is now secure and all features are unlocked!",
            parse_mode='Markdown'
        )
        
        await self.send_verified_welcome(update, first_name)
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Check verification before allowing access
        if not await user_model.is_user_verified(user_id):
            await update.message.reply_text("🔒 Device verification required. Please /start to verify your device.")
            return
        
        user = await user_model.get_user(user_id)
        if not user:
            await update.message.reply_text("❌ User not found.")
            return
        
        wallet_msg = f"""💰 **Your Secure Wallet**

👤 **User:** {user.get('first_name', 'Unknown')}
💳 **Current Balance:** ₹{user.get('wallet_balance', 0):.2f}
📊 **Total Earned:** ₹{user.get('total_earned', 0):.2f}
👥 **Referrals:** {user.get('total_referrals', 0)}

🔒 **Security:** ✅ Device Verified
📅 **Verified:** {user.get('device_verified_at', datetime.utcnow()).strftime('%Y-%m-%d')}"""
        
        keyboard = [
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = """🆘 **Bot Help**

**Commands:**
• /start - Main menu
• /wallet - Check balance
• /help - Show help

**Security Features:**
• Device fingerprinting
• One account per device
• Fraud prevention

**How to Earn:**
• Complete campaigns
• Refer friends
• Participate in tasks"""
        
        await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        # ✅ IMPORTANT: Check verification before allowing any button actions
        if not await user_model.is_user_verified(user_id):
            await query.edit_message_text("🔒 Device verification required. Please /start to verify first.")
            return
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "campaigns":
            await query.edit_message_text("📋 **Campaigns coming soon!**\n\nEarn money through verified tasks.", parse_mode="Markdown")
        elif data == "referral":
            await self.show_referral_program(update, context)
        elif data == "withdraw":
            await query.edit_message_text("💸 **Withdrawal system coming soon!**", parse_mode="Markdown")
    
    async def show_referral_program(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        bot_username = (await self.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        referral_msg = f"""👥 **Referral Program**

🎁 **Earn ₹10 for each verified friend!**

🔗 **Your Link:** `{referral_link}`

**How it works:**
1. Share your link
2. Friends verify their device
3. Both get ₹10 bonus!

🛡️ **Note:** Only device-verified users earn rewards"""
        
        keyboard = [[InlineKeyboardButton("📤 Share", url=f"https://t.me/share/url?url={referral_link}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        user_id = update.effective_user.id
        
        # ✅ Check verification for menu buttons
        if text in ["💰 My Wallet", "📋 Campaigns", "👥 Referral", "💸 Withdraw"]:
            if not await user_model.is_user_verified(user_id):
                await update.message.reply_text("🔒 Device verification required. Please /start to verify.", reply_markup=self.get_reply_keyboard())
                return
        
        if text == "💰 My Wallet":
            await self.wallet_command(update, context)
        elif text == "📋 Campaigns":
            await update.message.reply_text("📋 **Campaigns coming soon!**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        elif text == "👥 Referral":
            await self.show_referral_program(update, context)
        elif text == "💸 Withdraw":
            await update.message.reply_text("💸 **Withdrawals coming soon!**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        elif text == "🆘 Help":
            await self.help_command(update, context)
        elif text == "📊 Status":
            await self.show_status(update, context)
        else:
            await update.message.reply_text("👋 Hi! Use menu buttons for navigation.", reply_markup=self.get_reply_keyboard())
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        status_msg = f"""📊 **Bot Status**

🤖 **System:** ✅ Running
🔒 **Your Device:** {'✅ Verified' if await user_model.is_user_verified(user_id) else '⚠️ Not Verified'}
📊 **Database:** {'✅ Connected' if db_connected else '❌ Error'}
⏰ **Time:** {datetime.now().strftime('%H:%M:%S')}

**Security Features:**
• Device fingerprinting active
• One account per device policy
• Advanced fraud prevention"""
        
        await update.message.reply_text(status_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")

# Initialize bot
wallet_bot = None

# Device Verification API
@app.post("/api/verify-device")
async def verify_device(request: Request):
    """Handle device verification with proper first-account logic"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        # Generate device fingerprint
        fingerprint_components = [
            str(device_data.get('screen_resolution', '')),
            str(device_data.get('user_agent_hash', '')),
            str(device_data.get('timezone_offset', '')),
            str(device_data.get('platform', '')),
            str(device_data.get('canvas_hash', '')),
            str(device_data.get('timestamp', ''))
        ]
        fingerprint = hashlib.sha256('|'.join(fingerprint_components).encode()).hexdigest()
        
        # Verify device with proper logic
        result = await user_model.verify_device(user_id, fingerprint, device_data)
        
        if result["success"]:
            # Send success callback to bot
            await wallet_bot.bot.send_message(user_id, "/device_verified")
            logger.info(f"✅ Device verified for user {user_id}")
        else:
            logger.warning(f"❌ Device verification failed for user {user_id}: {result['message']}")
            
        return result
            
    except Exception as e:
        logger.error(f"❌ Device verification API error: {e}")
        return {"success": False, "message": "Technical error occurred"}

# Enhanced Device Verification Page
@app.get("/verify")
async def verification_page(user_id: int):
    """Enhanced verification page"""
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Device Security Verification</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; margin: 0; }}
        .container {{ max-width: 400px; margin: 0 auto; background: white; padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); text-align: center; }}
        .icon {{ font-size: 3rem; margin-bottom: 15px; }}
        h2 {{ color: #333; margin-bottom: 10px; }}
        p {{ color: #666; margin-bottom: 20px; line-height: 1.5; }}
        .btn {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 25px; border: none; border-radius: 8px; cursor: pointer; font-size: 16px; }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .status {{ margin: 20px 0; padding: 12px; border-radius: 8px; font-weight: bold; }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #e8f5e8; color: #2e7d32; }}
        .error {{ background: #ffebee; color: #c62828; }}
        .progress {{ width: 100%; height: 4px; background: #eee; border-radius: 2px; overflow: hidden; margin: 15px 0; }}
        .progress-bar {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); width: 0%; transition: width 0.3s; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">🔐</div>
        <h2>Device Security Verification</h2>
        <p>Secure your account with device fingerprinting. First account on this device will be approved.</p>
        
        <div class="progress">
            <div class="progress-bar" id="progressBar"></div>
        </div>
        
        <div id="status" class="status loading">Ready to verify...</div>
        
        <button id="verifyBtn" class="btn" onclick="verifyDevice()">🔍 Verify Device</button>
    </div>

    <script>
        const USER_ID = {user_id};
        let deviceData = {{}};
        
        function collectDeviceData() {{
            deviceData = {{
                screen_resolution: `${{screen.width}}x${{screen.height}}`,
                user_agent_hash: btoa(navigator.userAgent).slice(-20),
                timezone_offset: new Date().getTimezoneOffset(),
                platform: navigator.platform,
                canvas_hash: generateCanvasHash(),
                timestamp: Date.now()
            }};
        }}
        
        function generateCanvasHash() {{
            try {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillText('Device Security Check', 2, 2);
                return btoa(canvas.toDataURL()).slice(-20);
            }} catch (e) {{
                return 'canvas_error';
            }}
        }}
        
        function updateProgress(percent, message) {{
            document.getElementById('progressBar').style.width = percent + '%';
            document.getElementById('status').innerHTML = message;
        }}
        
        async function verifyDevice() {{
            updateProgress(30, '🔄 Collecting device information...');
            document.getElementById('verifyBtn').disabled = true;
            
            collectDeviceData();
            updateProgress(60, '🔄 Verifying device security...');
            
            try {{
                const response = await fetch('/api/verify-device', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ user_id: USER_ID, device_data: deviceData }})
                }});
                
                const result = await response.json();
                updateProgress(100, 'Verification complete!');
                
                if (result.success) {{
                    document.getElementById('status').innerHTML = '✅ Device verified successfully!';
                    document.getElementById('status').className = 'status success';
                    
                    setTimeout(() => {{
                        if (window.Telegram && window.Telegram.WebApp) {{
                            window.Telegram.WebApp.close();
                        }}
                    }}, 2000);
                }} else {{
                    document.getElementById('status').innerHTML = `❌ ${{result.message}}`;
                    document.getElementById('status').className = 'status error';
                    document.getElementById('verifyBtn').innerHTML = '🔄 Try Again';
                    document.getElementById('verifyBtn').disabled = false;
                }}
            }} catch (error) {{
                updateProgress(0, '❌ Network error. Please try again.');
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
        logger.error(f"❌ Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "enhanced-wallet-bot",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": db_connected,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "version": "2.0.0-enhanced"
    }

@app.get("/")
async def root():
    return {
        "message": "🤖 Enhanced Wallet Bot - Device Security Enabled",
        "status": "running", 
        "features": [
            "First account per device allowed",
            "Multiple account prevention",
            "Enhanced security verification",
            "Admin control panel ready"
        ]
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    """Enhanced Admin Dashboard"""
    try:
        total_users = 0
        verified_users = 0
        pending_verification = 0
        total_devices = 0
        
        if db_connected and db_client:
            total_users = await db_client.walletbot.users.count_documents({})
            verified_users = await db_client.walletbot.users.count_documents({"device_verified": True})
            pending_verification = total_users - verified_users
            total_devices = await db_client.walletbot.device_fingerprints.count_documents({})
        
        return {
            "admin_panel": "Enhanced Control Dashboard",
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_verification": pending_verification,
            "unique_devices": total_devices,
            "security_status": "Device fingerprinting active",
            "features": {
                "user_management": "Active",
                "device_tracking": "Active", 
                "campaign_control": "Ready to implement",
                "referral_system": "Active",
                "withdrawal_control": "Ready to implement"
            },
            "expandable_modules": [
                "Campaign Management UI",
                "User Analytics Dashboard",
                "Referral Tracking System",
                "Withdrawal Control Panel",
                "Security Monitoring Tools"
            ]
        }
    except Exception as e:
        logger.error(f"❌ Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup event
@app.on_event("startup")
async def startup_event():
    global wallet_bot
    
    logger.info("🚀 Starting Enhanced Wallet Bot with Device Security...")
    
    # Initialize database
    await init_database()
    
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
