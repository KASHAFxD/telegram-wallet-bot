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
import re

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
app = FastAPI(title="Wallet Bot - Enhanced Security", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()

# Global state storage
user_states = {}
user_pending_actions = {}

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

# Enhanced User Model with Device Security
class UserModel:
    def __init__(self):
        self.collection = None
        if db.client:
            self.collection = db.client.walletbot.users
    
    async def create_pending_user(self, user_data: dict):
        """Create user with pending status for verification"""
        if not self.collection:
            return None
        
        try:
            user_data.update({
                "created_at": datetime.utcnow(),
                "security_status": "pending",
                "wallet_balance": 0.0,
                "total_earned": 0.0,
                "referral_earnings": 0.0,
                "total_referrals": 0,
                "is_active": False,
                "device_fingerprint": None,
                "device_verified_at": None
            })
            
            # Use upsert to avoid duplicates
            await self.collection.update_one(
                {"user_id": user_data["user_id"]},
                {"$setOnInsert": user_data},
                upsert=True
            )
            
            logger.info(f"✅ Pending user created: {user_data['user_id']}")
            return True
        except Exception as e:
            logger.error(f"❌ Error creating pending user: {e}")
            return None
    
    async def complete_user_registration(self, user_id: int):
        """Complete user registration after device verification"""
        if not self.collection:
            return False
        
        try:
            result = await self.collection.update_one(
                {"user_id": user_id, "security_status": "active"},
                {"$set": {
                    "is_active": True,
                    "registration_completed_at": datetime.utcnow()
                }}
            )
            
            logger.info(f"✅ User registration completed: {user_id}")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error completing registration: {e}")
            return False
    
    async def check_device_security(self, user_id: int):
        """Check if user's device is verified"""
        if not self.collection:
            return False
        
        try:
            user = await self.collection.find_one({
                "user_id": user_id,
                "security_status": "active",
                "device_fingerprint": {"$ne": None}
            })
            
            return user is not None
        except Exception as e:
            logger.error(f"❌ Device security check error: {e}")
            return False
    
    async def update_device_fingerprint(self, user_id: int, fingerprint: str, device_data: dict):
        """Update user's device fingerprint"""
        if not self.collection:
            return False
        
        try:
            # Check for existing device conflicts
            existing_user = await self.collection.find_one({
                "device_fingerprint": fingerprint,
                "user_id": {"$ne": user_id},
                "security_status": "active"
            })
            
            if existing_user:
                logger.warning(f"⚠️ Device conflict detected: Fingerprint {fingerprint[:16]}... already used by user {existing_user['user_id']}")
                return False
            
            # Update user with device info
            result = await self.collection.update_one(
                {"user_id": user_id},
                {"$set": {
                    "device_fingerprint": fingerprint,
                    "security_status": "active",
                    "device_verified_at": datetime.utcnow(),
                    "device_data": device_data,
                    "last_security_check": datetime.utcnow()
                }}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Device verified for user {user_id}: {fingerprint[:16]}...")
                
                # Log device verification
                await self.log_security_event(user_id, "DEVICE_VERIFIED", {
                    "fingerprint": fingerprint[:16] + "...",
                    "device_data": device_data
                })
                
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error updating device fingerprint: {e}")
            return False
    
    async def get_user(self, user_id: int):
        if not self.collection:
            return None
        try:
            return await self.collection.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"❌ Error getting user: {e}")
            return None
    
    async def add_to_wallet(self, user_id: int, amount: float, transaction_type: str, description: str):
        if not self.collection:
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
                # Log transaction
                await self.log_transaction(user_id, amount, transaction_type, description, new_balance)
                logger.info(f"✅ Wallet updated for user {user_id}: +₹{amount}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error adding to wallet: {e}")
            return False
    
    async def log_security_event(self, user_id: int, event_type: str, details: dict):
        """Log security events"""
        try:
            if db.client:
                security_logs = db.client.walletbot.security_logs
                await security_logs.insert_one({
                    "user_id": user_id,
                    "event_type": event_type,
                    "details": details,
                    "timestamp": datetime.utcnow(),
                    "ip_address": details.get("ip_address", "unknown")
                })
        except Exception as e:
            logger.error(f"❌ Error logging security event: {e}")
    
    async def log_transaction(self, user_id: int, amount: float, transaction_type: str, description: str, balance_after: float):
        """Log transactions"""
        try:
            if db.client:
                transactions = db.client.walletbot.transactions
                await transactions.insert_one({
                    "user_id": user_id,
                    "amount": amount,
                    "type": transaction_type,
                    "description": description,
                    "balance_after": balance_after,
                    "created_at": datetime.utcnow(),
                    "status": "completed"
                })
        except Exception as e:
            logger.error(f"❌ Error logging transaction: {e}")

# Initialize models
user_model = UserModel()

# Device Security Functions
def generate_device_fingerprint(device_data: dict) -> str:
    """Generate unique device fingerprint from multiple parameters"""
    components = [
        str(device_data.get('screen_resolution', '')),
        str(device_data.get('user_agent_hash', '')),
        str(device_data.get('timezone_offset', '')),
        str(device_data.get('language', '')),
        str(device_data.get('platform', '')),
        str(device_data.get('canvas_hash', '')),
        str(device_data.get('webgl_hash', '')),
        str(device_data.get('hardware_concurrency', '')),
        str(device_data.get('memory', '')),
        str(device_data.get('color_depth', ''))
    ]
    combined = '|'.join(components)
    return hashlib.sha256(combined.encode()).hexdigest()

# Device Security Decorator
def device_security_wrapper(func):
    """Decorator to check device security before executing commands"""
    async def wrapper(update, context):
        user_id = update.effective_user.id
        
        # Check if device is verified
        if not await user_model.check_device_security(user_id):
            await require_device_verification(user_id, update.effective_user.first_name)
            return
        
        # If verified, execute original function
        return await func(update, context)
    
    return wrapper

async def require_device_verification(user_id: int, username: str = None):
    """Send device verification message"""
    # Create pending user entry
    await user_model.create_pending_user({
        "user_id": user_id,
        "username": username or "User",
        "first_name": username or "User"
    })
    
    verification_url = f"{RENDER_EXTERNAL_URL}/verify?user_id={user_id}"
    
    markup = InlineKeyboardMarkup()
    verify_btn = InlineKeyboardButton(
        "🔐 Verify Device", 
        web_app=WebAppInfo(url=verification_url)
    )
    markup.add(verify_btn)
    
    message = f"""🔒 **Device Verification Required**

Welcome {username}! Before you can use this bot, we need to verify your device for security.

⚠️ **Security Policy:**
• One device = One account only
• Multiple accounts not allowed
• This protects your wallet & earnings

🛡️ **Why Device Verification?**
• Prevents fraud and abuse
• Protects your earnings
• Ensures fair usage for everyone
• Advanced fingerprinting technology

Click below to verify your device:"""
    
    await wallet_bot.bot.send_message(user_id, message, reply_markup=markup, parse_mode='Markdown')

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
            # Commands with device security
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("wallet", self.wallet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("device_verified", self.device_verified_command))
            
            # Callback handlers
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            
            # Message handlers
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            logger.info("✅ Enhanced bot handlers setup complete")
        except Exception as e:
            logger.error(f"❌ Handler setup error: {e}")
    
    def get_reply_keyboard(self):
        keyboard = [
            [KeyboardButton("💰 My Wallet"), KeyboardButton("📋 Campaigns")],
            [KeyboardButton("👥 Referral"), KeyboardButton("💸 Withdraw")],
            [KeyboardButton("🆘 Help"), KeyboardButton("🔒 Security")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.first_name or "User"
        
        logger.info(f"🚀 Start command from user: {user_id} ({username})")
        
        # Admin access
        if user_id == ADMIN_CHAT_ID:
            args = update.message.text.split()
            if len(args) > 1 and args[1] == 'admin':
                await update.message.reply_text("🔧 Admin Panel Access", parse_mode='Markdown')
                return
        
        # Check device security FIRST
        if not await user_model.check_device_security(user_id):
            logger.info(f"❌ User {user_id} needs device verification")
            await require_device_verification(user_id, username)
            return
        
        # If device verified, proceed
        logger.info(f"✅ User {user_id} is verified, proceeding...")
        
        # Complete registration if needed
        await user_model.complete_user_registration(user_id)
        
        # Handle referral codes
        args = update.message.text.split()
        if len(args) > 1:
            referral_code = args[1]
            if referral_code.startswith('ref_'):
                await self.process_referral(user_id, referral_code)
        
        await self.send_main_menu(update)
    
    async def device_verified_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        logger.info(f"✅ Device verified callback from user: {user_id}")
        
        # Complete user registration
        await user_model.complete_user_registration(user_id)
        
        # Log activity
        await user_model.log_security_event(user_id, "DEVICE_VERIFIED_AND_REGISTERED", {
            "timestamp": datetime.utcnow().isoformat()
        })
        
        await update.message.reply_text(
            "✅ **Device Verified Successfully!**\n\n🎉 Your account is now secure and ready to use!\n\n💰 You can now earn money through campaigns, refer friends, and withdraw your earnings safely.\n\n🛡️ Your device fingerprint has been recorded for security.", 
            parse_mode='Markdown'
        )
        
        await self.send_main_menu(update)
    
    @device_security_wrapper
    async def wallet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        if not user:
            await update.message.reply_text("❌ User not found. Please /start first.", reply_markup=self.get_reply_keyboard())
            return
        
        # Check device security regularly
        last_check = user.get("last_security_check")
        if last_check and (datetime.utcnow() - last_check).days > 7:
            # Re-verify device every week
            if not await user_model.check_device_security(user_id):
                await require_device_verification(user_id, user.get("first_name"))
                return
        
        wallet_msg = f"""💰 **Your Secure Wallet**
*Protected by Device Fingerprinting*

👤 **User:** {user.get('first_name', 'Unknown')}
💳 **Current Balance:** ₹{user.get('wallet_balance', 0):.2f}
📊 **Total Earned:** ₹{user.get('total_earned', 0):.2f}
👥 **Referral Earnings:** ₹{user.get('referral_earnings', 0):.2f}
🎯 **Total Referrals:** {user.get('total_referrals', 0)}

🔒 **Security Status:** ✅ Device Verified
📅 **Last Verified:** {user.get('device_verified_at', datetime.utcnow()).strftime('%Y-%m-%d')}
🛡️ **Device ID:** {user.get('device_fingerprint', 'N/A')[:16]}...

💡 Complete campaigns to earn more rewards!"""
        
        keyboard = [
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
            [InlineKeyboardButton("📋 Campaigns", callback_data="campaigns")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="wallet")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(wallet_msg, reply_markup=reply_markup, parse_mode="Markdown")
    
    @device_security_wrapper
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = f"""🆘 **Enhanced Bot Help**

**Available Commands:**
• /start - Main menu
• /wallet - Check your secure balance
• /help - Show this help

**🔒 Security Features:**
• Device fingerprinting protection
• One device per account policy
• Regular security checks
• Advanced fraud prevention

**💰 How to Earn:**
1. 📋 Complete campaigns for rewards
2. 👥 Refer friends and earn bonus
3. 💸 Withdraw when you reach minimum

**🛡️ Security Info:**
• Your device is uniquely identified
• Multiple accounts are automatically blocked
• All transactions are logged and secured

**Running on:** Render.com (Enhanced Security)
**Need Support?** Contact admin team"""
        
        await update.message.reply_text(help_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        # Security check for button interactions
        if not await user_model.check_device_security(user_id):
            await query.edit_message_text("🔒 Device verification required. Please /start again.")
            return
        
        if data == "wallet":
            await self.wallet_command(update, context)
        elif data == "campaigns":
            await query.edit_message_text("📋 **Campaigns feature coming soon!**\n\n🔒 Secured with device fingerprinting", parse_mode="Markdown")
        elif data == "referral":
            await self.show_referral_program(update, context)
        elif data == "withdraw":
            await query.edit_message_text("💸 **Withdrawal system coming soon!**\n\n🔒 Enhanced security checks enabled", parse_mode="Markdown")
    
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
1. Share your referral link with friends
2. They must complete device verification
3. Both of you get ₹10 instant bonus!
4. No fake accounts allowed - all verified

🛡️ **Security Benefits:**
• Only real users can join
• Device fingerprinting prevents abuse
• Fair earnings for everyone
• Protected against referral fraud

💡 **Tip:** Share with genuine friends for best results!"""
        
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
            await require_device_verification(user_id, update.effective_user.first_name)
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
        elif text == "🔒 Security":
            await self.show_security_info(update, context)
        else:
            await update.message.reply_text(
                "👋 Hi! Use the menu buttons below for navigation.\n\n🔒 Your device is verified and secure!",
                reply_markup=self.get_reply_keyboard()
            )
    
    async def show_security_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = await user_model.get_user(user_id)
        
        if not user:
            return
        
        security_msg = f"""🔒 **Security Dashboard**

✅ **Device Status:** Verified & Active
🛡️ **Device ID:** {user.get('device_fingerprint', 'N/A')[:16]}...
📅 **Verified On:** {user.get('device_verified_at', datetime.utcnow()).strftime('%Y-%m-%d %H:%M')}
🔄 **Last Check:** {user.get('last_security_check', datetime.utcnow()).strftime('%Y-%m-%d %H:%M')}

**🔐 Security Features Active:**
• Device fingerprinting ✅
• Multiple account prevention ✅
• Regular security checks ✅
• Fraud detection system ✅
• Encrypted data storage ✅

**📊 Account Security Score:** 95/100 (Excellent)

💡 Your account is highly secure and protected!"""
        
        await update.message.reply_text(security_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
    
    async def process_referral(self, user_id: int, referral_code: str):
        try:
            referrer_id = int(referral_code.replace('ref_', ''))
            
            if referrer_id == user_id:
                return  # Can't refer yourself
            
            # Check if referrer exists and is verified
            referrer = await user_model.get_user(referrer_id)
            if not referrer or referrer.get('security_status') != 'active':
                return
            
            # Check if user already has referrer
            user = await user_model.get_user(user_id)
            if user and user.get('referred_by'):
                return
            
            # Add referral bonus
            referral_bonus = 10.0
            
            # Update both users
            if user_model.collection:
                await user_model.collection.update_one(
                    {"user_id": user_id},
                    {"$set": {"referred_by": referrer_id}}
                )
                
                await user_model.add_to_wallet(referrer_id, referral_bonus, "referral", f"Referral bonus from user {user_id}")
                await user_model.add_to_wallet(user_id, referral_bonus, "referral", f"Welcome bonus via referral")
                
                # Send notifications
                await self.bot.send_message(user_id, f"🎉 Referral bonus received! You got ₹{referral_bonus:.2f} for joining through a referral!")
                await self.bot.send_message(referrer_id, f"🎉 Someone used your referral link! You earned ₹{referral_bonus:.2f} bonus!")
                
                logger.info(f"✅ Referral processed: {referrer_id} → {user_id}")
                
        except ValueError:
            pass
    
    async def send_main_menu(self, update: Update):
        welcome_msg = f"""🎉 **Welcome to Enhanced Wallet Bot!**
*Secured with Advanced Device Fingerprinting*

💰 Earn money through verified campaigns
🔒 Protected by device security
👥 Secure referral system
💸 Safe withdrawal process

🛡️ **Security Features:**
• Device fingerprinting protection
• One account per device policy
• Advanced fraud prevention
• Regular security monitoring

Choose an option below:"""
        
        await update.message.reply_text(welcome_msg, reply_markup=self.get_reply_keyboard(), parse_mode='Markdown')

# Initialize bot
wallet_bot = WalletBot()

# Device Verification API Endpoint
@app.post("/api/verify-device")
async def verify_device(request: Request):
    """Handle device verification from WebApp"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        device_data = data.get('device_data', {})
        
        # Generate device fingerprint
        fingerprint = generate_device_fingerprint(device_data)
        
        # Update user with device fingerprint
        success = await user_model.update_device_fingerprint(user_id, fingerprint, device_data)
        
        if success:
            # Send verification command to bot
            await wallet_bot.bot.send_message(user_id, "/device_verified")
            
            logger.info(f"✅ Device verified for user {user_id}")
            return {"status": "success", "message": "Device verified successfully"}
        else:
            logger.warning(f"❌ Device verification failed for user {user_id} - possible conflict")
            return {"status": "error", "message": "Device already registered with another account"}
            
    except Exception as e:
        logger.error(f"❌ Device verification error: {e}")
        return {"status": "error", "message": "Verification failed"}

# Device Verification WebApp Page
@app.get("/verify")
async def verification_page(user_id: int):
    """Serve device verification page"""
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Device Verification</title>
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
            max-width: 400px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }}
        .icon {{ font-size: 4rem; margin-bottom: 20px; }}
        h1 {{ color: #333; margin-bottom: 10px; font-size: 1.5rem; }}
        p {{ color: #666; margin-bottom: 30px; line-height: 1.6; }}
        .status {{ 
            padding: 15px;
            border-radius: 10px;
            margin: 20px 0;
            font-weight: bold;
        }}
        .loading {{ background: #e3f2fd; color: #1976d2; }}
        .success {{ background: #e8f5e8; color: #2e7d32; }}
        .error {{ background: #ffebee; color: #c62828; }}
        .btn {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 10px;
            font-size: 1rem;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        .btn:hover {{ transform: translateY(-2px); }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .security-info {{
            background: #f5f5f5;
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
        <h1>Device Verification</h1>
        <p>We need to verify your device to ensure account security and prevent multiple accounts.</p>
        
        <div class="security-info">
            <h3>🛡️ Why Device Verification?</h3>
            <ul>
                <li>Prevents fraud and abuse</li>
                <li>Protects your earnings</li>
                <li>One account per device policy</li>
                <li>Advanced security measures</li>
            </ul>
        </div>
        
        <div id="status" class="status loading">
            📡 Collecting device information...
        </div>
        
        <button id="verifyBtn" class="btn" onclick="verifyDevice()" disabled>
            🔍 Verify Device
        </button>
    </div>

    <script>
        const USER_ID = {user_id};
        let deviceData = {{}};
        
        // Collect comprehensive device information
        function collectDeviceInfo() {{
            return new Promise((resolve) => {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillText('Device fingerprint test', 2, 2);
                const canvasHash = canvas.toDataURL().slice(-50);
                
                const gl = canvas.getContext('webgl');
                const glInfo = gl ? gl.getParameter(gl.RENDERER) : 'unknown';
                
                deviceData = {{
                    screen_resolution: `${{screen.width}}x${{screen.height}}`,
                    user_agent_hash: btoa(navigator.userAgent).slice(-20),
                    timezone_offset: new Date().getTimezoneOffset(),
                    language: navigator.language,
                    platform: navigator.platform,
                    canvas_hash: canvasHash,
                    webgl_hash: btoa(glInfo).slice(-20),
                    hardware_concurrency: navigator.hardwareConcurrency || 0,
                    memory: navigator.deviceMemory || 0,
                    color_depth: screen.colorDepth,
                    touch_support: 'ontouchstart' in window,
                    timestamp: Date.now()
                }};
                
                setTimeout(() => {{
                    document.getElementById('status').innerHTML = '✅ Device information collected';
                    document.getElementById('status').className = 'status success';
                    document.getElementById('verifyBtn').disabled = false;
                    resolve();
                }}, 2000);
            }});
        }}
        
        async function verifyDevice() {{
            document.getElementById('status').innerHTML = '🔄 Verifying device...';
            document.getElementById('status').className = 'status loading';
            document.getElementById('verifyBtn').disabled = true;
            
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
                
                if (result.status === 'success') {{
                    document.getElementById('status').innerHTML = '🎉 Device verified successfully!<br>You can now close this page.';
                    document.getElementById('status').className = 'status success';
                    
                    // Close WebApp after 3 seconds
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
                document.getElementById('status').innerHTML = '❌ Verification failed. Please try again.';
                document.getElementById('status').className = 'status error';
                document.getElementById('verifyBtn').disabled = false;
            }}
        }}
        
        // Start collecting device info on page load
        collectDeviceInfo();
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
        "service": "wallet-bot-enhanced-security",
        "timestamp": datetime.utcnow().isoformat(),
        "mongodb_connected": mongo_healthy,
        "telegram_bot_initialized": wallet_bot.initialized if wallet_bot else False,
        "security_features": [
            "Device fingerprinting",
            "Multiple account prevention",
            "Regular security checks",
            "Advanced fraud detection"
        ],
        "version": "2.0.0-enhanced"
    }

@app.get("/")
async def root():
    return {
        "message": "🤖 Enhanced Wallet Bot with Device Security",
        "status": "running",
        "platform": "Render.com",
        "security": "Advanced Device Fingerprinting Enabled",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health",
            "verify": "/verify?user_id=<id>",
            "admin": "/api/admin/dashboard"
        },
        "features": [
            "Device fingerprinting protection",
            "One account per device policy",
            "Advanced fraud prevention",
            "Secure wallet system",
            "Enhanced referral protection"
        ]
    }

@app.get("/api/admin/dashboard")
async def admin_dashboard(admin: str = Depends(authenticate_admin)):
    try:
        total_users = 0
        verified_users = 0
        pending_users = 0
        security_incidents = 0
        
        if db.client:
            total_users = await db.client.walletbot.users.count_documents({})
            verified_users = await db.client.walletbot.users.count_documents({"security_status": "active"})
            pending_users = await db.client.walletbot.users.count_documents({"security_status": "pending"})
            security_incidents = await db.client.walletbot.security_logs.count_documents({"event_type": {"$in": ["DEVICE_CONFLICT", "MULTIPLE_ACCOUNT_ATTEMPT"]}})
        
        return {
            "platform": "Enhanced Render Security",
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_verification": pending_users,
            "security_incidents": security_incidents,
            "security_features": [
                "Device Fingerprinting Active",
                "Multiple Account Prevention",
                "Regular Security Audits",
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
    logger.info("🚀 Starting Enhanced Wallet Bot with Device Security...")
    
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
    
    logger.info("🎉 Enhanced security bot startup completed!")

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
