# TankPit Bot Controller

An automated fuel management bot for the web-based game www.tankpit.com with real-time control interface.

## Features

### 🎮 Game Automation
- **Automated Login**: Secure login to tankpit.com accounts
- **Tank Selection**: Choose from available tanks on your account
- **Smart Fuel Management**: Automated fuel monitoring and collection
- **Shield Activation**: Auto-activate shields at critical fuel levels (10%)
- **Map Navigation**: Intelligent fuel-dense area detection and navigation
- **Enemy Farming**: Allow enemies to attack for experience while maintaining fuel

### 🎛️ Real-time Control Panel
- **Live Status Monitoring**: Real-time fuel levels, position, and bot status
- **Customizable Settings**: Adjustable fuel thresholds via sliders
- **Activity Logging**: Live activity feed with timestamps
- **WebSocket Integration**: Real-time updates without page refresh

### ⚙️ Configurable Settings
- **Refuel Threshold**: When to start looking for fuel (default: 25%)
- **Shield Threshold**: When to activate shields (default: 10%)
- **Safe Threshold**: When to stop collecting fuel (default: 85%)
- **Target Player**: Optional player name to protect

## Bot Logic

### Fuel Management Cycle
1. **Screen Arrival**: Automatically activate "bot" and "mine" features
2. **Fuel Monitoring**: Continuously monitor fuel levels using computer vision
3. **Shield Activation**: At 10% fuel, activate shields (press "1" key)
4. **Fuel Collection**: At 25% fuel, click fuel canisters with highest fuel content
5. **Map Navigation**: When no fuel available and <35% fuel, open map and navigate to fuel-dense areas
6. **Stationary Mode**: At 85%+ fuel, remain stationary until refuel needed

## Usage Instructions

### 1. Initial Setup
1. Open the TankPit Bot Controller interface
2. Enter your tankpit.com username and password
3. Click "Login" to connect to the game

### 2. Tank Selection
1. After successful login, available tanks will be displayed
2. Click on a tank to select it
3. Tank information shows current fuel and position

### 3. Bot Configuration
1. Adjust fuel thresholds using the sliders
2. Optionally set a target player name to protect
3. Click "Update Settings" to save changes

### 4. Bot Operation
1. Click "Start Bot" to begin automated fuel management
2. Monitor real-time status and activity log
3. Click "Stop Bot" to halt automation

## Technical Stack
- **Backend**: FastAPI + Playwright + OpenCV
- **Frontend**: React + Tailwind CSS + WebSockets
- **Browser Automation**: Chromium with visible window
