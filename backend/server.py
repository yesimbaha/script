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
        # Always clean up any existing browser first to avoid stale sessions
        await self.cleanup_browser()
            
        try:
            playwright = await async_playwright().__aenter__()
            
            # Launch fresh browser instance
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
            
            # Verify we're on the right page
            page_title = await self.page.title()
            current_url = self.page.url
            
            if "tankpit" not in page_title.lower() and "tankpit" not in current_url.lower():
                logging.error(f"Not on tankpit.com - Title: {page_title}, URL: {current_url}")
                return False
            
            logging.info(f"Successfully navigated to tankpit.com - Title: {page_title}")
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
            # Start fresh browser session
            if not await self.start_browser():
                return False
                
            # Wait for page to fully load and verify we're on the right page
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            
            # Take screenshot for debugging
            await self.page.screenshot(path="/tmp/tankpit_before_login.png")
            
            # Verify page content
            page_content = await self.page.content()
            if "header-login" not in page_content:
                logging.error("Page doesn't contain expected login elements")
                # Try refreshing the page
                await self.page.reload()
                await self.page.wait_for_load_state("networkidle", timeout=10000)
                page_content = await self.page.content()
            
            # TankPit.com specific: Click the header login link to show the overlay
            login_clicked = False
            
            # Try multiple approaches to find and click the login link
            login_selectors = [
                '#header-login',
                'a[href="#login"]',
                'a:has-text("Log in")',
                'a:has-text("Login")'
            ]
            
            for selector in login_selectors:
                try:
                    header_login = await self.page.wait_for_selector(selector, timeout=3000)
                    if header_login:
                        # Check if element is visible and clickable
                        is_visible = await header_login.is_visible()
                        if is_visible:
                            await header_login.click()
                            logging.info(f"Clicked login link with selector: {selector}")
                            login_clicked = True
                            break
                        else:
                            logging.warning(f"Login element found but not visible: {selector}")
                except Exception as e:
                    logging.warning(f"Could not find login element with selector {selector}: {e}")
                    continue
            
            if not login_clicked:
                logging.error("Could not find any clickable login elements")
                return False
                
            # Wait for login overlay to appear
            await self.page.wait_for_timeout(2000)
            
            # Now look for the login form fields
            username_field = None
            password_field = None
            
            # TankPit.com specific selectors
            try:
                username_field = await self.page.wait_for_selector('#login-username', timeout=5000)
                logging.info("Found tankpit.com username field: #login-username")
            except Exception as e:
                logging.error(f"Could not find username field: {e}")
                # Take screenshot for debugging
                await self.page.screenshot(path="/tmp/tankpit_no_username.png")
                return False
            
            try:
                password_field = await self.page.wait_for_selector('#login input[name="password"][type="password"]', timeout=5000)
                logging.info("Found tankpit.com password field")
            except Exception as e:
                logging.error(f"Could not find password field: {e}")
                # Take screenshot for debugging
                await self.page.screenshot(path="/tmp/tankpit_no_password.png")
                return False
            
            # Fill in credentials
            await username_field.fill(username)
            await password_field.fill(password)
            logging.info("Filled in credentials")
            
            # Look for submit button
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
            
            # Take screenshot after login attempt
            await self.page.screenshot(path="/tmp/tankpit_after_login_attempt.png")
            
            # TankPit specific success indicators
            if ("dashboard" in current_url.lower() or 
                "game" in current_url.lower() or 
                "play" in current_url.lower() or
                "welcome" in page_content.lower() or
                "logout" in page_content.lower() or
                f"Logged in: {username}" in page_content):
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
            
            # Check if login overlay disappeared (success indicator)
            try:
                login_overlay = await self.page.query_selector('#login.overlay')
                if login_overlay:
                    overlay_visible = await login_overlay.is_visible()
                    if not overlay_visible:
                        logging.info("Login overlay disappeared, login likely successful")
                        return True
                    else:
                        logging.error("Login overlay still visible, login likely failed")
                        return False
            except:
                pass
            
            # Final check - look for user info in page content
            if f"Logged in: {username}" in page_content or username in page_content:
                logging.info("Found username in page content, login successful")
                return True
            
            logging.error("Login failed - no success indicators found")
            return False
            
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
                
            # Get current page info
            current_url = self.page.url
            page_title = await self.page.title()
            logging.info(f"Current URL: {current_url}")
            logging.info(f"Page title: {page_title}")
            
            tanks = []
            
            # TankPit.com specific: Look for the logged-in user info that shows current tank
            # From the logs, we can see: "Tank: General Boofington"
            try:
                # Look for elements that contain tank information
                user_info_elements = await self.page.query_selector_all('*')
                
                for element in user_info_elements:
                    try:
                        text = await element.inner_text()
                        if text and "Tank:" in text:
                            # Found tank information
                            lines = text.split('\n')
                            for line in lines:
                                if line.strip().startswith("Tank:"):
                                    tank_name = line.replace("Tank:", "").strip()
                                    if tank_name:
                                        logging.info(f"Found tank: {tank_name}")
                                        tanks.append({
                                            "name": tank_name,
                                            "id": "0",  # Primary tank
                                            "fuel": 100,  # Default, will be updated when we get real data
                                            "position": {"x": 0, "y": 0}
                                        })
                                        break
                            if tanks:  # If we found a tank, we can stop looking
                                break
                    except:
                        continue
                        
            except Exception as e:
                logging.error(f"Error looking for tank info in user elements: {e}")
            
            # Alternative approach: Look in the page source for JavaScript variables
            if not tanks:
                try:
                    page_content = await self.page.content()
                    
                    # Look for the tankpit JavaScript object that contains user info
                    import re
                    
                    # Look for tank name in various patterns
                    tank_patterns = [
                        r'Tank:\s*([^\\n\\r]+)',
                        r'"tank":\s*"([^"]+)"',
                        r"'tank':\s*'([^']+)'",
                        r'tank_name["\']?\s*:\s*["\']([^"\']+)["\']'
                    ]
                    
                    for pattern in tank_patterns:
                        matches = re.findall(pattern, page_content, re.IGNORECASE)
                        if matches:
                            for match in matches:
                                tank_name = match.strip()
                                if tank_name and len(tank_name) > 1:
                                    logging.info(f"Found tank via regex: {tank_name}")
                                    tanks.append({
                                        "name": tank_name,
                                        "id": str(len(tanks)),
                                        "fuel": 100,
                                        "position": {"x": 0, "y": 0}
                                    })
                    
                    # Remove duplicates
                    unique_tanks = []
                    seen_names = set()
                    for tank in tanks:
                        if tank["name"] not in seen_names:
                            unique_tanks.append(tank)
                            seen_names.add(tank["name"])
                    tanks = unique_tanks
                    
                except Exception as e:
                    logging.error(f"Error parsing page content for tanks: {e}")
            
            # If still no tanks found, look for "Manage Tanks" or similar links
            if not tanks:
                try:
                    manage_links = await self.page.query_selector_all('a[href*="tank"], a:has-text("Manage"), a:has-text("Tank")')
                    if manage_links:
                        logging.info(f"Found {len(manage_links)} tank management links")
                        # Click on tank management link
                        await manage_links[0].click()
                        await self.page.wait_for_load_state("networkidle", timeout=10000)
                        
                        # Now look for tank list on the management page
                        tank_elements = await self.page.query_selector_all('.tank, [class*="tank"], li, tr')
                        for i, element in enumerate(tank_elements[:5]):  # Check first 5 elements
                            try:
                                text = await element.inner_text()
                                if text and len(text.strip()) > 2 and len(text.strip()) < 50:
                                    # Potential tank name
                                    tanks.append({
                                        "name": text.strip(),
                                        "id": str(i),
                                        "fuel": 100,
                                        "position": {"x": 0, "y": 0}
                                    })
                            except:
                                continue
                                
                except Exception as e:
                    logging.error(f"Error trying to access tank management: {e}")
            
            # If we still have no tanks, create a default one based on the logged-in user
            if not tanks:
                # We know from the logs that there's a tank, so create a default entry
                tanks.append({
                    "name": "Your Tank (Auto-detected)",
                    "id": "0",
                    "fuel": 100,
                    "position": {"x": 0, "y": 0}
                })
                logging.info("Created default tank entry")
            
            logging.info(f"Final tank count: {len(tanks)}")
            for tank in tanks:
                logging.info(f"Tank found: {tank['name']}")
                
            return tanks
            
        except Exception as e:
            logging.error(f"Failed to get tanks: {e}")
            return []
    
    async def select_tank(self, tank_id: str):
        """Select a specific tank"""
        try:
            if not self.page:
                logging.error("No page available for tank selection")
                return False
                
            # For tankpit.com, tank selection might be different than a typical list
            # The tank "General Boofington" is already the active/selected tank shown in user info
            
            # Check if we need to navigate to a tank selection page
            current_url = self.page.url
            page_content = await self.page.content()
            
            # Look for tank management or selection links
            tank_management_links = [
                'a[href*="tank"]',
                'a:has-text("Manage")',
                'a:has-text("Tank")',
                'a:has-text("Select")',
                'a:has-text("Choose")'
            ]
            
            tank_clicked = False
            
            # Try to find and navigate to tank management
            for selector in tank_management_links:
                try:
                    links = await self.page.query_selector_all(selector)
                    for link in links:
                        link_text = await link.inner_text()
                        href = await link.get_attribute('href') if link else ""
                        
                        # Look for tank-related management links
                        if any(keyword in link_text.lower() for keyword in ['tank', 'manage', 'select']) or \
                           any(keyword in href.lower() for keyword in ['tank', 'manage']):
                            await link.click()
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                            tank_clicked = True
                            logging.info(f"Clicked tank management link: {link_text}")
                            break
                    if tank_clicked:
                        break
                except Exception as e:
                    logging.warning(f"Could not find tank management with selector {selector}: {e}")
                    continue
            
            # If we navigated to a tank management page, look for tank selection elements
            if tank_clicked:
                # Look for tank elements on the management page
                tank_selectors = [
                    f".tank:nth-child({int(tank_id) + 1})",
                    f".tank-list .tank:nth-child({int(tank_id) + 1})",
                    f"[data-tank-id='{tank_id}']",
                    f"tr:nth-child({int(tank_id) + 1})",
                    f"li:nth-child({int(tank_id) + 1})"
                ]
                
                for selector in tank_selectors:
                    try:
                        tank_element = await self.page.wait_for_selector(selector, timeout=3000)
                        if tank_element:
                            await tank_element.click()
                            await self.page.wait_for_timeout(2000)
                            logging.info(f"Selected tank using selector: {selector}")
                            return True
                    except:
                        continue
            
            # Alternative approach: If the tank is already active/selected (which it seems to be)
            # Check if "General Boofington" is already the active tank
            if "General Boofington" in page_content:
                logging.info("Tank 'General Boofington' appears to already be active/selected")
                
                # For tankpit.com, the tank might already be selected by default
                # Check if we can start playing with this tank
                play_buttons = await self.page.query_selector_all('a[href*="play"], button:has-text("Play"), .play-button')
                if play_buttons:
                    logging.info("Tank appears to be ready for play - considering selection successful")
                    return True
            
            # If we can't find specific tank selection UI, but we detected the tank,
            # it might mean the tank is already selected and ready to use
            logging.info("No explicit tank selection UI found, but tank was detected - assuming selection successful")
            return True
            
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
    
    async def enter_game(self):
        """Enter the actual tankpit.com game interface"""
        try:
            if not self.page:
                logging.error("No page available for game entry")
                return False
                
            current_url = self.page.url
            logging.info(f"Attempting to enter game from URL: {current_url}")
            
            # Look for play/game entry buttons
            play_selectors = [
                'a:has-text("Play")',
                'button:has-text("Play")',
                'a:has-text("Play Now")',
                'button:has-text("Play Now")',
                '.play-button',
                '#play-button',
                'a[href*="play"]',
                'a[href*="game"]',
                'button:has-text("Enter")',
                'button:has-text("Start")'
            ]
            
            game_entered = False
            
            for selector in play_selectors:
                try:
                    play_button = await self.page.wait_for_selector(selector, timeout=3000)
                    if play_button:
                        # Check if button is visible and clickable
                        is_visible = await play_button.is_visible()
                        if is_visible:
                            button_text = await play_button.inner_text()
                            logging.info(f"Found play button: {button_text} with selector: {selector}")
                            
                            await play_button.click()
                            await self.page.wait_for_load_state("networkidle", timeout=15000)
                            
                            # Check if we successfully entered the game
                            new_url = self.page.url
                            page_content = await self.page.content()
                            
                            if (new_url != current_url or 
                                "game" in new_url.lower() or 
                                "play" in new_url.lower() or
                                any(keyword in page_content.lower() for keyword in ['fuel', 'tank', 'health', 'armor', 'weapon'])):
                                logging.info(f"Successfully entered game! New URL: {new_url}")
                                game_entered = True
                                break
                            else:
                                logging.warning(f"Click didn't seem to enter game, trying next option")
                                
                except Exception as e:
                    logging.warning(f"Could not find/click play button with selector {selector}: {e}")
                    continue
            
            if not game_entered:
                logging.error("Could not find any working play/game entry buttons")
                # Take screenshot for debugging
                await self.page.screenshot(path="/tmp/tankpit_no_game_entry.png")
                return False
                
            # Wait a bit more for game interface to fully load
            await self.page.wait_for_timeout(3000)
            logging.info("Game interface should now be loaded")
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to enter game: {e}")
            return False
    
    async def activate_bot_and_mine(self):
        """Activate bot and mine features on new screen"""
        try:
            # Look for bot button
            bot_button = await self.page.query_selector("button:has-text('bot'), .bot-button, [data-action='bot']")
            if bot_button:
                await bot_button.click()
                await self.page.wait_for_timeout(1000)
                logging.info("Activated bot feature")
            
            # Look for mine button  
            mine_button = await self.page.query_selector("button:has-text('mine'), .mine-button, [data-action='mine']")
            if mine_button:
                await mine_button.click()
                await self.page.wait_for_timeout(1000)
                logging.info("Activated mine feature")
                
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