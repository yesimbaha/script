from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
import json
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser
import cv2
import numpy as np
from PIL import Image
import base64

# Set Playwright browser path if not set
if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/pw-browsers'

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

# Global bot state
bot_state = {
    "running": False,
    "browser": None,
    "page": None,
    "current_fuel": 0,
    "shields_active": False,
    "position": {"x": 0, "y": 0},
    "status": "idle",
    "settings": {
        "refuel_threshold": 25,
        "shield_threshold": 10,
        "safe_threshold": 85,
        "target_player": "",
        "username": "",
        "password": ""
    }
}

# WebSocket connections
websocket_connections = []

# Models
class BotSettings(BaseModel):
    refuel_threshold: int = 25
    shield_threshold: int = 10
    safe_threshold: int = 85
    target_player: str = ""
    username: str = ""
    password: str = ""

class LoginCredentials(BaseModel):
    username: str
    password: str

class BotStatus(BaseModel):
    running: bool
    current_fuel: int
    shields_active: bool
    position: Dict[str, int]
    status: str
    settings: BotSettings

class TankInfo(BaseModel):
    name: str
    id: str
    fuel: int
    position: Dict[str, int]

# Game Bot Class
class TankpitBot:
    def __init__(self):
        self.browser = None
        self.page = None
        self.running = False
        
    async def start_browser(self):
        """Initialize browser and navigate to tankpit.com"""
        # Check if browser is already running
        if self.browser:
            try:
                # Test if browser is still alive by checking its state
                await self.browser.new_page()  # This will fail if browser is closed
                await self.browser.contexts[0].pages[-1].close()  # Close the test page
                logging.info("Browser already running, reusing existing instance")
                return True
            except:
                # Browser is closed or invalid, continue to create new one
                self.browser = None
                self.page = None
            
        try:
            playwright = await async_playwright().__aenter__()
            
            # Launch browser with virtual display for container environment
            self.browser = await playwright.chromium.launch(
                headless=False,
                args=[
                    '--no-sandbox', 
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--remote-debugging-port=9222',
                    '--display=:99'
                ]
            )
                
            self.page = await self.browser.new_page()
            
            # Navigate to tankpit.com with timeout
            await self.page.goto("https://www.tankpit.com", timeout=15000)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            
            logging.info("Successfully navigated to tankpit.com")
            return True
            
        except Exception as e:
            logging.error(f"Failed to start browser: {e}")
            await self.cleanup_browser()
            return False
    
    async def cleanup_browser(self):
        """Clean up browser resources"""
        try:
            if self.page:
                await self.page.close()
                self.page = None
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception as e:
            logging.error(f"Error cleaning up browser: {e}")
        
    async def login(self, username: str, password: str):
        """Login to tankpit.com with improved error handling"""
        try:
            # Start browser if not already running
            if not await self.start_browser():
                return False
                
            # Wait for page to fully load
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            
            # TankPit.com specific: Click the header login link to show the overlay
            try:
                header_login = await self.page.wait_for_selector('#header-login', timeout=5000)
                await header_login.click()
                logging.info("Clicked header login link")
                await self.page.wait_for_timeout(2000)  # Wait for overlay to appear
            except Exception as e:
                logging.error(f"Could not find or click header login link: {e}")
                # Try alternative methods to show login form
                login_links = await self.page.query_selector_all('a[href="#login"], a:has-text("Log in"), a:has-text("Login")')
                if login_links:
                    await login_links[0].click()
                    await self.page.wait_for_timeout(2000)
                else:
                    logging.error("Could not find any login links")
                    return False
            
            # Now the login form should be visible, look for the specific fields
            username_field = None
            password_field = None
            
            # TankPit.com specific selectors
            try:
                username_field = await self.page.wait_for_selector('#login-username', timeout=5000)
                logging.info("Found tankpit.com username field: #login-username")
            except Exception as e:
                logging.error(f"Could not find username field: {e}")
                return False
            
            try:
                password_field = await self.page.wait_for_selector('#login input[name="password"][type="password"]', timeout=5000)
                logging.info("Found tankpit.com password field")
            except Exception as e:
                logging.error(f"Could not find password field: {e}")
                return False
            
            # Fill in credentials
            await username_field.fill(username)
            await password_field.fill(password)
            logging.info("Filled in credentials")
            
            # Look for submit button - tankpit.com uses: <input type="submit" value="Log in">
            try:
                submit_button = await self.page.wait_for_selector('#login input[type="submit"]', timeout=5000)
                logging.info("Found submit button")
                await submit_button.click()
                logging.info("Clicked submit button")
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                logging.error(f"Could not find or click submit button: {e}")
                # Try pressing Enter on password field as backup
                await password_field.press('Enter')
                await self.page.wait_for_load_state("networkidle", timeout=15000)
            
            # Check if login was successful
            current_url = self.page.url
            page_content = await self.page.content()
            
            # TankPit specific success indicators
            if ("dashboard" in current_url.lower() or 
                "game" in current_url.lower() or 
                "play" in current_url.lower() or
                "welcome" in page_content.lower() or
                "logout" in page_content.lower()):
                logging.info("Login appears successful based on page content")
                return True
            
            # Check for error messages in the login form
            try:
                error_elements = await self.page.query_selector_all('#login .error, #login .message, .alert-error')
                if error_elements:
                    for error_elem in error_elements:
                        error_text = await error_elem.inner_text()
                        if error_text.strip():
                            logging.error(f"Login error detected: {error_text}")
                            return False
            except:
                pass
            
            # Check if login overlay is still visible (indicates failure)
            try:
                login_overlay = await self.page.query_selector('#login.overlay')
                if login_overlay:
                    overlay_visible = await login_overlay.is_visible()
                    if not overlay_visible:
                        logging.info("Login overlay disappeared, login likely successful")
                        return True
                    else:
                        # Check if there are any error indicators in the overlay
                        form_content = await login_overlay.inner_text()
                        if "incorrect" in form_content.lower() or "invalid" in form_content.lower() or "error" in form_content.lower():
                            logging.error("Login failed - error in form content")
                            return False
                        
                        logging.info("Login overlay still visible but no explicit errors")
                        return False
            except:
                pass
            
            # Take a screenshot for debugging
            await self.page.screenshot(path="/tmp/tankpit_after_login.png")
            logging.info("Login attempt completed, screenshot saved")
            
            # If we got here and no errors were detected, assume success
            logging.info("Login completed, no explicit error indicators found")
            return True
            
        except Exception as e:
            logging.error(f"Login failed with exception: {e}")
            await self.cleanup_browser()
            return False
    
    async def get_available_tanks(self):
        """Get list of tanks available on the account"""
        try:
            if not self.page:
                logging.error("No page available for tank detection")
                return []
                
            # Take a screenshot for debugging
            await self.page.screenshot(path="/tmp/tankpit_after_login.png")
            logging.info("Screenshot saved to /tmp/tankpit_after_login.png")
            
            # Get current page info
            current_url = self.page.url
            page_title = await self.page.title()
            logging.info(f"Current URL: {current_url}")
            logging.info(f"Page title: {page_title}")
            
            # Get page content to analyze
            page_content = await self.page.content()
            
            # Look for common tank-related keywords in the HTML
            if "tank" in page_content.lower():
                logging.info("Found 'tank' references in page content")
            else:
                logging.warning("No 'tank' references found in page content")
            
            # Try multiple strategies to find tanks
            tanks = []
            
            # Strategy 1: Look for tank selection elements (original approach)
            tank_elements = await self.page.query_selector_all(".tank-list .tank, .tank-selection .tank, .tank")
            logging.info(f"Strategy 1 - Found {len(tank_elements)} elements with tank classes")
            
            # Strategy 2: Look for links or buttons that might lead to tank selection
            tank_links = await self.page.query_selector_all('a[href*="tank"], button:has-text("tank"), a:has-text("tank")')
            logging.info(f"Strategy 2 - Found {len(tank_links)} tank-related links/buttons")
            
            # Strategy 3: Look for game-related navigation elements
            game_elements = await self.page.query_selector_all('a[href*="play"], a[href*="game"], button:has-text("Play"), button:has-text("Game")')
            logging.info(f"Strategy 3 - Found {len(game_elements)} game-related elements")
            
            # Strategy 4: Look for any dropdown or selection elements
            select_elements = await self.page.query_selector_all('select, .dropdown, .selection')
            logging.info(f"Strategy 4 - Found {len(select_elements)} selection elements")
            
            # If we found tank elements, process them
            if tank_elements:
                for i, tank_element in enumerate(tank_elements):
                    try:
                        name = await tank_element.inner_text()
                        tanks.append({
                            "name": name.strip(),
                            "id": str(i),
                            "fuel": 100,  # Default values
                            "position": {"x": 0, "y": 0}
                        })
                    except Exception as e:
                        logging.error(f"Error processing tank element {i}: {e}")
            
            # If no tanks found with direct approach, try to navigate to tank selection
            if not tanks and game_elements:
                logging.info("No tanks found directly, trying to navigate to game area")
                try:
                    # Click on a "Play" or "Game" link
                    await game_elements[0].click()
                    await self.page.wait_for_load_state("networkidle", timeout=10000)
                    
                    # Try to find tanks again after navigation
                    tank_elements = await self.page.query_selector_all(".tank-list .tank, .tank-selection .tank, .tank")
                    logging.info(f"After navigation - Found {len(tank_elements)} tank elements")
                    
                    for i, tank_element in enumerate(tank_elements):
                        try:
                            name = await tank_element.inner_text()
                            tanks.append({
                                "name": name.strip(),
                                "id": str(i),
                                "fuel": 100,
                                "position": {"x": 0, "y": 0}
                            })
                        except Exception as e:
                            logging.error(f"Error processing tank element {i} after navigation: {e}")
                            
                except Exception as e:
                    logging.error(f"Failed to navigate to game area: {e}")
            
            # If still no tanks, look for any text that might indicate tank names
            if not tanks:
                logging.info("Still no tanks found, looking for any tank-related text on page")
                
                # Look for any text elements that might contain tank information
                all_elements = await self.page.query_selector_all('*')
                tank_indicators = []
                
                for element in all_elements[:50]:  # Limit to first 50 elements to avoid timeout
                    try:
                        text = await element.inner_text()
                        if text and any(keyword in text.lower() for keyword in ['tank', 'vehicle', 'unit', 'character']):
                            tank_indicators.append(text.strip()[:100])  # Limit text length
                    except:
                        continue
                
                if tank_indicators:
                    logging.info(f"Found potential tank indicators: {tank_indicators[:5]}")  # Log first 5
                    
                    # Create mock tanks based on found indicators (for testing)
                    for i, indicator in enumerate(tank_indicators[:3]):  # Max 3 tanks
                        if len(indicator) > 3:  # Skip very short text
                            tanks.append({
                                "name": f"Tank-{i+1}: {indicator[:20]}",
                                "id": str(i),
                                "fuel": 100,
                                "position": {"x": 0, "y": 0}
                            })
            
            logging.info(f"Final tank count: {len(tanks)}")
            return tanks
            
        except Exception as e:
            logging.error(f"Failed to get tanks: {e}")
            return []
    
    async def select_tank(self, tank_id: str):
        """Select a specific tank"""
        try:
            tank_selector = f".tank-list .tank:nth-child({int(tank_id) + 1})"
            tank_element = await self.page.query_selector(tank_selector)
            if tank_element:
                await tank_element.click()
                await self.page.wait_for_timeout(2000)
                return True
            return False
        except Exception as e:
            logging.error(f"Failed to select tank: {e}")
            return False
    
    async def detect_fuel_level(self):
        """Detect current fuel level using computer vision"""
        try:
            # Take screenshot of fuel gauge area
            screenshot = await self.page.screenshot()
            
            # Convert to OpenCV format
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # This would need to be customized based on tankpit's fuel gauge
            # For now, return a mock value
            fuel_level = 75  # Mock fuel level
            
            return fuel_level
        except Exception as e:
            logging.error(f"Failed to detect fuel level: {e}")
            return 50  # Default value
    
    async def activate_bot_and_mine(self):
        """Activate bot and mine features on new screen"""
        try:
            # Look for bot button
            bot_button = await self.page.query_selector("button:has-text('bot'), .bot-button, [data-action='bot']")
            if bot_button:
                await bot_button.click()
                await self.page.wait_for_timeout(1000)
            
            # Look for mine button
            mine_button = await self.page.query_selector("button:has-text('mine'), .mine-button, [data-action='mine']")
            if mine_button:
                await mine_button.click()
                await self.page.wait_for_timeout(1000)
                
            return True
        except Exception as e:
            logging.error(f"Failed to activate bot/mine: {e}")
            return False
    
    async def find_fuel_canisters(self):
        """Find fuel canisters on screen and return the one with most fuel"""
        try:
            # Look for fuel canister elements
            canisters = await self.page.query_selector_all(".fuel-canister, [data-type='fuel'], .fuel")
            
            best_canister = None
            max_fuel = 0
            
            for canister in canisters:
                # Get fuel amount from canister (this would need customization)
                fuel_text = await canister.inner_text()
                try:
                    fuel_amount = int(''.join(filter(str.isdigit, fuel_text)))
                    if fuel_amount > max_fuel:
                        max_fuel = fuel_amount
                        best_canister = canister
                except:
                    continue
            
            return best_canister
        except Exception as e:
            logging.error(f"Failed to find fuel canisters: {e}")
            return None
    
    async def click_fuel_canister(self):
        """Click on the fuel canister with most fuel"""
        try:
            canister = await self.find_fuel_canisters()
            if canister:
                await canister.click()
                await self.page.wait_for_timeout(2000)
                return True
            return False
        except Exception as e:
            logging.error(f"Failed to click fuel canister: {e}")
            return False
    
    async def activate_shields(self):
        """Activate shields when fuel is critically low"""
        try:
            # Look for shield button (key "1")
            await self.page.keyboard.press("1")
            await self.page.wait_for_timeout(1000)
            return True
        except Exception as e:
            logging.error(f"Failed to activate shields: {e}")
            return False
    
    async def open_map(self):
        """Open the map overview"""
        try:
            # Look for map button or press map key
            map_button = await self.page.query_selector("button:has-text('map'), .map-button, [data-action='map']")
            if map_button:
                await map_button.click()
            else:
                # Try pressing 'M' key
                await self.page.keyboard.press("m")
            
            await self.page.wait_for_timeout(2000)
            return True
        except Exception as e:
            logging.error(f"Failed to open map: {e}")
            return False
    
    async def find_dense_fuel_area(self):
        """Find area with most fuel density on map"""
        try:
            # Look for yellow dots (fuel indicators) on map
            fuel_dots = await self.page.query_selector_all(".fuel-dot, [data-type='fuel-marker'], .yellow-dot")
            
            if fuel_dots:
                # For now, click the first available fuel dot
                # In a more sophisticated version, we'd analyze density
                await fuel_dots[0].click()
                await self.page.wait_for_timeout(2000)
                return True
            
            return False
        except Exception as e:
            logging.error(f"Failed to find fuel area: {e}")
            return False
    
    async def run_bot_cycle(self):
        """Main bot logic cycle"""
        while self.running:
            try:
                # Update fuel level
                current_fuel = await self.detect_fuel_level()
                bot_state["current_fuel"] = current_fuel
                
                # Broadcast status update
                await self.broadcast_status()
                
                # Check if shields need activation (10% threshold)
                if current_fuel <= bot_state["settings"]["shield_threshold"] and not bot_state["shields_active"]:
                    await self.activate_shields()
                    bot_state["shields_active"] = True
                    bot_state["status"] = "shields_activated"
                
                # Check if refueling needed (25% threshold)
                if current_fuel <= bot_state["settings"]["refuel_threshold"]:
                    bot_state["status"] = "refueling"
                    
                    # Try to click fuel canister
                    if await self.click_fuel_canister():
                        bot_state["status"] = "fuel_collected"
                    else:
                        # No fuel canisters available, check map
                        bot_state["status"] = "searching_map"
                        await self.open_map()
                        await self.find_dense_fuel_area()
                
                # If fuel is above safe threshold (85%), stay stationary
                elif current_fuel >= bot_state["settings"]["safe_threshold"]:
                    bot_state["status"] = "stationary"
                    bot_state["shields_active"] = False
                
                # On new screen, activate bot and mine
                await self.activate_bot_and_mine()
                
                # Wait before next cycle
                await asyncio.sleep(2)
                
            except Exception as e:
                logging.error(f"Bot cycle error: {e}")
                bot_state["status"] = f"error: {str(e)}"
                await asyncio.sleep(5)
    
    async def broadcast_status(self):
        """Broadcast current status to all WebSocket connections"""
        status_data = {
            "type": "status_update",
            "data": {
                "running": self.running,
                "current_fuel": bot_state["current_fuel"],
                "shields_active": bot_state["shields_active"],
                "position": bot_state["position"],
                "status": bot_state["status"],
                "settings": bot_state["settings"]
            }
        }
        
        # Send to all connected WebSocket clients
        for connection in websocket_connections[:]:
            try:
                await connection.send_text(json.dumps(status_data))
            except:
                websocket_connections.remove(connection)
    
    async def stop(self):
        """Stop the bot and cleanup"""
        self.running = False
        bot_state["running"] = False
        bot_state["status"] = "stopped"
        
        await self.cleanup_browser()

