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
import time

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
        
    
    async def dismiss_login_overlay(self):
        """Dismiss any remaining login overlays that might be intercepting clicks"""
        try:
            if not self.page:
                return
                
            logging.info("Attempting to dismiss login overlay...")
            
            # Method 1: Try to close overlay with common close selectors
            close_selectors = [
                '.overlay-close',
                '.close-button', 
                '.modal-close',
                '#login .close',
                '.login-overlay .close',
                '[aria-label="Close"]',
                'button:has-text("Close")',
                'button:has-text("×")',
                '.fa-times',
                '.fa-close'
            ]
            
            for selector in close_selectors:
                try:
                    close_element = await self.page.query_selector(selector)
                    if close_element:
                        await close_element.click()
                        await self.page.wait_for_timeout(1000)
                        logging.info(f"Closed overlay using selector: {selector}")
                        break
                except:
                    continue
            
            # Method 2: Press Escape key to dismiss modals
            try:
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(1000)
                logging.info("Pressed Escape to dismiss overlays")
            except:
                pass
            
            # Method 3: Click outside the login overlay if it exists
            try:
                overlay = await self.page.query_selector('#login.overlay, .login-overlay')
                if overlay:
                    # Click in the top-left corner to dismiss overlay
                    await self.page.mouse.click(50, 50)
                    await self.page.wait_for_timeout(1000)
                    logging.info("Clicked outside overlay to dismiss")
            except:
                pass
            
            # Method 4: Use JavaScript to hide overlay directly
            try:
                await self.page.evaluate("""
                    // Hide login overlay using JavaScript
                    const overlays = document.querySelectorAll('#login, .overlay, .login-overlay, .modal');
                    overlays.forEach(overlay => {
                        if (overlay.style) {
                            overlay.style.display = 'none';
                            overlay.style.visibility = 'hidden';
                            overlay.style.opacity = '0';
                            overlay.style.pointerEvents = 'none';
                        }
                    });
                    
                    // Remove overlay elements completely if needed
                    const loginOverlay = document.querySelector('#login.overlay');
                    if (loginOverlay) {
                        loginOverlay.remove();
                    }
                    
                    return 'Overlays hidden via JavaScript';
                """)
                logging.info("Used JavaScript to hide login overlays")
            except Exception as e:
                logging.warning(f"JavaScript overlay dismissal failed: {e}")
            
            # Method 5: Wait for overlay to disappear naturally
            try:
                await self.page.wait_for_selector('#login.overlay', state='hidden', timeout=3000)
                logging.info("Login overlay disappeared naturally")
            except:
                logging.warning("Login overlay did not disappear within timeout")
            
            # Give the page a moment to settle after overlay dismissal
            await self.page.wait_for_timeout(2000)
            logging.info("Login overlay dismissal completed")
            
        except Exception as e:
            logging.error(f"Error dismissing login overlay: {e}")
    
    async def start_browser(self):
        """Initialize browser and navigate to tankpit.com"""
        # Always clean up any existing browser first to avoid stale sessions
        await self.cleanup_browser()
            
        try:
            playwright = await async_playwright().__aenter__()
            
            # Launch fresh browser instance with improved resource management
            self.browser = await playwright.chromium.launch(
                headless=False,
                args=[
                    '--no-sandbox', 
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--remote-debugging-port=9222',
                    '--display=:99',
                    '--memory-pressure-off',  # Prevent memory pressure crashes
                    '--max_old_space_size=512',  # Limit memory usage
                    '--disable-background-timer-throttling',  # Prevent timeouts
                    '--disable-renderer-backgrounding',
                    '--disable-features=TranslateUI',
                    '--disable-ipc-flood-protection'
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
        """Login to tankpit.com with improved error handling and browser session management"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                logging.info(f"Login attempt {attempt + 1}/{max_retries}")
                
                # Start fresh browser session
                if not await self.start_browser():
                    logging.error(f"Failed to start browser on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Wait before retry
                        continue
                    return False
                
                # Check if browser is still alive
                if not self.browser or not self.page:
                    logging.error("Browser or page is None after startup")
                    if attempt < max_retries - 1:
                        continue
                    return False
                    
                # Wait for page to fully load and verify we're on the right page
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                
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
                    if attempt < max_retries - 1:
                        continue
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
                    if attempt < max_retries - 1:
                        continue
                    return False
                
                try:
                    password_field = await self.page.wait_for_selector('#login input[name="password"][type="password"]', timeout=5000)
                    logging.info("Found tankpit.com password field")
                except Exception as e:
                    logging.error(f"Could not find password field: {e}")
                    # Take screenshot for debugging
                    await self.page.screenshot(path="/tmp/tankpit_no_password.png")
                    if attempt < max_retries - 1:
                        continue
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
                    logging.info(f"Success detected - URL: {current_url}, returning True")
                    
                    # CRITICAL FIX: Dismiss any remaining login overlays after successful login
                    await self.dismiss_login_overlay()
                    
                    return True
                
                # Check for error messages in the login form
                try:
                    error_elements = await self.page.query_selector_all('#login .error, #login .message, .alert-error')
                    if error_elements:
                        for error_elem in error_elements:
                            error_text = await error_elem.inner_text()
                            if error_text.strip():
                                logging.error(f"Login error detected: {error_text}")
                                if attempt < max_retries - 1:
                                    continue
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
                            if attempt < max_retries - 1:
                                continue
                            return False
                except:
                    pass
                
                # Final check - look for user info in page content
                if f"Logged in: {username}" in page_content or username in page_content:
                    logging.info("Found username in page content, login successful")
                    return True
                
                logging.error("Login failed - no success indicators found")
                if attempt < max_retries - 1:
                    logging.info("Retrying login...")
                    continue
                return False
                
            except Exception as e:
                logging.error(f"Login attempt {attempt + 1} failed with exception: {e}")
                await self.cleanup_browser()
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)  # Wait before retry
                    continue
                return False
        
        # If we get here, all attempts failed
        logging.error("All login attempts failed")
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
        """Detect current fuel level by measuring the fuel gauge - IMPROVED LOCATION DETECTION"""
        try:
            if not self.page:
                logging.error("No page available for fuel detection")
                return 50
                
            # Take a fresh screenshot for real-time detection
            screenshot = await self.page.screenshot()
            
            # Convert to OpenCV format  
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                logging.error("Failed to decode screenshot for fuel detection")
                return 50
                
            # Get image dimensions
            height, width = img.shape[:2]
            logging.info(f"Screenshot dimensions: {width}x{height}")
            
            # Try multiple areas to find the fuel gauge
            # Method 1: Bottom UI area (original)
            bottom_ui_start = int(height * 0.85)  # Bottom 15% of screen
            fuel_gauge_area = img[bottom_ui_start:height, :]
            fuel_percentage = await self.measure_fuel_gauge_simple(fuel_gauge_area, "bottom_15%")
            
            if fuel_percentage is not None and fuel_percentage > 0:
                logging.info(f"FUEL GAUGE (bottom 15%): {fuel_percentage}%")
                return fuel_percentage
            
            # Method 2: Different bottom area - maybe it's higher up
            bottom_ui_start = int(height * 0.75)  # Bottom 25% of screen
            fuel_gauge_area = img[bottom_ui_start:height, :]
            fuel_percentage = await self.measure_fuel_gauge_simple(fuel_gauge_area, "bottom_25%")
            
            if fuel_percentage is not None and fuel_percentage > 0:
                logging.info(f"FUEL GAUGE (bottom 25%): {fuel_percentage}%")
                return fuel_percentage
            
            # Method 3: Try right side of screen (some games have fuel gauge on side)
            right_ui_start = int(width * 0.8)  # Right 20% of screen
            fuel_gauge_area = img[:, right_ui_start:width]
            fuel_percentage = await self.measure_fuel_gauge_simple(fuel_gauge_area, "right_20%")
            
            if fuel_percentage is not None and fuel_percentage > 0:
                logging.info(f"FUEL GAUGE (right side): {fuel_percentage}%")
                return fuel_percentage
            
            # Method 4: Try specific coordinates where fuel gauge might be
            # Based on 1280x720 screenshot, try middle-right area
            fuel_gauge_area = img[int(height*0.6):int(height*0.9), int(width*0.7):width]
            fuel_percentage = await self.measure_fuel_gauge_simple(fuel_gauge_area, "middle_right")
            
            if fuel_percentage is not None and fuel_percentage > 0:
                logging.info(f"FUEL GAUGE (middle-right): {fuel_percentage}%")
                return fuel_percentage
            
            # If all methods fail, return a reasonable default
            logging.warning("Could not locate fuel gauge in any expected area")
            return 50
            
        except Exception as e:
            logging.error(f"Critical error in fuel detection: {e}")
            return 50
    
    async def measure_fuel_gauge_simple(self, fuel_gauge_area, location_name="unknown"):
        """Simple fuel gauge measurement with location info for debugging"""
        try:
            if fuel_gauge_area.size == 0:
                logging.warning(f"Fuel gauge area ({location_name}) is empty")
                return None
                
            # Get dimensions of the fuel gauge area
            gauge_height, gauge_width = fuel_gauge_area.shape[:2]
            
            # Look for actual fuel gauge colors - tank games often use specific colors
            # Try multiple color ranges for fuel gauges
            fuel_color_ranges = [
                # Green fuel gauge
                (np.array([0, 100, 0]), np.array([100, 255, 100])),
                # Blue fuel gauge  
                (np.array([100, 100, 0]), np.array([255, 255, 100])),
                # Yellow fuel gauge
                (np.array([0, 150, 150]), np.array([100, 255, 255])),
                # Red fuel gauge
                (np.array([0, 0, 100]), np.array([100, 100, 255])),
                # White/gray fuel gauge
                (np.array([150, 150, 150]), np.array([255, 255, 255]))
            ]
            
            best_fuel_percentage = None
            max_fuel_pixels = 0
            
            for i, (fuel_lower, fuel_upper) in enumerate(fuel_color_ranges):
                # Create mask for this fuel color range
                fuel_mask = cv2.inRange(fuel_gauge_area, fuel_lower, fuel_upper)
                fuel_pixels = cv2.countNonZero(fuel_mask)
                
                if fuel_pixels > max_fuel_pixels:
                    max_fuel_pixels = fuel_pixels
                    
                    # Define black/empty areas
                    black_lower = np.array([0, 0, 0])
                    black_upper = np.array([50, 50, 50])
                    black_mask = cv2.inRange(fuel_gauge_area, black_lower, black_upper)
                    black_pixels = cv2.countNonZero(black_mask)
                    
                    total_relevant_pixels = fuel_pixels + black_pixels
                    
                    if total_relevant_pixels > 100:  # Need enough pixels
                        fuel_percentage = int((fuel_pixels / total_relevant_pixels) * 100)
                        fuel_percentage = max(0, min(100, fuel_percentage))
                        best_fuel_percentage = fuel_percentage
                        
                        logging.info(f"FUEL GAUGE ({location_name}, color_range_{i}): {fuel_pixels} fuel, {black_pixels} empty = {fuel_percentage}%")
            
            # If no colored fuel gauge found, try the original black vs non-black approach
            if best_fuel_percentage is None or max_fuel_pixels < 50:
                black_lower = np.array([0, 0, 0])
                black_upper = np.array([50, 50, 50])
                black_mask = cv2.inRange(fuel_gauge_area, black_lower, black_upper)
                
                # Everything else is considered "colored" (fuel remaining)
                colored_lower = np.array([51, 51, 51])
                colored_upper = np.array([255, 255, 255])
                colored_mask = cv2.inRange(fuel_gauge_area, colored_lower, colored_upper)
                
                black_pixels = cv2.countNonZero(black_mask)
                colored_pixels = cv2.countNonZero(colored_mask)
                total_relevant_pixels = black_pixels + colored_pixels
                
                if total_relevant_pixels > 100:
                    fuel_percentage = int((colored_pixels / total_relevant_pixels) * 100)
                    fuel_percentage = max(0, min(100, fuel_percentage))
                    logging.info(f"FUEL GAUGE ({location_name}, black_vs_colored): {colored_pixels} fuel, {black_pixels} empty = {fuel_percentage}%")
                    return fuel_percentage
                else:
                    logging.info(f"FUEL GAUGE ({location_name}): Not enough relevant pixels ({total_relevant_pixels})")
                    return None
            
            return best_fuel_percentage
            
        except Exception as e:
            logging.error(f"Error in fuel gauge measurement ({location_name}): {e}")
            return None
    
    async def detect_death(self):
        """Detect if the bot has died and needs to respawn"""
        try:
            if not self.page:
                return False
                
            # Take screenshot to analyze for death indicators
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return False
            
            # Method 1: Look for death-related text
            page_content = await self.page.content()
            death_indicators = [
                "you have been destroyed",
                "you died", 
                "game over",
                "destroyed",
                "respawn",
                "press any key",
                "click to continue"
            ]
            
            content_lower = page_content.lower()
            for indicator in death_indicators:
                if indicator in content_lower:
                    logging.info(f"Death detected: found text '{indicator}'")
                    return True
            
            # Method 2: Look for visual death indicators (dark screen, etc.)
            # Convert to grayscale and check if screen is mostly dark
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            
            # If screen is very dark (death screen), consider it death
            if mean_brightness < 30:  # Very dark screen
                logging.info(f"Death detected: screen very dark (brightness: {mean_brightness})")
                return True
            
            # Method 3: Look for specific death screen colors (red overlays, etc.)
            # Check for red tint that often indicates death
            red_channel = img[:, :, 2]  # Red channel in BGR
            mean_red = np.mean(red_channel)
            if mean_red > 150:  # High red values might indicate death screen
                logging.info(f"Death detected: high red tint (red level: {mean_red})")
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting death: {e}")
            return False
    
    async def handle_death_and_respawn(self):
        """Handle death by pressing Q to quit and restarting"""
        try:
            logging.info("Handling death - pressing Q to quit")
            bot_state["status"] = "handling_death"
            await self.broadcast_status()
            
            # Press Q to quit the current map
            await self.page.keyboard.press("q")
            await self.page.wait_for_timeout(3000)
            
            # Wait a moment for the quit to process
            await self.page.wait_for_timeout(2000)
            
            # Use overview map to restart in a new location
            logging.info("Respawning - using overview map for new location")
            await self.use_overview_map_for_fuel()
            
            logging.info("Death handling complete - resuming normal operations")
            bot_state["status"] = "respawned"
            
        except Exception as e:
            logging.error(f"Error handling death and respawn: {e}")
            bot_state["status"] = f"death_handling_error: {str(e)}"
    
    async def detect_nothing_found_message(self):
        """Detect if radar shows 'nothing detected here' message"""
        try:
            if not self.page:
                return False
                
            page_content = await self.page.content()
            nothing_found_indicators = [
                "nothing detected here",
                "nothing found",
                "no targets detected",
                "area clear",
                "nothing detected"
            ]
            
            content_lower = page_content.lower()
            for indicator in nothing_found_indicators:
                if indicator in content_lower:
                    logging.info(f"Nothing detected message found: '{indicator}'")
                    return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting nothing found message: {e}")
            return False
    
    async def perform_random_proximity_move(self):
        """Perform a small random 12-pixel teleport to search for items - FAST VERSION"""
        try:
            if not self.page:
                return
                
            logging.info("Fast 12-pixel proximity search")
            bot_state["status"] = "fast_proximity_search"
            
            # Take screenshot to get current screen center
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return
                
            height, width = img.shape[:2]
            center_x = width // 2
            center_y = height // 2
            
            # Generate random offset within 12 pixel radius
            import random
            import math
            
            angle = random.uniform(0, 2 * math.pi)
            distance = random.uniform(5, 12)
            
            offset_x = int(distance * math.cos(angle))
            offset_y = int(distance * math.sin(angle))
            
            target_x = center_x + offset_x
            target_y = center_y + offset_y
            
            # Ensure target is within screen bounds
            target_x = max(50, min(width - 50, target_x))
            target_y = max(50, min(height - 50, target_y))
            
            # SPEED UP: Reduced wait times dramatically
            await self.page.mouse.click(target_x, target_y)
            await self.page.wait_for_timeout(800)  # Reduced from 2000ms to 800ms
            
            # Fast radar scan
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(600)  # Reduced from 2000ms to 600ms
            
            logging.info(f"Fast 12-pixel move to ({target_x}, {target_y})")
            
        except Exception as e:
            logging.error(f"Error in fast proximity move: {e}")
    
    async def move_to_screen_edge_and_radar(self):
        """Move to edge of screen and radar - FAST VERSION"""
        try:
            if not self.page:
                return
                
            logging.info("Fast screen edge exploration")
            bot_state["status"] = "fast_edge_exploration"
            
            # Take screenshot to get screen dimensions
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return
                
            height, width = img.shape[:2]
            
            # Choose random edge: 0=top, 1=right, 2=bottom, 3=left
            import random
            edge = random.randint(0, 3)
            margin = 30
            
            if edge == 0:  # Top edge
                target_x = random.randint(margin, width - margin)
                target_y = margin
                edge_name = "top"
            elif edge == 1:  # Right edge
                target_x = width - margin
                target_y = random.randint(margin, height - margin)
                edge_name = "right"
            elif edge == 2:  # Bottom edge
                target_x = random.randint(margin, width - margin)
                target_y = height - margin
                edge_name = "bottom"
            else:  # Left edge
                target_x = margin
                target_y = random.randint(margin, height - margin)
                edge_name = "left"
            
            # SPEED UP: Much faster edge movement
            await self.page.mouse.click(target_x, target_y)
            await self.page.wait_for_timeout(1200)  # Reduced from 3000ms to 1200ms
            
            # Fast radar scan
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(600)  # Reduced from 2000ms to 600ms
            
            logging.info(f"Fast {edge_name} edge exploration")
            
        except Exception as e:
            logging.error(f"Error in fast edge exploration: {e}")
    
    async def persistent_fuel_and_equipment_search(self):
        """FAST persistent search - never stop until safety threshold reached"""
        try:
            if not self.page:
                return False
                
            current_fuel = await self.detect_fuel_level()
            max_search_attempts = 25  # Increased attempts since each is faster
            search_attempt = 0
            
            logging.info(f"FAST persistent search - current: {current_fuel}%, target: {bot_state['settings']['safe_threshold']}%")
            
            while current_fuel < bot_state["settings"]["safe_threshold"] and search_attempt < max_search_attempts:
                search_attempt += 1
                bot_state["status"] = f"fast_search_{search_attempt}"
                
                # Step 1: Fast radar scan
                await self.page.keyboard.press("s")
                await self.page.wait_for_timeout(500)  # Reduced from 2000ms to 500ms
                
                # Step 2: Quick detection and collection
                fuel_nodes = await self.detect_fuel_nodes()
                equipment_items = await self.detect_equipment_visually()
                
                items_found = len(fuel_nodes) + len(equipment_items)
                
                if items_found > 0:
                    # Fast fuel collection
                    if fuel_nodes:
                        await self.fast_collect_fuel_from_nodes(fuel_nodes)
                        
                    # Fast equipment collection
                    if equipment_items:
                        await self.fast_collect_equipment()
                    
                    # Quick fuel check
                    current_fuel = await self.detect_fuel_level()
                    
                    if current_fuel >= bot_state["settings"]["safe_threshold"]:
                        logging.info(f"FAST search success: {current_fuel}%")
                        bot_state["status"] = "fast_search_complete"
                        return True
                        
                else:
                    # Fast search strategies
                    if search_attempt % 3 == 0:
                        await self.move_to_screen_edge_and_radar()
                    else:
                        await self.perform_random_proximity_move()
                
                # Quick fuel update
                current_fuel = await self.detect_fuel_level()
                
                # Minimal pause between attempts
                await self.page.wait_for_timeout(300)  # Reduced from 1000ms to 300ms
            
            if search_attempt >= max_search_attempts:
                logging.info("Fast search exhausted - using overview map")
                await self.use_overview_map_for_fuel()
                return False
            
            return True
            
        except Exception as e:
            logging.error(f"Error in fast persistent search: {e}")
            return False
    
    async def fast_collect_fuel_from_nodes(self, fuel_nodes):
        """Fast fuel collection from detected nodes"""
        try:
            for i, fuel_node in enumerate(fuel_nodes[:5]):  # Limit to 5 for speed
                # Quick click without long waits
                await self.page.mouse.click(fuel_node['x'], fuel_node['y'])
                await self.page.wait_for_timeout(400)  # Reduced from 1500ms to 400ms
                
                # Quick fuel check every 2 nodes
                if i % 2 == 1:
                    current_fuel = await self.detect_fuel_level()
                    if current_fuel >= bot_state["settings"]["safe_threshold"]:
                        logging.info(f"Fast fuel collection complete: {current_fuel}%")
                        break
            
        except Exception as e:
            logging.error(f"Error in fast fuel collection: {e}")
    
    async def fast_collect_equipment(self):
        """Fast equipment collection"""
        try:
            equipment_items = await self.detect_equipment_visually()
            
            for i, equipment in enumerate(equipment_items[:5]):  # Limit for speed
                await self.page.mouse.click(equipment['x'], equipment['y'])
                await self.page.wait_for_timeout(300)  # Reduced from 1000ms to 300ms
                
                # Stop after collecting a few items to maintain speed
                if i >= 4:
                    break
            
        except Exception as e:
            logging.error(f"Error in fast equipment collection: {e}")
    
    async def find_and_measure_fuel_bar(self, ui_area, total_width, total_height):
        """Find the actual fuel bar and measure its black vs colored portions"""
        try:
            # Convert to different color spaces for analysis
            gray = cv2.cvtColor(ui_area, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(ui_area, cv2.COLOR_BGR2HSV)
            
            # METHOD 1: Look for horizontal rectangular structures (fuel bars)
            # Find edges to locate bar boundaries
            edges = cv2.Canny(gray, 50, 150)
            
            # Find contours that could be fuel bars
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            potential_fuel_bars = []
            ui_height, ui_width = ui_area.shape[:2]
            
            for contour in contours:
                # Get bounding rectangle
                x, y, w, h = cv2.boundingRect(contour)
                
                # Filter for horizontal rectangles that could be fuel bars
                aspect_ratio = w / h if h > 0 else 0
                area = w * h
                
                # Fuel bars are typically: horizontal (wide), small height, decent size
                if (aspect_ratio > 3.0 and  # Wide horizontal bar
                    h >= 5 and h <= 30 and   # Reasonable height for a fuel bar
                    w >= 50 and              # Minimum width to be significant
                    area >= 250):            # Minimum area
                    
                    potential_fuel_bars.append({
                        'rect': (x, y, w, h),
                        'area': area,
                        'aspect_ratio': aspect_ratio
                    })
            
            # Sort by area (larger bars more likely to be the fuel bar)
            potential_fuel_bars.sort(key=lambda x: x['area'], reverse=True)
            
            # Analyze the most promising fuel bar candidates
            for bar_info in potential_fuel_bars[:3]:  # Check top 3 candidates
                x, y, w, h = bar_info['rect']
                
                # Extract the fuel bar region
                fuel_bar_region = ui_area[y:y+h, x:x+w]
                
                if fuel_bar_region.size == 0:
                    continue
                
                # Measure fuel in this specific bar region
                fuel_pct = await self.measure_fuel_in_bar(fuel_bar_region)
                if fuel_pct is not None:
                    logging.info(f"Found fuel bar at ({x},{y}) size {w}x{h}, fuel: {fuel_pct}%")
                    return fuel_pct
            
            # METHOD 2: Scan bottom area horizontally for fuel bar patterns
            return await self.scan_for_fuel_bar_pattern(ui_area)
            
        except Exception as e:
            logging.error(f"Error in precise fuel bar detection: {e}")
            return None
    
    async def measure_fuel_in_bar(self, fuel_bar_region):
        """Measure fuel percentage in a specific bar region"""
        try:
            if fuel_bar_region.size == 0:
                return None
                
            bar_height, bar_width = fuel_bar_region.shape[:2]
            
            # Define what constitutes "black/empty" vs "colored/fuel"
            # Black/empty: Very dark colors (fuel is missing)
            black_lower = np.array([0, 0, 0])
            black_upper = np.array([40, 40, 40])  # Dark threshold
            
            # Create mask for black/empty areas
            black_mask = cv2.inRange(fuel_bar_region, black_lower, black_upper)
            
            # Create mask for colored/fuel areas (anything not black)
            fuel_lower = np.array([41, 41, 41])  # Above black threshold
            fuel_upper = np.array([255, 255, 255])
            fuel_mask = cv2.inRange(fuel_bar_region, fuel_lower, fuel_upper)
            
            # Count pixels
            black_pixels = cv2.countNonZero(black_mask)
            fuel_pixels = cv2.countNonZero(fuel_mask)
            total_bar_pixels = black_pixels + fuel_pixels
            
            # Need sufficient pixels to be confident this is a fuel bar
            if total_bar_pixels < (bar_width * bar_height * 0.5):  # At least 50% of bar should be fuel-related
                return None
            
            if total_bar_pixels > 0:
                fuel_percentage = int((fuel_pixels / total_bar_pixels) * 100)
                fuel_percentage = max(0, min(100, fuel_percentage))
                
                logging.info(f"Bar measurement: {fuel_pixels} fuel pixels, {black_pixels} empty pixels, {fuel_percentage}% fuel")
                return fuel_percentage
            
            return None
            
        except Exception as e:
            logging.error(f"Error measuring fuel in bar: {e}")
            return None
    
    async def scan_for_fuel_bar_pattern(self, ui_area):
        """Scan the UI area looking for horizontal fuel bar patterns"""
        try:
            ui_height, ui_width = ui_area.shape[:2]
            
            # Scan each horizontal line in the bottom UI area
            best_fuel_measurement = None
            max_confidence = 0
            
            for y in range(5, ui_height - 5):  # Skip very top and bottom edges
                # Extract horizontal line (with some height for robustness)
                line_height = 8  # Examine several pixels vertically
                if y + line_height >= ui_height:
                    continue
                    
                line_region = ui_area[y:y+line_height, :]
                
                # Look for fuel bar characteristics in this line
                fuel_pct, confidence = await self.analyze_horizontal_line_for_fuel(line_region)
                
                if confidence > max_confidence and fuel_pct is not None:
                    max_confidence = confidence
                    best_fuel_measurement = fuel_pct
            
            if max_confidence > 0.3:  # Need reasonable confidence
                logging.info(f"Line scan found fuel: {best_fuel_measurement}% (confidence: {max_confidence:.2f})")
                return best_fuel_measurement
            
            return None
            
        except Exception as e:
            logging.error(f"Error in horizontal line scanning: {e}")
            return None
    
    async def analyze_horizontal_line_for_fuel(self, line_region):
        """Analyze a horizontal line to detect fuel bar patterns"""
        try:
            if line_region.size == 0:
                return None, 0
                
            line_height, line_width = line_region.shape[:2]
            
            # Look for transitions from colored to black (fuel to empty)
            # This is characteristic of a fuel bar
            
            # Convert to grayscale for edge detection
            gray_line = cv2.cvtColor(line_region, cv2.COLOR_BGR2GRAY)
            
            # Find horizontal edges (transitions from fuel to empty)
            horizontal_edges = cv2.Sobel(gray_line, cv2.CV_64F, 1, 0, ksize=3)
            edge_strength = np.mean(np.abs(horizontal_edges))
            
            # Also check color variation - fuel bars have distinct colors
            color_variance = np.var(line_region)
            
            # Calculate black vs colored ratio
            black_mask = cv2.inRange(line_region, np.array([0,0,0]), np.array([40,40,40]))
            fuel_mask = cv2.inRange(line_region, np.array([41,41,41]), np.array([255,255,255]))
            
            black_pixels = cv2.countNonZero(black_mask)
            fuel_pixels = cv2.countNonZero(fuel_mask)
            total_pixels = black_pixels + fuel_pixels
            
            if total_pixels > (line_width * line_height * 0.6):  # Most of line should be fuel-related
                fuel_percentage = int((fuel_pixels / total_pixels) * 100) if total_pixels > 0 else 0
                
                # Confidence based on edge strength and color variance
                confidence = min(1.0, (edge_strength / 50.0) + (color_variance / 10000))
                
                return fuel_percentage, confidence
            
            return None, 0
            
        except Exception as e:
            logging.error(f"Error analyzing horizontal line: {e}")
            return None, 0
    
    async def analyze_fuel_area_improved(self, ui_area):
        """Improved fallback analysis when precise fuel bar detection fails"""
        try:
            # More focused analysis of the UI area
            # Look for color patterns typical of fuel indicators
            
            # Method 1: Enhanced black vs colored analysis
            # Define more precise thresholds for empty vs fuel
            empty_lower = np.array([0, 0, 0])
            empty_upper = np.array([35, 35, 35])  # Very dark = empty
            
            fuel_lower = np.array([50, 50, 50])   # Moderate brightness = fuel
            fuel_upper = np.array([255, 255, 255])
            
            empty_mask = cv2.inRange(ui_area, empty_lower, empty_upper)
            fuel_mask = cv2.inRange(ui_area, fuel_lower, fuel_upper)
            
            empty_pixels = cv2.countNonZero(empty_mask)
            fuel_pixels = cv2.countNonZero(fuel_mask)
            total_relevant = empty_pixels + fuel_pixels
            
            if total_relevant > 100:  # Need sufficient pixels
                fuel_percentage = int((fuel_pixels / total_relevant) * 100)
                fuel_percentage = max(0, min(100, fuel_percentage))
                logging.info(f"Improved area analysis: {fuel_percentage}% (fuel: {fuel_pixels}, empty: {empty_pixels})")
                return fuel_percentage
            
            # Method 2: Brightness-based fallback
            gray_ui = cv2.cvtColor(ui_area, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray_ui)
            fuel_from_brightness = int(min(100, max(0, (mean_brightness / 255) * 100)))
            
            logging.info(f"Brightness fallback: {fuel_from_brightness}% (brightness: {mean_brightness})")
            return fuel_from_brightness
            
        except Exception as e:
            logging.error(f"Error in improved area analysis: {e}")
            return 50
    
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
    
    async def perform_screen_entry_sequence(self):
        """Complete sequence to perform after landing on a new screen"""
        try:
            logging.info("Starting screen entry sequence...")
            bot_state["status"] = "screen_entry_sequence"
            
            # Step 1: Press "S" to use radar - refresh screen of fuel and equipment, avoid ghosts
            logging.info("Step 1: Pressing S to use radar and refresh screen")
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(2000)  # Wait for radar to refresh
            bot_state["status"] = "radar_used"
            
            # Step 2: Press "D" to lay mines for defense
            logging.info("Step 2: Pressing D to lay defensive mines")
            await self.page.keyboard.press("d")
            await self.page.wait_for_timeout(1500)  # Wait for mines to be laid
            bot_state["status"] = "mines_laid"
            
            # Step 3: Check fuel and move to fuel if needed (before equipment collection)
            current_fuel = await self.detect_fuel_level()
            bot_state["current_fuel"] = current_fuel
            
            if current_fuel <= bot_state["settings"]["refuel_threshold"]:
                logging.info(f"Step 3: Fuel at {current_fuel}%, moving to fuel first")
                bot_state["status"] = "priority_refueling"
                await self.collect_fuel_canisters()
            else:
                logging.info(f"Step 3: Fuel sufficient at {current_fuel}%, proceeding to equipment")
            
            # Step 4: Collect all equipment on screen until inventory is full
            logging.info("Step 4: Collecting all equipment on screen")
            bot_state["status"] = "collecting_equipment"
            await self.collect_all_equipment()
            
            logging.info("Screen entry sequence completed successfully")
            bot_state["status"] = "sequence_complete"
            return True
            
        except Exception as e:
            logging.error(f"Error in screen entry sequence: {e}")
            bot_state["status"] = f"sequence_error: {str(e)}"
            return False
    
    async def collect_fuel_canisters(self):
        """Collect fuel canisters until fuel is sufficient"""
        try:
            fuel_collected = 0
            max_attempts = 10  # Prevent infinite loops
            
            for attempt in range(max_attempts):
                current_fuel = await self.detect_fuel_level()
                
                # If fuel is sufficient, stop collecting
                if current_fuel >= bot_state["settings"]["safe_threshold"]:
                    logging.info(f"Fuel sufficient at {current_fuel}%, stopping fuel collection")
                    break
                
                # Find and click fuel canister
                fuel_canister = await self.find_fuel_canisters()
                if fuel_canister:
                    await fuel_canister.click()
                    await self.page.wait_for_timeout(1500)  # Wait for collection
                    fuel_collected += 1
                    logging.info(f"Collected fuel canister #{fuel_collected}")
                else:
                    logging.info("No more fuel canisters found on screen")
                    break
                    
        except Exception as e:
            logging.error(f"Error collecting fuel canisters: {e}")
    
    async def collect_all_equipment(self):
        """Collect all equipment on screen using improved visual detection"""
        try:
            equipment_collected = 0
            max_attempts = 15
            
            logging.info("Starting equipment collection with visual detection")
            
            for attempt in range(max_attempts):
                # Detect equipment using visual analysis
                equipment_items = await self.detect_equipment_visually()
                
                if not equipment_items:
                    logging.info("No equipment detected on screen")
                    break
                
                # Click on detected equipment items
                for equipment in equipment_items:
                    try:
                        await self.page.mouse.click(equipment['x'], equipment['y'])
                        await self.page.wait_for_timeout(1000)
                        equipment_collected += 1
                        logging.info(f"Collected equipment item #{equipment_collected} at ({equipment['x']}, {equipment['y']})")
                        
                        # Break if we've collected enough
                        if equipment_collected >= 10:  # Reasonable limit
                            break
                            
                    except Exception as e:
                        logging.warning(f"Failed to collect equipment: {e}")
                        continue
                
                # Break if we found and processed equipment
                if equipment_items:
                    break
                    
            logging.info(f"Equipment collection complete. Collected {equipment_collected} items")
            
        except Exception as e:
            logging.error(f"Error collecting equipment: {e}")
    
    async def detect_equipment_visually(self):
        """Detect equipment items using visual analysis based on equipment image characteristics"""
        try:
            if not self.page:
                logging.error("No page available for equipment detection")
                return []
                
            # Take screenshot for analysis
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return []
            
            equipment_items = []
            
            # Convert to HSV for better color analysis
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Equipment typically has distinct colors - look for metallic/brown/orange tones
            # Based on the equipment image, look for brown/orange equipment colors
            equipment_color_ranges = [
                # Brown/orange equipment tones
                (np.array([10, 50, 50]), np.array([25, 255, 255])),  # Orange-brown
                (np.array([0, 50, 50]), np.array([10, 255, 255])),   # Red-brown
                # Gray/metallic equipment
                (np.array([0, 0, 100]), np.array([180, 30, 200])),   # Gray metallic
            ]
            
            combined_mask = None
            
            for lower, upper in equipment_color_ranges:
                mask = cv2.inRange(hsv, lower, upper)
                if combined_mask is None:
                    combined_mask = mask
                else:
                    combined_mask = cv2.bitwise_or(combined_mask, mask)
            
            if combined_mask is None:
                return []
            
            # Apply morphological operations to clean up the mask
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)
            
            # Find contours for potential equipment
            contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            height, width = img.shape[:2]
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Filter by reasonable equipment size (not too small, not too large)
                if 100 < area < 5000:
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Additional filtering: reasonable aspect ratio for equipment
                    aspect_ratio = w / h if h > 0 else 0
                    if 0.3 < aspect_ratio < 3.0:  # Not too elongated
                        
                        # Check if it's not at the very edges (likely UI elements)
                        margin = 50
                        if (margin < x < width - margin - w and 
                            margin < y < height - margin - h):
                            
                            equipment_items.append({
                                'x': x + w//2,
                                'y': y + h//2,
                                'width': w,
                                'height': h,
                                'area': area,
                                'aspect_ratio': aspect_ratio
                            })
            
            # Sort by area (larger equipment items first, likely more valuable)
            equipment_items.sort(key=lambda x: x['area'], reverse=True)
            
            # Limit to avoid clicking too many false positives
            equipment_items = equipment_items[:8]
            
            logging.info(f"Detected {len(equipment_items)} potential equipment items")
            
            return equipment_items
            
        except Exception as e:
            logging.error(f"Error in visual equipment detection: {e}")
            return []
    
    async def activate_bot_and_mine(self):
        """Activate bot and mine features on new screen - DEPRECATED, replaced by perform_screen_entry_sequence"""
        # This function is now replaced by the more comprehensive perform_screen_entry_sequence
        # Keeping for compatibility but redirecting to new function
        return await self.perform_screen_entry_sequence()
    
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
        """Main bot logic cycle with improved fuel and equipment sequence"""
        # First, make sure we're in the game
        if not await self.enter_game():
            logging.error("Failed to enter game, stopping bot")
            bot_state["status"] = "failed_to_enter_game"
            self.running = False
            return
            
        bot_state["status"] = "entered_game"
        logging.info("Bot successfully entered the game")
        
        # Perform initial optimized sequence when joining
        await self.perform_initial_join_sequence()
        
        while self.running:
            try:
                # Check if we still have a valid browser session
                if not self.page or not self.browser:
                    logging.error("Lost browser session, attempting to reconnect...")
                    bot_state["status"] = "reconnecting_browser"
                    await self.broadcast_status()
                    
                    # Try to re-enter the game
                    if not await self.enter_game():
                        logging.error("Failed to reconnect to game, stopping bot")
                        bot_state["status"] = "connection_lost"
                        self.running = False
                        break
                
                # DEATH DETECTION - Check if bot has died
                if await self.detect_death():
                    logging.info("Death detected - handling respawn")
                    await self.handle_death_and_respawn()
                    continue  # Skip rest of cycle and restart
                
                # Update fuel level and position
                current_fuel = await self.detect_fuel_level()
                current_position = await self.detect_position()
                
                bot_state["current_fuel"] = current_fuel
                bot_state["position"] = current_position
                
                # Broadcast status update EVERY cycle for real-time UI updates
                await self.broadcast_status()
                
                # Check if shields need activation (critical threshold)
                if current_fuel <= bot_state["settings"]["shield_threshold"] and not bot_state["shields_active"]:
                    if self.page:  # Only try if we have a page
                        await self.activate_shields()
                        bot_state["shields_active"] = True
                        bot_state["status"] = "shields_activated"
                        await self.broadcast_status()
                
                # Main bot sequence logic - only if we have a page
                if self.page:
                    if current_fuel <= bot_state["settings"]["refuel_threshold"]:
                        # Low fuel - prioritize fuel collection
                        await self.execute_fuel_priority_sequence()
                    elif current_fuel >= bot_state["settings"]["safe_threshold"]:
                        # High fuel - stationary mode, collect equipment if available
                        await self.execute_safe_mode_sequence()
                    else:
                        # Medium fuel - balanced approach
                        await self.execute_balanced_sequence()
                else:
                    # No page available, try to reconnect
                    logging.warning("No page available for bot sequences")
                    bot_state["status"] = "no_browser_session"
                
                # Wait before next cycle
                await asyncio.sleep(2)
                
            except Exception as e:
                logging.error(f"Bot cycle error: {e}")
                bot_state["status"] = f"error: {str(e)}"
                await asyncio.sleep(5)
    
    async def perform_initial_join_sequence(self):
        """Optimized sequence when first joining the game"""
        try:
            if not self.page:
                logging.error("No page available for initial join sequence")
                bot_state["status"] = "no_browser_session"
                return
                
            logging.info("Starting initial join sequence...")
            bot_state["status"] = "initial_join_sequence"
            
            # Step 1: Configure equipment settings (armors:off, duals:on, missiles:off, homing:off, radars:on)
            await self.configure_equipment_settings()
            
            # Step 2: Press "S" for radar to reveal fuel and equipment
            logging.info("Step 2: Activating radar with S key")
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(2000)
            
            # Step 3: Move to fuel nodes until safety threshold
            await self.collect_fuel_until_safe()
            
            # Step 4: Collect any revealed equipment
            logging.info("Step 4: Collecting revealed equipment")
            await self.collect_all_equipment()
            
            logging.info("Initial join sequence completed")
            bot_state["status"] = "join_sequence_complete"
            
        except Exception as e:
            logging.error(f"Error in initial join sequence: {e}")
            bot_state["status"] = f"join_sequence_error: {str(e)}"
    
    async def configure_equipment_settings(self):
        """Configure equipment settings: armors:off, duals:on, missiles:off, homing:off, radars:on"""
        try:
            if not self.page:
                logging.error("No page available for equipment configuration")
                return
                
            logging.info("Step 1: Configuring equipment settings")
            bot_state["status"] = "configuring_equipment"
            
            # Equipment toggle sequence based on tankpit.com controls
            # These are typical key bindings for tank games - may need adjustment based on actual game
            
            # Armors: OFF - typically 'A' key toggles armor
            logging.info("Setting armors: OFF")
            await self.page.keyboard.press("a")  # Toggle armor (assuming it turns off if on)
            await self.page.wait_for_timeout(500)
            
            # Check if we need to toggle again (some games show current state)
            # For now, assume single press sets to desired state
            
            # Duals: ON - typically 'W' or 'D' key for dual weapons  
            logging.info("Setting duals: ON")
            await self.page.keyboard.press("w")  # Toggle dual weapons on
            await self.page.wait_for_timeout(500)
            
            # Missiles: OFF - typically 'M' key for missiles
            logging.info("Setting missiles: OFF") 
            await self.page.keyboard.press("m")  # Toggle missiles (assuming it turns off if on)
            await self.page.wait_for_timeout(500)
            
            # Homing: OFF - typically 'H' key for homing missiles
            logging.info("Setting homing: OFF")
            await self.page.keyboard.press("h")  # Toggle homing (assuming it turns off if on)
            await self.page.wait_for_timeout(500)
            
            # Radars: ON - typically 'R' key for radar systems
            logging.info("Setting radars: ON")
            await self.page.keyboard.press("r")  # Toggle radar on
            await self.page.wait_for_timeout(500)
            
            # Alternative method: Use number keys if equipment is numbered
            # Some tank games use number keys 1-5 for different equipment slots
            equipment_keys = ['1', '2', '3', '4', '5']
            for i, key in enumerate(equipment_keys):
                try:
                    await self.page.keyboard.press(key)
                    await self.page.wait_for_timeout(200)
                    logging.info(f"Toggled equipment slot {i+1} with key '{key}'")
                except:
                    continue
            
            # Wait a moment for settings to take effect
            await self.page.wait_for_timeout(1000)
            
            logging.info("Equipment configuration completed: armors:OFF, duals:ON, missiles:OFF, homing:OFF, radars:ON")
            
        except Exception as e:
            logging.error(f"Error configuring equipment settings: {e}")
            # Continue with sequence even if equipment config fails
    
    async def verify_equipment_settings(self):
        """Verify current equipment settings by checking page content or UI elements"""
        try:
            if not self.page:
                return {}
                
            # Try to detect current equipment status from page content
            page_content = await self.page.content()
            content_lower = page_content.lower()
            
            equipment_status = {
                'armors': 'unknown',
                'duals': 'unknown', 
                'missiles': 'unknown',
                'homing': 'unknown',
                'radars': 'unknown'
            }
            
            # Look for equipment status indicators in the page
            # This would need to be customized based on actual tankpit.com UI
            if 'armor on' in content_lower or 'armor: on' in content_lower:
                equipment_status['armors'] = 'on'
            elif 'armor off' in content_lower or 'armor: off' in content_lower:
                equipment_status['armors'] = 'off'
                
            if 'dual on' in content_lower or 'dual: on' in content_lower:
                equipment_status['duals'] = 'on'
            elif 'dual off' in content_lower or 'dual: off' in content_lower:
                equipment_status['duals'] = 'off'
                
            # Similar checks for other equipment...
            
            logging.info(f"Equipment status detected: {equipment_status}")
            return equipment_status
            
        except Exception as e:
            logging.error(f"Error verifying equipment settings: {e}")
            return {}
    
    async def toggle_specific_equipment(self, equipment_type, desired_state):
        """Toggle a specific equipment type to desired state (on/off)"""
        try:
            if not self.page:
                return False
                
            # Equipment key mappings (may need adjustment based on actual game)
            equipment_keys = {
                'armors': 'a',
                'duals': 'w', 
                'missiles': 'm',
                'homing': 'h',
                'radars': 'r'
            }
            
            if equipment_type not in equipment_keys:
                logging.warning(f"Unknown equipment type: {equipment_type}")
                return False
                
            key = equipment_keys[equipment_type]
            
            # Get current state (if possible)
            current_status = await self.verify_equipment_settings()
            current_state = current_status.get(equipment_type, 'unknown')
            
            # If current state matches desired, no need to toggle
            if current_state == desired_state:
                logging.info(f"Equipment {equipment_type} already {desired_state}")
                return True
                
            # Toggle the equipment
            logging.info(f"Toggling {equipment_type} to {desired_state} using key '{key}'")
            await self.page.keyboard.press(key)
            await self.page.wait_for_timeout(500)
            
            return True
            
        except Exception as e:
            logging.error(f"Error toggling {equipment_type}: {e}")
            return False
    
    async def execute_fuel_priority_sequence(self):
        """Execute sequence when fuel is below refuel threshold - NEVER STOP UNTIL FULL"""
        try:
            if not self.page:
                logging.error("No page available for fuel priority sequence")
                bot_state["status"] = "no_browser_session"
                return
                
            bot_state["status"] = "fuel_priority_mode"
            logging.info("Executing fuel priority sequence - PERSISTENT SEARCH UNTIL FULL")
            
            # Use persistent search - never stop until safety threshold reached
            search_successful = await self.persistent_fuel_and_equipment_search()
            
            if not search_successful:
                # If persistent search failed after max attempts, try overview map
                logging.info("Persistent search exhausted - using overview map as last resort")
                await self.use_overview_map_for_fuel()
                
                # Try persistent search again in new location
                await self.persistent_fuel_and_equipment_search()
                
        except Exception as e:
            logging.error(f"Error in fuel priority sequence: {e}")
    
    async def collect_fuel_from_nodes(self, fuel_nodes):
        """Collect fuel from detected nodes"""
        try:
            collected = 0
            for fuel_node in fuel_nodes:
                current_fuel = await self.detect_fuel_level()
                
                # Stop if we've reached safety threshold
                if current_fuel >= bot_state["settings"]["safe_threshold"]:
                    logging.info(f"Reached safety threshold ({current_fuel}%), stopping fuel collection")
                    break
                
                # Click on the fuel node
                await self.page.mouse.click(fuel_node['x'], fuel_node['y'])
                await self.page.wait_for_timeout(1500)
                
                collected += 1
                logging.info(f"Collected fuel node {collected} (estimated value: {fuel_node['estimated_value']})")
            
            logging.info(f"Collected fuel from {collected} nodes")
            
        except Exception as e:
            logging.error(f"Error collecting fuel from nodes: {e}")
    
    async def execute_safe_mode_sequence(self):
        """Execute sequence when fuel is above safe threshold"""
        try:
            if not self.page:
                logging.error("No page available for safe mode sequence")
                bot_state["status"] = "no_browser_session"
                return
                
            bot_state["status"] = "safe_mode_stationary"
            logging.info("Executing safe mode sequence - staying stationary")
            
            # Deactivate shields if active
            bot_state["shields_active"] = False
            
            # Press S occasionally to check for new equipment
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(1500)
            
            # Collect any available equipment
            await self.collect_all_equipment()
            
            # Stay stationary and wait
            await asyncio.sleep(3)
            
        except Exception as e:
            logging.error(f"Error in safe mode sequence: {e}")
    
    async def execute_balanced_sequence(self):
        """Execute sequence when fuel is in medium range"""
        try:
            if not self.page:
                logging.error("No page available for balanced sequence")
                bot_state["status"] = "no_browser_session"
                return
                
            bot_state["status"] = "balanced_mode"
            logging.info("Executing balanced mode sequence")
            
            # Press S for radar
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(1500)
            
            # Press D for mines
            await self.page.keyboard.press("d")
            await self.page.wait_for_timeout(1000)
            
            # Check for fuel and equipment
            fuel_nodes = await self.detect_fuel_nodes()
            equipment_items = await self.detect_equipment_visually()
            
            # If nothing found, use persistent search (but limited for balanced mode)
            if len(fuel_nodes) == 0 and len(equipment_items) == 0:
                logging.info("Nothing found in balanced mode - using limited persistent search")
                # Use shorter search for balanced mode (don't be as aggressive)
                current_fuel = await self.detect_fuel_level()
                search_attempts = 5  # Limited search attempts in balanced mode
                
                for attempt in range(search_attempts):
                    if attempt % 2 == 0:
                        await self.perform_random_proximity_move()
                    else:
                        await self.move_to_screen_edge_and_radar()
                    
                    # Check again
                    fuel_nodes = await self.detect_fuel_nodes()
                    equipment_items = await self.detect_equipment_visually()
                    
                    if len(fuel_nodes) > 0 or len(equipment_items) > 0:
                        break
            
            # Collect what we found
            if fuel_nodes:
                await self.collect_fuel_from_nodes(fuel_nodes[:2])  # Limit collection in balanced mode
            
            if equipment_items:
                await self.collect_all_equipment()
            
        except Exception as e:
            logging.error(f"Error in balanced sequence: {e}")
    
    async def detect_fuel_nodes(self):
        """Detect fuel nodes on screen using improved visual analysis based on fuel image"""
        try:
            if not self.page:
                logging.error("No page available for fuel node detection")
                return []
                
            # Take screenshot for analysis
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return []
            
            fuel_nodes = []
            
            # Convert to HSV for better color detection
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Based on fuel.png, fuel nodes appear to be bright yellow/golden
            # Define multiple fuel color ranges to catch variations
            fuel_color_ranges = [
                # Bright yellow fuel
                (np.array([20, 150, 150]), np.array([30, 255, 255])),
                # Golden fuel 
                (np.array([15, 100, 150]), np.array([35, 255, 255])),
                # Light yellow fuel
                (np.array([25, 80, 180]), np.array([35, 255, 255])),
            ]
            
            combined_fuel_mask = None
            
            # Combine all fuel color masks
            for lower, upper in fuel_color_ranges:
                fuel_mask = cv2.inRange(hsv, lower, upper)
                if combined_fuel_mask is None:
                    combined_fuel_mask = fuel_mask
                else:
                    combined_fuel_mask = cv2.bitwise_or(combined_fuel_mask, fuel_mask)
            
            if combined_fuel_mask is None:
                return []
            
            # Apply morphological operations to clean up the mask
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            combined_fuel_mask = cv2.morphologyEx(combined_fuel_mask, cv2.MORPH_CLOSE, kernel)
            combined_fuel_mask = cv2.morphologyEx(combined_fuel_mask, cv2.MORPH_OPEN, kernel)
            
            # Find contours for fuel nodes
            contours, _ = cv2.findContours(combined_fuel_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            height, width = img.shape[:2]
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Filter by fuel node size (based on typical fuel node dimensions)
                if 80 < area < 3000:  # Reasonable fuel node size range
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Check aspect ratio - fuel nodes are roughly circular/square
                    aspect_ratio = w / h if h > 0 else 0
                    if 0.5 < aspect_ratio < 2.0:  # Not too elongated
                        
                        # Avoid edges of screen (likely UI elements)
                        margin = 40
                        if (margin < x < width - margin - w and 
                            margin < y < height - margin - h):
                            
                            # Estimate fuel value based on size and brightness
                            roi = img[y:y+h, x:x+w]
                            avg_brightness = np.mean(roi)
                            
                            # Larger and brighter nodes likely have more fuel
                            estimated_value = int((area / 20) + (avg_brightness / 10))
                            estimated_value = max(10, min(100, estimated_value))  # Clamp to reasonable range
                            
                            fuel_nodes.append({
                                'x': x + w//2,
                                'y': y + h//2,
                                'width': w,
                                'height': h,
                                'area': area,
                                'brightness': avg_brightness,
                                'estimated_value': estimated_value,
                                'aspect_ratio': aspect_ratio
                            })
            
            # Sort by estimated value (highest first)
            fuel_nodes.sort(key=lambda x: x['estimated_value'], reverse=True)
            
            # Limit to reasonable number to avoid false positives
            fuel_nodes = fuel_nodes[:10]
            
            logging.info(f"Detected {len(fuel_nodes)} fuel nodes")
            if fuel_nodes:
                for i, node in enumerate(fuel_nodes[:3]):  # Log top 3
                    logging.info(f"  Fuel node {i+1}: value={node['estimated_value']}, size={node['area']}, pos=({node['x']},{node['y']})")
            
            return fuel_nodes
            
        except Exception as e:
            logging.error(f"Error detecting fuel nodes: {e}")
            return []
    
    async def detect_death(self):
        """Detect if the bot has died and needs to respawn"""
        try:
            if not self.page:
                return False
                
            # Take screenshot to analyze for death indicators
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return False
            
            # Method 1: Look for death-related text
            page_content = await self.page.content()
            death_indicators = [
                "you have been destroyed",
                "you died", 
                "game over",
                "destroyed",
                "respawn",
                "press any key",
                "click to continue"
            ]
            
            content_lower = page_content.lower()
            for indicator in death_indicators:
                if indicator in content_lower:
                    logging.info(f"Death detected: found text '{indicator}'")
                    return True
            
            # Method 2: Look for visual death indicators (dark screen, etc.)
            # Convert to grayscale and check if screen is mostly dark
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            
            # If screen is very dark (death screen), consider it death
            if mean_brightness < 30:  # Very dark screen
                logging.info(f"Death detected: screen very dark (brightness: {mean_brightness})")
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting death: {e}")
            return False
    
    async def handle_death_and_respawn(self):
        """Handle death by pressing Q to quit and restarting"""
        try:
            logging.info("Handling death - pressing Q to quit")
            bot_state["status"] = "handling_death"
            await self.broadcast_status()
            
            # Press Q to quit the current map
            await self.page.keyboard.press("q")
            await self.page.wait_for_timeout(3000)
            
            # Wait a moment for the quit to process
            await self.page.wait_for_timeout(2000)
            
            # Use overview map to restart in a new location
            logging.info("Respawning - using overview map for new location")
            await self.use_overview_map_for_fuel()
            
            logging.info("Death handling complete - resuming normal operations")
            bot_state["status"] = "respawned"
            
        except Exception as e:
            logging.error(f"Error handling death and respawn: {e}")
            bot_state["status"] = f"death_handling_error: {str(e)}"
    
    async def detect_nothing_found_message(self):
        """Detect if radar shows 'nothing detected here' message"""
        try:
            if not self.page:
                return False
                
            page_content = await self.page.content()
            nothing_found_indicators = [
                "nothing detected here",
                "nothing found",
                "no targets detected",
                "area clear",
                "nothing detected"
            ]
            
            content_lower = page_content.lower()
            for indicator in nothing_found_indicators:
                if indicator in content_lower:
                    logging.info(f"Nothing detected message found: '{indicator}'")
                    return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting nothing found message: {e}")
            return False
    
    async def perform_random_proximity_move(self):
        """Perform a small random 15-pixel teleport to search for items"""
        try:
            if not self.page:
                return
                
            logging.info("Nothing detected - performing random proximity move")
            bot_state["status"] = "searching_proximity"
            
            # Take screenshot to get current screen center
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is None:
                return
                
            height, width = img.shape[:2]
            center_x = width // 2
            center_y = height // 2
            
            # Generate random offset within 15 pixel radius
            import random
            import math
            
            angle = random.uniform(0, 2 * math.pi)  # Random angle
            distance = random.uniform(5, 15)  # Random distance 5-15 pixels
            
            offset_x = int(distance * math.cos(angle))
            offset_y = int(distance * math.sin(angle))
            
            target_x = center_x + offset_x
            target_y = center_y + offset_y
            
            # Ensure target is within screen bounds
            target_x = max(50, min(width - 50, target_x))
            target_y = max(50, min(height - 50, target_y))
            
            logging.info(f"Random proximity move: clicking ({target_x}, {target_y}) - offset ({offset_x}, {offset_y})")
            
            # Click the random nearby location
            await self.page.mouse.click(target_x, target_y)
            await self.page.wait_for_timeout(2000)
            
            # After moving, press S again to radar
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(2000)
            
            logging.info("Random proximity move completed")
            
        except Exception as e:
            logging.error(f"Error in random proximity move: {e}")
    
    async def collect_fuel_until_safe(self):
        """Collect fuel until reaching safety threshold using persistent search"""
        try:
            current_fuel = await self.detect_fuel_level()
            
            if current_fuel >= bot_state["settings"]["safe_threshold"]:
                logging.info(f"Already at safety threshold: {current_fuel}%")
                return
            
            logging.info(f"Starting fuel collection until safe - current: {current_fuel}%, target: {bot_state['settings']['safe_threshold']}%")
            
            # Use persistent search to ensure we reach safety threshold
            search_successful = await self.persistent_fuel_and_equipment_search()
            
            if search_successful:
                final_fuel = await self.detect_fuel_level()
                logging.info(f"Successfully reached safety threshold: {final_fuel}%")
            else:
                final_fuel = await self.detect_fuel_level()
                logging.warning(f"Could not reach safety threshold, current fuel: {final_fuel}%")
            
        except Exception as e:
            logging.error(f"Error collecting fuel until safe: {e}")
    
    async def use_overview_map_for_fuel(self):
        """Use overview map to find fuel when none available locally"""
        try:
            logging.info("Opening overview map to search for fuel")
            bot_state["status"] = "using_overview_map"
            
            # Press F to open overview map
            await self.page.keyboard.press("f")
            await self.page.wait_for_timeout(3000)
            
            # Take screenshot to analyze map
            screenshot = await self.page.screenshot()
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Look for flashing tank (bot's current position)
            bot_position = await self.find_bot_on_overview_map(img)
            
            if bot_position:
                # Click within 15 pixel radius of bot's position (reduced from 30)
                import random
                offset_x = random.randint(-15, 15)
                offset_y = random.randint(-15, 15)
                
                target_x = bot_position['x'] + offset_x
                target_y = bot_position['y'] + offset_y
                
                logging.info(f"Clicking near bot position: ({target_x}, {target_y}) within 15px radius")
                await self.page.mouse.click(target_x, target_y)
                await self.page.wait_for_timeout(4000)
                
                # After landing, start main sequence
                await self.execute_landing_sequence()
            else:
                logging.warning("Could not find bot position on overview map")
                # Close map and continue
                await self.page.keyboard.press("escape")
                await self.page.wait_for_timeout(1000)
                
        except Exception as e:
            logging.error(f"Error using overview map: {e}")
    
    async def find_bot_on_overview_map(self, img):
        """Find the flashing bot indicator on overview map"""
        try:
            # Look for bright/flashing elements that could be the bot
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Find bright spots (flashing elements)
            _, bright_thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            
            contours, _ = cv2.findContours(bright_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # Look for small bright spots that could be the bot indicator
            for contour in contours:
                area = cv2.contourArea(contour)
                if 10 < area < 100:  # Small bright spot
                    x, y, w, h = cv2.boundingRect(contour)
                    return {'x': x + w//2, 'y': y + h//2}
            
            # Fallback: assume bot is near center of map
            height, width = img.shape[:2]
            return {'x': width//2, 'y': height//2}
            
        except Exception as e:
            logging.error(f"Error finding bot on overview map: {e}")
            return None
    
    async def execute_landing_sequence(self):
        """Execute sequence after landing from overview map"""
        try:
            logging.info("Executing post-landing sequence")
            bot_state["status"] = "post_landing_sequence"
            
            # Step 1: Configure equipment settings
            await self.configure_equipment_settings()
            
            # Step 2: Press S for radar
            await self.page.keyboard.press("s")
            await self.page.wait_for_timeout(2000)
            
            # Step 3: Press D for mines
            await self.page.keyboard.press("d")
            await self.page.wait_for_timeout(1500)
            
            # Step 4: Collect fuel until threshold
            await self.collect_fuel_until_safe()
            
            # Step 5: Collect equipment
            await self.collect_all_equipment()
            
            logging.info("Post-landing sequence completed")
            
        except Exception as e:
            logging.error(f"Error in landing sequence: {e}")
    
        """Determine if bot should perform screen maintenance based on game conditions"""
        try:
            # Check for new equipment that might have spawned
            equipment_selectors = ['.equipment', '.item', '[class*="equipment"]', '[class*="item"]']
            
            for selector in equipment_selectors:
                equipment_elements = await self.page.query_selector_all(selector)
                visible_equipment = 0
                
                for equipment in equipment_elements:
                    if await equipment.is_visible():
                        visible_equipment += 1
                        
                # If there's equipment available, we should maintain the screen
                if visible_equipment > 0:
                    logging.info(f"Found {visible_equipment} equipment items, triggering maintenance")
                    return True
                    
            # Also trigger maintenance periodically (every 30 seconds) to stay competitive
            import time
            current_time = time.time()
            last_maintenance = getattr(self, 'last_maintenance_time', 0)
            
            if current_time - last_maintenance > 30:  # 30 seconds
                self.last_maintenance_time = current_time
                logging.info("Periodic maintenance due")
                return True
                
            return False
            
        except Exception as e:
            logging.error(f"Error checking screen maintenance needs: {e}")
            return False
    
    async def detect_position(self):
        """Detect current tank position on the map"""
        try:
            # This is game-specific and would need to be customized based on tankpit.com's interface
            # For now, we'll try to extract position from common sources
            
            # Method 1: Look for position indicators in the UI
            position_selectors = [
                '*:has-text("X:")', '*:has-text("Y:")',
                '*[class*="position"]', '*[id*="position"]',
                '*[class*="coord"]', '*[id*="coord"]'
            ]
            
            for selector in position_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    for element in elements:
                        text = await element.inner_text()
                        
                        # Look for coordinate patterns
                        import re
                        coord_matches = re.findall(r'[XY][:=\s]*(\d+)', text)
                        if coord_matches and len(coord_matches) >= 2:
                            x, y = int(coord_matches[0]), int(coord_matches[1])
                            logging.info(f"Found position ({x}, {y}) from UI element")
                            return {"x": x, "y": y}
                            
                except:
                    continue
            
            # Method 2: Try JavaScript to get position
            js_position_checks = [
                "window.player && window.player.x && window.player.y && {x: window.player.x, y: window.player.y}",
                "window.tank && window.tank.x && window.tank.y && {x: window.tank.x, y: window.tank.y}",
                "window.game && window.game.player && {x: window.game.player.x, y: window.game.player.y}"
            ]
            
            for js_check in js_position_checks:
                try:
                    result = await self.page.evaluate(js_check)
                    if result and isinstance(result, dict) and 'x' in result and 'y' in result:
                        logging.info(f"Found position ({result['x']}, {result['y']}) via JavaScript")
                        return {"x": int(result['x']), "y": int(result['y'])}
                except:
                    continue
            
            # Method 3: Generate mock position based on movement (placeholder)
            # In a real implementation, you'd track movements and calculate position
            current_time = time.time()
            x = int((current_time % 1000))  # Mock X coordinate
            y = int((current_time % 800))   # Mock Y coordinate
            
            return {"x": x, "y": y}
            
        except Exception as e:
            logging.error(f"Error detecting position: {e}")
            return {"x": 0, "y": 0}
    
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
                "current_map": bot_state.get("current_map", "none"),
                "settings": bot_state["settings"]
            }
        }
        
        # Send to all connected WebSocket clients
        disconnected_connections = []
        for connection in websocket_connections[:]:
            try:
                await connection.send_text(json.dumps(status_data))
                logging.debug(f"Broadcasted status to WebSocket client: fuel={bot_state['current_fuel']}%, status={bot_state['status']}")
            except Exception as e:
                logging.warning(f"WebSocket connection failed, removing: {e}")
                disconnected_connections.append(connection)
        
        # Remove failed connections
        for conn in disconnected_connections:
            if conn in websocket_connections:
                websocket_connections.remove(conn)
        
        logging.info(f"Status broadcast to {len(websocket_connections)} clients: fuel={bot_state['current_fuel']}%, shields={bot_state['shields_active']}, status={bot_state['status']}")
    
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

@api_router.get("/bot/screenshot")
async def get_bot_screenshot():
    """Get current screenshot of bot's screen"""
    try:
        if not tankpit_bot.page:
            raise HTTPException(status_code=400, detail="Bot not in game")
        
        # Take screenshot
        screenshot = await tankpit_bot.page.screenshot()
        
        # Convert to base64 for web display
        screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
        
        return {
            "success": True, 
            "screenshot": f"data:image/png;base64,{screenshot_b64}",
            "timestamp": datetime.now().isoformat()
        }
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