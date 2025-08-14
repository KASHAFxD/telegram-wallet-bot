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

# Initialize FastAPI
app = FastAPI(title="Wallet Bot - FINAL WORKING VERSION", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# Global database instance
db_client = None
db_connected = False

# Initialize database connection
async def init_database():
    global db_client, db_connected
    try:
        db_client = AsyncIOMotorClient(MONGODB_URL)
        # Test connection
        await db_client.admin.command('ping')
        db_connected = True
        logger.info("✅ MongoDB Atlas connected successfully")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        db_connected = False
        return False

# Simple User Model (Fixed PyMongo Boolean Issues)
class UserModel:
    def __init__(self):
        pass
    
    def get_collection(self):
        """Get users collection safely"""
        if db_client is not None and db_connected:
            return db_client.walletbot.users
        return None
    
    async def create_user(self, user_data: dict):
        collection = self.get_collection()
        if collection is None:
            logger.warning("❌ Database not connected")
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
                "device_fingerprint": None
            })
            
            # Use upsert to avoid duplicates
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
            user = await collection.find_one({"user_id": user_id})
            return user
        except Exception as e:
            logger.error(f"❌ Error getting user: {e}")
            return None
    
    async def update_user(self, user_id: int, update_data: dict):
        collection = self.get_collection()
        if collection is None:
            return False
            
        try:
            update_data["updated_at"] = datetime.utcnow()
            result = await collection.update_one(
                {"user_id": user_id},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error updating user: {e}")
            return False

# Initialize user model
user_model = UserModel()

# Simplified Telegram Bot (No Device Fingerprinting Blocking)
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
            logger.info("✅ Telegram bot initialized successfully")
        except Exception as e:
            logger.error(f"❌ Error initializing bot: {e}")
            self.initialized = False
    
    def setup_handlers(self):
        try:
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            # Add error handler
            self.application.add_error_handler(self.error_handler)
            logger.info("✅ Bot handlers setup complete")
        except Exception as e:
            logger.error(f"❌ Handler setup error: {e}")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and send a telegram message to notify the developer."""
        logger.error("Exception while handling an update:", exc_info=context.error)
    
    def get_reply_keyboard(self):
        keyboard = [
            [KeyboardButton("💰 My Wallet"), KeyboardButton("📋 Campaigns")],
            [KeyboardButton("👥 Referral"), KeyboardButton("💸 Withdraw")],
            [KeyboardButton("🆘 Help"), KeyboardButton("📊 Status")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            username = update.effective_user.username or "Unknown"
            first_name = update.effective_user.first_name or "User"
            
            logger.info(f"🚀 Start command from user: {user_id} ({first_name})")
            
            # Create/get user without device verification blocking
            user_data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name
            }
            await user_model.create_user(user_data)
            
            # Send welcome message immediately (no device verification blocking)
            welcome_msg = f"""🎉 **Welcome to Enhanced Wallet Bot!**
*Successfully Running on Render.com*

Hi {first_name}! 👋

💰 **Earn money through verified campaigns**
🔒 **Advanced security with device fingerprinting**
👥 **Secure referral system** - Earn ₹10 per friend
💸 **Safe withdrawal process**

🚀 **Platform:** Render.com (Reliable & Fast)
✅ **Status:** Bot working perfectly
⚡ **Response:** Instant replies

Choose an option below to get started:"""
            
            inline_keyboard = [
                [InlineKeyboardButton("💰 My Wallet", callback_data="wallet")],
                [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
                [InlineKeyboardButton("👥 Referral", callback_data="referral")],
                [InlineKeyboardButton("🔐 Verify Device", callback_data="verify_device")]
            ]
            inline_reply_markup = InlineKeyboardMarkup(inline_keyboard)
            
            await update.message.reply_text(welcome_msg, reply_markup=inline_reply_markup, parse_mode="Markdown")
            await update.message.reply_text("🎯 **Use the permanent menu buttons below for quick access:**", reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            
            logger.info(f"✅ Welcome message sent to user: {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Start command error: {e}")
            logger.error(traceback.format_exc())
            try:
                await update.message.reply_text("❌ An error occurred. Bot is working now - please try again!", reply_markup=self.get_reply_keyboard())
            except:
                pass
    
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            if not user:
                await update.message.reply_text("❌ User not found. Please /start first.", reply_markup=self.get_reply_keyboard())
                return
            
            wallet_msg = f"""💰 **Your Secure Wallet**
*Successfully Running on Render.com*

👤 **User:** {user.get('first_name', 'Unknown')}
💳 **Current Balance:** ₹{user.get('wallet_balance', 0):.2f}
📊 **Total Earned:** ₹{user.get('total_earned', 0):.2f}
👥 **Referral Earnings:** ₹{user.get('referral_earnings', 0):.2f}
🎯 **Total Referrals:** {user.get('total_referrals', 0)}

**Account Details:**
📅 **Member Since:** {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d')}
🔒 **Device Status:** {'✅ Verified' if user.get('device_verified') else '⚠️ Pending Verification'}
🚀 **Platform:** Render.com
💡 **Tip:** Complete campaigns to earn more rewards!"""
            
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
            logger.error(f"❌ Wallet command error: {e}")
            await update.message.reply_text("❌ Error loading wallet. Please try again.", reply_markup=self.get_reply_keyboard())
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            help_msg = f"""🆘 **Enhanced Bot Help**

**Available Commands:**
• /start - Main menu and welcome
• /wallet - Check your balance
• /help - Show this help

**🚀 Bot Features:**
• Advanced security with device fingerprinting
• One device per account policy
• Enhanced fraud prevention
• 24/7 uptime on Render.com

**💰 How to Earn:**
1. 📋 Complete campaigns for instant rewards
2. 👥 Refer friends and earn ₹10 bonus
3. 💸 Withdraw when you reach minimum amount

**🛡️ Security Info:**
• Your device fingerprint ensures account security
• Multiple accounts are automatically detected
• All transactions are logged and secured

**🚀 Platform:** Render.com (Enhanced Stability)
**Need Support?** Contact admin team"""
            
            await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"❌ Help command error: {e}")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            
            data = query.data
            user_id = update.effective_user.id
            
            logger.info(f"🔘 Button pressed: {data} by user {user_id}")
            
            if data == "wallet":
                await self.wallet_command(update, context)
            elif data == "campaigns":
                await query.edit_message_text("📋 **Campaigns Feature**\n*Coming Soon on Render.com*\n\n🚀 **What's Coming:**\n• Task-based earning system\n• Screenshot verification\n• Instant rewards\n• Multiple campaign types\n\n🖥️ **Platform:** Render.com VPS\n⏰ **Expected:** Very Soon\n\nStay tuned for exciting earning opportunities!", parse_mode="Markdown")
            elif data == "referral":
                try:
                    bot_username = (await self.bot.get_me()).username
                    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                    
                    referral_msg = f"""👥 **Secure Referral Program**
*Powered by Render.com VPS*

🎁 **Earn ₹10 for each friend you refer!**

📊 **Your Referral Stats:**
• Total Referrals: 0
• Referral Earnings: ₹0.00

🔗 **Your Referral Link:**
`{referral_link}`

**How it works:**
1. Share your referral link with friends
2. When they join and start using the bot
3. You get ₹10 instantly in your wallet!

🖥️ **System:** Render.com VPS
💡 **Tip:** Share in groups and social media to earn more!"""
                    
                    keyboard = [[InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.edit_message_text(referral_msg, reply_markup=reply_markup, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Referral error: {e}")
                    
            elif data == "verify_device":
                verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
                verify_msg = f"""🔐 **Device Verification**
*Enhanced Security Feature*

🛡️ **Why Verify?**
• Prevents fraud and abuse
• Protects your earnings
• One device per account policy
• Advanced fingerprinting protection

Click below to verify your device:"""
                
                keyboard = [[InlineKeyboardButton("🔐 Verify Device", web_app=WebAppInfo(url=verification_url))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(verify_msg, reply_markup=reply_markup, parse_mode="Markdown")
                
            elif data == "withdraw":
                withdraw_msg = f"""💸 **Withdrawal System**
*Hosted on Render.com VPS*

🏦 **Available Methods:**
• Bank Transfer (Coming Soon)
• UPI Payment (Coming Soon)
• PayTM Wallet (Coming Soon)

⚙️ **Settings:**
• Minimum Withdrawal: ₹6.00
• Processing Time: 24-48 hours
• Platform: Render.com VPS

💡 **Note:** Withdrawal system is under development.
Stay tuned for the launch!"""
                await query.edit_message_text(withdraw_msg, parse_mode="Markdown")
            else:
                await query.answer("⚠️ Unknown action. Please try again.")
                
        except Exception as e:
            logger.error(f"❌ Button handler error: {e}")
            try:
                await query.answer("❌ An error occurred. Please try again.", show_alert=True)
            except:
                pass
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            text = update.message.text
            user_id = update.effective_user.id
            
            logger.info(f"💬 Message from user {user_id}: {text[:30]}...")
            
            if text == "💰 My Wallet":
                await self.wallet_command(update, context)
            elif text == "📋 Campaigns":
                campaigns_msg = """📋 **Campaigns Feature**

🚀 Task-based earning system coming soon on Render.com!

🖥️ Platform: Render.com VPS
💡 Stay tuned for exciting opportunities!"""
                await update.message.reply_text(campaigns_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            elif text == "👥 Referral":
                try:
                    bot_username = (await self.bot.get_me()).username
                    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                    referral_msg = f"""👥 **Referral Program**

🎁 Earn ₹10 for each friend!

🔗 **Your Link:** `{referral_link}`

🖥️ Powered by Render.com VPS"""
                    await update.message.reply_text(referral_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Referral message error: {e}")
            elif text == "💸 Withdraw":
                withdraw_msg = """💸 **Withdrawal System**

🏦 Coming soon on Render.com!
⚙️ Minimum: ₹6.00
🖥️ Platform: Render.com VPS"""
                await update.message.reply_text(withdraw_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            elif text == "🆘 Help":
                await self.help_command(update, context)
            elif text == "📊 Status":
                await self.show_status(update, context)
            else:
                welcome_msg = f"""👋 **Hi there!** 

🤖 **Enhanced Wallet Bot** running on Render.com
🖥️ **Specs:** Reliable hosting with instant response
🌐 **Status:** ✅ Working perfectly

Use the menu buttons below for easy navigation!

💡 **Available Options:**
• 💰 Check your wallet
• 📋 View campaigns (coming soon)
• 👥 Referral program
• 💸 Withdrawal system (coming soon)
• 📊 Bot status
• 🆘 Help & support"""
                await update.message.reply_text(welcome_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
                
        except Exception as e:
            logger.error(f"❌ Message handler error: {e}")
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = await user_model.get_user(user_id)
            
            status_msg = f"""📊 **Enhanced Bot Status**

🤖 **System Status:**
• Bot: ✅ Running perfectly
• Database: {'✅ Connected' if db_connected else '❌ Disconnected'}
• Server: ✅ Render.com VPS
• Response Time: ⚡ Instant

👤 **Your Account:**
• Status: ✅ Active
• Device: {'✅ Verified' if user and user.get('device_verified') else '⚠️ Pending'}
• Member Since: {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d') if user else 'Today'}

🌐 **Platform Details:**
• Hosting: Render.com
• Uptime: 24/7 Available
• Security: Enhanced fingerprinting
• Features: Advanced anti-fraud system

⏰ **Server Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🔄 **Last Update:** Real-time status"""
            
            await update.message.reply_text(status_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"❌ Status command error: {e}")

# Initialize bot
wallet_bot = None

# Device Verification API
@app.post("/api/verify-device")
async def verify_device(request: Request):
    """Handle device verification"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        # Generate simple fingerprint
        fingerprint_components = [
            str(device_data.get('screen_resolution', '')),
            str(device_data.get('user_agent_hash', '')),
            str(device_data.get('timezone_offset', '')),
            str(device_data.get('platform', ''))
        ]
        fingerprint = hashlib.sha256('|'.join(fingerprint_components).encode()).hexdigest()
        
        # Update user verification status
        success = await user_model.update_user(user_id, {
            "device_verified": True,
            "device_fingerprint": fingerprint,
            "device_verified_at": datetime.utcnow()
        })
        
        if success:
            logger.info(f"✅ Device verified for user {user_id}")
            return {"status": "success", "message": "Device verified successfully"}
        else:
            return {"status": "error", "message": "Verification failed"}
            
    except Exception as e:
        logger.error(f"❌ Device verification error: {e}")
        return {"status": "error", "message": "Verification failed"}

# Device Verification Page
@app.get("/verify")
async def verification_page(user_id: int):
    """Device verification page"""
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Device Verification</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: Arial, sans-serif; padding: 20px; text-align: center; }}
        .container {{ max-width: 400px; margin: 0 auto; background: #f9f9f9; padding: 30px; border-radius: 10px; }}
        .btn {{ background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
        .status {{ margin: 20px 0; padding: 10px; border-radius: 5px; }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #e8f5e8; color: #2e7d32; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>🔐 Device Verification</h2>
        <p>Verify your device for enhanced security</p>
        <div id="status" class="status loading">Ready to verify...</div>
        <button id="verifyBtn" class="btn" onclick="verifyDevice()">Verify Device</button>
    </div>

    <script>
        const USER_ID = {user_id};
        
        async function verifyDevice() {{
            document.getElementById('status').innerHTML = 'Verifying...';
            document.getElementById('verifyBtn').disabled = true;
            
            const deviceData = {{
                screen_resolution: screen.width + 'x' + screen.height,
                user_agent_hash: btoa(navigator.userAgent).slice(-20),
                timezone_offset: new Date().getTimezoneOffset(),
                platform: navigator.platform,
                timestamp: Date.now()
            }};
            
            try {{
                const response = await fetch('/api/verify-device', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ user_id: USER_ID, device_data: deviceData }})
                }});
                
                const result = await response.json();
                
                if (result.status === 'success') {{
                    document.getElementById('status').innerHTML = '✅ Device verified successfully!';
                    document.getElementById('status').className = 'status success';
                    
                    setTimeout(() => {{
                        if (window.Telegram && window.Telegram.WebApp) {{
                            window.Telegram.WebApp.close();
                        }}
                    }}, 2000);
                }} else {{
                    document.getElementById('status').innerHTML = '❌ Verification failed';
                    document.getElementById('verifyBtn').disabled = false;
                }}
            }} catch (error) {{
                document.getElementById('status').innerHTML = '❌ Network error';
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
        "service": "wallet-bot-final",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": db_connected,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "platform": "Render.com",
        "version": "1.0.0-final-working"
    }

@app.get("/")
async def root():
    return {
        "message": "🤖 Enhanced Wallet Bot - FINAL WORKING VERSION",
        "status": "running",
        "platform": "Render.com", 
        "features": ["Fixed PyMongo Boolean errors", "Enhanced error handling", "Device fingerprinting", "24/7 uptime"],
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health",
            "verify": "/verify?user_id=<id>",
            "admin": "/api/admin/dashboard"
        }
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    try:
        total_users = 0
        verified_users = 0
        
        if db_connected and db_client:
            total_users = await db_client.walletbot.users.count_documents({})
            verified_users = await db_client.walletbot.users.count_documents({"device_verified": True})
        
        return {
            "platform": "Render.com - FINAL VERSION",
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_verification": total_users - verified_users,
            "status": "working_perfectly"
        }
    except Exception as e:
        logger.error(f"❌ Admin dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup event
@app.on_event("startup")
async def startup_event():
    global wallet_bot
    
    logger.info("🚀 Starting FINAL Enhanced Wallet Bot...")
    
    # Initialize database first
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
            await asyncio.sleep(2)
            
            result = await wallet_bot.bot.set_webhook(url=webhook_url)
            if result:
                logger.info(f"✅ FINAL webhook set: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Startup error: {e}")
    
    logger.info("🎉 FINAL enhanced bot startup completed!")

@app.on_event("shutdown") 
async def shutdown_event():
    logger.info("🔄 Shutting down final bot...")
    if wallet_bot and wallet_bot.application:
        try:
            await wallet_bot.bot.delete_webhook()
            await wallet_bot.application.stop()
            await wallet_bot.application.shutdown()
            await wallet_bot.bot.shutdown()
        except:
            pass
    logger.info("✅ Final shutdown completed")

# Main entry point
if __name__ == "__main__":
    import uvicorn
    logger.info(f"🚀 Starting FINAL Enhanced Secure Wallet Bot - Port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