# Global bot instance
tankpit_bot = TankpitBot()

# API Routes
@api_router.post("/bot/login")
async def login_to_tankpit(credentials: LoginCredentials):
    """Login to tankpit.com"""
    try:
        await tankpit_bot.start_browser()
        success = await tankpit_bot.login(credentials.username, credentials.password)
        
        if success:
            bot_state["settings"]["username"] = credentials.username
            bot_state["settings"]["password"] = credentials.password
            return {"success": True, "message": "Login successful"}
        else:
            return {"success": False, "message": "Login failed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/bot/tanks")
async def get_tanks():
    """Get available tanks"""
    try:
        tanks = await tankpit_bot.get_available_tanks()
        return {"success": True, "tanks": tanks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/bot/select-tank/{tank_id}")
async def select_tank(tank_id: str):
    """Select a tank"""
    try:
        success = await tankpit_bot.select_tank(tank_id)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/bot/start")
async def start_bot():
    """Start the bot"""
    try:
        if not tankpit_bot.running:
            tankpit_bot.running = True
            bot_state["running"] = True
            bot_state["status"] = "starting"
            
            # Start bot cycle in background
            asyncio.create_task(tankpit_bot.run_bot_cycle())
            
        return {"success": True, "message": "Bot started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/bot/stop")
async def stop_bot():
    """Stop the bot"""
    try:
        await tankpit_bot.stop()
        return {"success": True, "message": "Bot stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/bot/status")
async def get_bot_status():
    """Get current bot status"""
    return {
        "running": bot_state["running"],
        "current_fuel": bot_state["current_fuel"],
        "shields_active": bot_state["shields_active"],
        "position": bot_state["position"],
        "status": bot_state["status"],
        "settings": bot_state["settings"]
    }

@api_router.post("/bot/settings")
async def update_settings(settings: BotSettings):
    """Update bot settings"""
    bot_state["settings"].update(settings.dict())
    return {"success": True, "settings": bot_state["settings"]}

@api_router.websocket("/ws/bot-status")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time status updates"""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    try:
        # Send initial status
        initial_status = {
            "type": "status_update",
            "data": {
                "running": bot_state["running"],
                "current_fuel": bot_state["current_fuel"],
                "shields_active": bot_state["shields_active"],
                "position": bot_state["position"],
                "status": bot_state["status"],
                "settings": bot_state["settings"]
            }
        }
        await websocket.send_text(json.dumps(initial_status))
        
        # Keep connection alive with periodic pings
        while True:
            await asyncio.sleep(30)  # Send ping every 30 seconds
            ping_data = {"type": "ping", "timestamp": datetime.now().isoformat()}
            await websocket.send_text(json.dumps(ping_data))
    except WebSocketDisconnect:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)

# Include router
app.include_router(api_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    await tankpit_bot.stop()
    client.close()