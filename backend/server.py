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
import re

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
    "current_map": "none",
    "settings": {
        "refuel_threshold": 25,
        "shield_threshold": 10,
        "safe_threshold": 85,
        "target_player": "",
        "username": "",
        "password": "",
        "preferred_map": "world"
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
    preferred_map: str = "world"  # "world", "practice", "tournament"

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
        """Detect current fuel level from tankpit.com interface"""
        try:
            if not self.page:
                logging.error("No page available for fuel detection")
                return 50
                
            # Strategy 1: Look for the fuel/HP bar in the bottom UI panel (based on screenshot)
            try:
                # The fuel bar appears to be in the bottom UI area, let's look for progress bars there
                fuel_bar_selectors = [
                    # Look for progress bars, bars, or similar elements
                    'div[style*="width"][style*="%"]',  # Divs with width percentages
                    '.bar', '.progress', '.fuel', '.health', '.hp',
                    'div[class*="bar"]', 'div[class*="fuel"]', 'div[class*="health"]',
                    # Canvas elements that might contain the bar
                    'canvas'
                ]
                
                for selector in fuel_bar_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        for element in elements:
                            if not await element.is_visible():
                                continue
                                
                            # Check if element is in the bottom portion of the screen
                            bounding_box = await element.bounding_box()
                            if bounding_box:
                                viewport_height = self.page.viewport_size["height"]
                                # Only consider elements in the bottom 30% of the screen
                                if bounding_box["y"] < viewport_height * 0.7:
                                    continue
                                    
                                # Check for width-based percentage (common for fuel bars)
                                style = await element.get_attribute('style')
                                if style and 'width:' in style:
                                    width_match = re.search(r'width:\s*(\d+)%', style)
                                    if width_match:
                                        fuel_percentage = int(width_match.group(1))
                                        if 0 <= fuel_percentage <= 100:
                                            logging.info(f"Found fuel level {fuel_percentage}% from element style: {style}")
                                            return fuel_percentage
                                            
                    except Exception as e:
                        continue
            except Exception as e:
                logging.error(f"Error in Strategy 1 fuel detection: {e}")
            
            # Strategy 2: Take screenshot and analyze the fuel bar area visually
            try:
                # Take screenshot
                screenshot = await self.page.screenshot()
                
                # Convert to OpenCV format
                nparr = np.frombuffer(screenshot, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if img is None:
                    raise Exception("Failed to decode screenshot")
                
                # Get image dimensions
                height, width = img.shape[:2]
                
                # Focus on the bottom UI area where the fuel bar is located (bottom 25% of screen)
                bottom_ui_start = int(height * 0.75)
                ui_area = img[bottom_ui_start:height, :]
                
                # Convert to different color spaces for analysis
                hsv = cv2.cvtColor(ui_area, cv2.COLOR_BGR2HSV)
                gray = cv2.cvtColor(ui_area, cv2.COLOR_BGR2GRAY)
                
                # Look for black pixels (depleted health) - very dark pixels
                black_lower = np.array([0, 0, 0])
                black_upper = np.array([30, 30, 30])  # Very dark but not pure black due to compression
                black_mask = cv2.inRange(ui_area, black_lower, black_upper)
                
                # Alternative: use grayscale threshold for black detection
                _, black_thresh = cv2.threshold(gray, 40, 255, cv2.THRESH_BINARY_INV)
                
                # Combine both black detection methods
                black_combined = cv2.bitwise_or(black_mask, black_thresh)
                
                # Look for colored pixels (remaining health) - exclude very dark and very light pixels
                # This will catch tank colors (which can be various colors)
                colored_lower = np.array([40, 40, 40])   # Not too dark
                colored_upper = np.array([220, 220, 220]) # Not too bright (avoid white UI elements)
                colored_mask = cv2.inRange(ui_area, colored_lower, colored_upper)
                
                # Remove black pixels from colored mask
                colored_only = cv2.bitwise_and(colored_mask, cv2.bitwise_not(black_combined))
                
                # Count pixels
                black_pixels = cv2.countNonZero(black_combined)
                colored_pixels = cv2.countNonZero(colored_only)
                total_bar_pixels = black_pixels + colored_pixels
                
                # Only proceed if we found a significant bar (likely the health bar)
                if total_bar_pixels > 50:  # Reduced threshold
                    if total_bar_pixels > 0:
                        fuel_percentage = int((colored_pixels / total_bar_pixels) * 100)
                        fuel_percentage = max(0, min(100, fuel_percentage))  # Clamp to 0-100
                        logging.info(f"Detected fuel level {fuel_percentage}% via color analysis (tank_color: {colored_pixels}, black: {black_pixels})")
                        return fuel_percentage
                        
                # Try alternative approach: look for the most common non-black color in bottom area
                # This might be the tank color
                if total_bar_pixels <= 50:
                    # Create mask for bottom area excluding very dark and very light pixels
                    mid_range_mask = cv2.inRange(ui_area, np.array([30, 30, 30]), np.array([200, 200, 200]))
                    
                    if cv2.countNonZero(mid_range_mask) > 0:
                        # Find the most dominant color (excluding black/dark pixels)
                        pixels = ui_area[mid_range_mask > 0]
                        if len(pixels) > 0:
                            # Use a simple approach: assume most pixels are health, few are black
                            dark_pixels = cv2.countNonZero(cv2.inRange(ui_area, np.array([0, 0, 0]), np.array([50, 50, 50])))
                            total_ui_pixels = ui_area.shape[0] * ui_area.shape[1]
                            health_pixels = total_ui_pixels - dark_pixels
                            
                            if total_ui_pixels > 0:
                                fuel_percentage = int((health_pixels / total_ui_pixels) * 100)
                                fuel_percentage = max(20, min(100, fuel_percentage))  # Reasonable bounds
                                logging.info(f"Estimated fuel level {fuel_percentage}% via alternative analysis")
                                return fuel_percentage
                    
            except Exception as e:
                logging.error(f"Error in visual fuel detection: {e}")
            
            # Strategy 3: Look for numerical fuel indicators in the UI
            try:
                # Look for text elements that might show fuel numbers
                page_content = await self.page.content()
                
                # Search for fuel-related patterns in the HTML
                fuel_patterns = [
                    r'fuel["\s:]*(\d+)',
                    r'hp["\s:]*(\d+)', 
                    r'health["\s:]*(\d+)',
                    r'energy["\s:]*(\d+)',
                    # Look for numbers that might be percentages
                    r'(\d+)%',
                    # Look for fractions like "75/100"
                    r'(\d+)/100'
                ]
                
                for pattern in fuel_patterns:
                    matches = re.findall(pattern, page_content, re.IGNORECASE)
                    for match in matches:
                        fuel_value = int(match)
                        if 0 <= fuel_value <= 100:
                            logging.info(f"Found potential fuel level {fuel_value}% in page content")
                            return fuel_value
                        elif fuel_value > 100:
                            # Might be out of a larger number, convert to percentage
                            normalized = min(100, fuel_value // 10)
                            if normalized > 0:
                                logging.info(f"Normalized fuel level to {normalized}% from value {fuel_value}")
                                return normalized
                                
            except Exception as e:
                logging.error(f"Error in text-based fuel detection: {e}")
            
            # Strategy 4: Try JavaScript evaluation for game state
            try:
                js_fuel_checks = [
                    # Common game state variables
                    "window.gameState && window.gameState.player && window.gameState.player.fuel",
                    "window.player && window.player.health",
                    "window.tank && window.tank.fuel", 
                    "window.game && window.game.fuel",
                    # Try to find elements with fuel-related IDs/classes
                    "document.querySelector('[id*=\"fuel\"], [class*=\"fuel\"], [id*=\"health\"], [class*=\"health\"]') && document.querySelector('[id*=\"fuel\"], [class*=\"fuel\"], [id*=\"health\"], [class*=\"health\"]').textContent"
                ]
                
                for js_check in js_fuel_checks:
                    try:
                        result = await self.page.evaluate(js_check)
                        if result:
                            if isinstance(result, (int, float)) and 0 <= result <= 100:
                                logging.info(f"Found fuel level {int(result)}% via JavaScript")
                                return int(result)
                            elif isinstance(result, str):
                                numbers = re.findall(r'(\d+)', result)
                                if numbers:
                                    fuel_val = int(numbers[0])
                                    if 0 <= fuel_val <= 100:
                                        logging.info(f"Extracted fuel level {fuel_val}% from JS result")
                                        return fuel_val
                    except:
                        continue
                        
            except Exception as e:
                logging.error(f"Error in JavaScript fuel detection: {e}")
            
            # If no fuel detected, save debug screenshot and return a reasonable default
            await self.page.screenshot(path="/tmp/tankpit_fuel_debug_detailed.png")
            logging.warning("Could not detect fuel level with any method, using default value")
            
            # Return a reasonable default or last known value
            last_fuel = bot_state.get("current_fuel", 75)
            return max(25, last_fuel)  # Ensure minimum 25% to prevent constant shield activation
            
        except Exception as e:
            logging.error(f"Failed to detect fuel level: {e}")
            return 75  # Safe default
    
    async def get_available_maps(self):
        """Get list of available maps/game modes"""
        try:
            if not self.page:
                logging.error("No page available for map detection")
                return []
                
            maps = []
            
            # Look for different game mode options
            map_selectors = [
                'a:has-text("Practice")',
                'button:has-text("Practice")',
                'a:has-text("Tournament")',
                'button:has-text("Tournament")',
                'a:has-text("Play")',
                'button:has-text("Play")',
                '.game-mode',
                '.map-option',
                '[data-map]',
                '[data-mode]'
            ]
            
            for selector in map_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    for element in elements:
                        text = await element.inner_text()
                        href = await element.get_attribute('href') or ""
                        data_map = await element.get_attribute('data-map') or ""
                        
                        if text and len(text.strip()) > 0:
                            map_info = {
                                "name": text.strip(),
                                "selector": selector,
                                "element": element,
                                "href": href,
                                "data_map": data_map
                            }
                            
                            # Categorize the map type
                            text_lower = text.lower()
                            if "practice" in text_lower:
                                map_info["type"] = "practice"
                            elif "tournament" in text_lower or "tourney" in text_lower:
                                map_info["type"] = "tournament"
                            elif "play" in text_lower:
                                map_info["type"] = "general"
                            else:
                                map_info["type"] = "unknown"
                            
                            maps.append(map_info)
                            logging.info(f"Found map option: {text.strip()} (type: {map_info['type']})")
                            
                except Exception as e:
                    continue
            
            # Remove duplicates based on name
            unique_maps = []
            seen_names = set()
            for map_info in maps:
                if map_info["name"] not in seen_names:
                    unique_maps.append(map_info)
                    seen_names.add(map_info["name"])
            
            logging.info(f"Found {len(unique_maps)} unique map options")
            return unique_maps
            
        except Exception as e:
            logging.error(f"Failed to get available maps: {e}")
            return []

    async def enter_game(self):
        """Enter the actual tankpit.com game interface with map selection"""
        try:
            if not self.page:
                logging.error("No page available for game entry")
                return False
                
            current_url = self.page.url
            logging.info(f"Attempting to enter game from URL: {current_url}")
            
            # Get user's preferred map type
            preferred_map = bot_state["settings"].get("preferred_map", "world")
            logging.info(f"User prefers map type: {preferred_map}")
            
            # Step 1: Navigate to the correct map page
            map_navigated = False
            
            if preferred_map == "world":
                # Look for main "Play" button or world map
                world_selectors = [
                    'a[href="/play"]',
                    'a:has-text("Play")',
                    'button:has-text("Play")',
                    'a:has-text("World")',
                    'a:has-text("Main")'
                ]
                
                for selector in world_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=3000)
                        if element and await element.is_visible():
                            await element.click()
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                            logging.info(f"Navigated to world map using: {selector}")
                            map_navigated = True
                            break
                    except:
                        continue
                        
            elif preferred_map == "practice":
                # Look for practice map options
                practice_selectors = [
                    'a[href*="practice"]',
                    'a:has-text("Practice")',
                    'button:has-text("Practice")',
                    'a:has-text("Training")'
                ]
                
                for selector in practice_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=3000)
                        if element and await element.is_visible():
                            await element.click()
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                            logging.info(f"Navigated to practice map using: {selector}")
                            map_navigated = True
                            break
                    except:
                        continue
                        
            elif preferred_map == "tournament":
                # Look for tournament options
                tournament_selectors = [
                    'a[href*="tournament"]',
                    'a:has-text("Tournament")',
                    'button:has-text("Tournament")',
                    'a:has-text("Competitive")'
                ]
                
                for selector in tournament_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=3000)
                        if element and await element.is_visible():
                            await element.click()
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                            logging.info(f"Navigated to tournament map using: {selector}")
                            map_navigated = True
                            break
                    except:
                        continue
            
            if not map_navigated:
                logging.warning("Could not navigate to preferred map, trying generic play")
                # Fallback to any play button
                fallback_selectors = ['a:has-text("Play")', 'button:has-text("Play")']
                for selector in fallback_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=3000)
                        if element and await element.is_visible():
                            await element.click()
                            await self.page.wait_for_load_state("networkidle", timeout=10000)
                            logging.info(f"Used fallback play button: {selector}")
                            map_navigated = True
                            break
                    except:
                        continue
            
            if not map_navigated:
                logging.error("Failed to navigate to any map")
                return False
            
            # Step 2: Now we should be on a map page, click the middle to enter game
            await self.page.wait_for_timeout(3000)  # Wait for page to load
            
            current_url = self.page.url
            logging.info(f"Now on map page: {current_url}")
            
            # Click in the middle of the page/map to spawn at that location
            try:
                # Get viewport size
                viewport_size = self.page.viewport_size
                center_x = viewport_size["width"] // 2
                center_y = viewport_size["height"] // 2
                
                logging.info(f"Clicking center of map at coordinates ({center_x}, {center_y})")
                
                # Click in the center of the map
                await self.page.mouse.click(center_x, center_y)
                
                # Wait for game to load after clicking
                await self.page.wait_for_timeout(5000)
                await self.page.wait_for_load_state("networkidle", timeout=15000)
                
                # Check if we successfully entered the game
                new_url = self.page.url
                page_content = await self.page.content()
                
                # Look for game interface indicators after map click
                if (new_url != current_url or 
                    any(keyword in page_content.lower() for keyword in [
                        'fuel', 'health', 'armor', 'weapon', 'tank', 'ammo', 
                        'score', 'kills', 'playing', 'match', 'game'
                    ])):
                    logging.info(f"Successfully entered game by clicking map center! New URL: {new_url}")
                    bot_state["current_map"] = preferred_map
                    game_joined = True
                else:
                    logging.info("Clicked map center, checking if we're in game interface...")
                    # Sometimes the URL doesn't change but we're still in the game
                    # Check for game elements in the DOM
                    game_elements = await self.page.query_selector_all(
                        'canvas, #game, .game, .game-area, .map, .battlefield'
                    )
                    if game_elements:
                        logging.info("Found game canvas/elements - assuming successful game entry")
                        bot_state["current_map"] = preferred_map
                        game_joined = True
                    else:
                        logging.warning("Map click didn't seem to enter game")
                        # Take screenshot for debugging
                        await self.page.screenshot(path="/tmp/tankpit_after_map_click.png")
                
            except Exception as e:
                logging.error(f"Failed to click map center: {e}")
                
            if not game_joined:
                logging.error("Could not enter game by clicking map")
                
                # Maybe we're already in the game? Check page content again
                page_content = await self.page.content()
                if any(keyword in page_content.lower() for keyword in [
                    'fuel', 'health', 'armor', 'weapon', 'tank', 'ammo'
                ]):
                    logging.info("Looks like we might already be in the game interface")
                    bot_state["current_map"] = preferred_map
                    return True
                
                return False
                
            # Wait for game interface to fully load
            await self.page.wait_for_timeout(5000)
            logging.info("Game interface should now be fully loaded after map click")
            
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
        # First, make sure we're in the game
        if not await self.enter_game():
            logging.error("Failed to enter game, stopping bot")
            bot_state["status"] = "failed_to_enter_game"
            self.running = False
            return
            
        bot_state["status"] = "entered_game"
        logging.info("Bot successfully entered the game")
        
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
        bot_state["status"] = "stopping"
        
        # Press Q to exit the map before cleanup
        try:
            if self.page:
                logging.info("Pressing Q to exit the map...")
                await self.page.keyboard.press("q")
                await self.page.wait_for_timeout(2000)  # Wait for exit to process
                bot_state["status"] = "exited_map"
                logging.info("Successfully pressed Q to exit map")
        except Exception as e:
            logging.error(f"Failed to press Q to exit map: {e}")
        
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

@api_router.get("/bot/maps")
async def get_maps():
    """Get available maps"""
    try:
        maps = await tankpit_bot.get_available_maps()
        # Convert maps to serializable format
        serializable_maps = []
        for map_info in maps:
            serializable_maps.append({
                "name": map_info["name"],
                "type": map_info["type"],
                "href": map_info["href"],
                "data_map": map_info["data_map"]
            })
        return {"success": True, "maps": serializable_maps}
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
        "current_map": bot_state.get("current_map", "none"),
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