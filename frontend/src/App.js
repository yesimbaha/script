import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import './App.css';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function App() {
  // Bot state
  const [botStatus, setBotStatus] = useState({
    running: false,
    current_fuel: 0,
    shields_active: false,
    position: { x: 0, y: 0 },
    status: 'idle',
    settings: {
      refuel_threshold: 25,
      shield_threshold: 10,
      safe_threshold: 85,
      target_player: '',
      username: '',
      password: ''
    }
  });

  // UI state
  const [loginData, setLoginData] = useState({ username: '', password: '' });
  const [availableTanks, setAvailableTanks] = useState([]);
  const [selectedTank, setSelectedTank] = useState('');
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [logs, setLogs] = useState([]);
  
  // Settings state
  const [settings, setSettings] = useState({
    refuel_threshold: 25,
    shield_threshold: 10,
    safe_threshold: 85,
    target_player: '',
    preferred_map: 'practice'
  });

  // Map state
  const [availableMaps, setAvailableMaps] = useState([]);

  // WebSocket connection
  const wsRef = useRef(null);

  // Initialize WebSocket connection
  useEffect(() => {
    const wsUrl = BACKEND_URL.replace('https://', 'wss://').replace('http://', 'ws://');
    
    const connectWebSocket = () => {
      wsRef.current = new WebSocket(`${wsUrl}/api/ws/bot-status`);
      
      wsRef.current.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'status_update') {
          setBotStatus(data.data);
          addLog(`Status: ${data.data.status} | Fuel: ${data.data.current_fuel}%`);
        } else if (data.type === 'ping') {
          // Handle ping - connection is alive
          console.log('WebSocket ping received');
        }
      };

      wsRef.current.onopen = () => {
        addLog('Connected to bot status feed');
      };

      wsRef.current.onclose = () => {
        addLog('Disconnected from bot status feed - attempting reconnect...');
        // Reconnect after 5 seconds
        setTimeout(connectWebSocket, 5000);
      };

      wsRef.current.onerror = (error) => {
        addLog('WebSocket error - connection lost');
        console.error('WebSocket error:', error);
      };
    };

    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  // Fetch initial bot status
  useEffect(() => {
    fetchBotStatus();
  }, []);

  const addLog = (message) => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs(prev => [...prev.slice(-49), `[${timestamp}] ${message}`]);
  };

  const fetchBotStatus = async () => {
    try {
      const response = await axios.get(`${API}/bot/status`);
      setBotStatus(response.data);
      setSettings({
        refuel_threshold: response.data.settings.refuel_threshold,
        shield_threshold: response.data.settings.shield_threshold,
        safe_threshold: response.data.settings.safe_threshold,
        target_player: response.data.settings.target_player,
        preferred_map: response.data.settings.preferred_map || 'practice'
      });
    } catch (error) {
      addLog(`Error fetching status: ${error.message}`);
    }
  };

  const handleLogin = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API}/bot/login`, loginData);
      if (response.data.success) {
        setIsLoggedIn(true);
        addLog('Successfully logged into tankpit.com');
        
        // Fetch available tanks
        const tanksResponse = await axios.get(`${API}/bot/tanks`);
        if (tanksResponse.data.success) {
          setAvailableTanks(tanksResponse.data.tanks);
          addLog(`Found ${tanksResponse.data.tanks.length} tanks`);
        }
        
        // Fetch available maps
        const mapsResponse = await axios.get(`${API}/bot/maps`);
        if (mapsResponse.data.success) {
          setAvailableMaps(mapsResponse.data.maps);
          addLog(`Found ${mapsResponse.data.maps.length} available maps`);
        }
      } else {
        addLog(`Login failed: ${response.data.message}`);
      }
    } catch (error) {
      addLog(`Login error: ${error.message}`);
    }
    setIsLoading(false);
  };

  const handleSelectTank = async (tankId) => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API}/bot/select-tank/${tankId}`);
      if (response.data.success) {
        setSelectedTank(tankId);
        addLog(`Selected tank: ${availableTanks.find(t => t.id === tankId)?.name}`);
      } else {
        addLog('Failed to select tank');
      }
    } catch (error) {
      addLog(`Tank selection error: ${error.message}`);
    }
    setIsLoading(false);
  };

  const handleStartBot = async () => {
    if (!selectedTank) {
      addLog('Please select a tank first');
      return;
    }
    
    setIsLoading(true);
    try {
      const response = await axios.post(`${API}/bot/start`);
      if (response.data.success) {
        addLog('Bot started successfully');
      } else {
        addLog('Failed to start bot');
      }
    } catch (error) {
      addLog(`Start bot error: ${error.message}`);
    }
    setIsLoading(false);
  };

  const handleStopBot = async () => {
    setIsLoading(true);
    try {
      const response = await axios.post(`${API}/bot/stop`);
      if (response.data.success) {
        addLog('Bot stopped successfully');
      } else {
        addLog('Failed to stop bot');
      }
    } catch (error) {
      addLog(`Stop bot error: ${error.message}`);
    }
    setIsLoading(false);
  };

  const handleSettingsUpdate = async () => {
    try {
      const response = await axios.post(`${API}/bot/settings`, settings);
      if (response.data.success) {
        addLog('Settings updated successfully');
      } else {
        addLog('Failed to update settings');
      }
    } catch (error) {
      addLog(`Settings update error: ${error.message}`);
    }
  };

  const getFuelBarColor = () => {
    if (botStatus.current_fuel <= settings.shield_threshold) return 'bg-red-500';
    if (botStatus.current_fuel <= settings.refuel_threshold) return 'bg-yellow-500';
    if (botStatus.current_fuel >= settings.safe_threshold) return 'bg-green-500';
    return 'bg-blue-500';
  };

  const getStatusColor = () => {
    if (botStatus.status.includes('error')) return 'text-red-500';
    if (botStatus.running) return 'text-green-500';
    return 'text-gray-500';
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-6xl mx-auto">
        <header className="text-center mb-8">
          <h1 className="text-4xl font-bold text-blue-400 mb-2">TankPit Bot Controller</h1>
          <p className="text-gray-400">Automated fuel management for tankpit.com</p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Panel - Controls */}
          <div className="space-y-6">
            {/* Login Section */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4 text-blue-300">Login to TankPit</h2>
              {!isLoggedIn ? (
                <div className="space-y-4">
                  <input
                    type="text"
                    placeholder="Username"
                    value={loginData.username}
                    onChange={(e) => setLoginData(prev => ({ ...prev, username: e.target.value }))}
                    className="w-full p-3 bg-gray-700 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                  />
                  <input
                    type="password"
                    placeholder="Password"
                    value={loginData.password}
                    onChange={(e) => setLoginData(prev => ({ ...prev, password: e.target.value }))}
                    className="w-full p-3 bg-gray-700 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                  />
                  <button
                    onClick={handleLogin}
                    disabled={isLoading || !loginData.username || !loginData.password}
                    className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white font-semibold py-3 px-4 rounded transition-colors"
                  >
                    {isLoading ? 'Logging in...' : 'Login'}
                  </button>
                </div>
              ) : (
                <div className="text-green-400">
                  ✓ Logged in as {loginData.username}
                </div>
              )}
            </div>

            {/* Tank Selection */}
            {isLoggedIn && (
              <div className="bg-gray-800 rounded-lg p-6">
                <h2 className="text-xl font-semibold mb-4 text-blue-300">Select Tank</h2>
                {availableTanks.length > 0 ? (
                  <div className="space-y-2">
                    {availableTanks.map((tank) => (
                      <button
                        key={tank.id}
                        onClick={() => handleSelectTank(tank.id)}
                        className={`w-full p-3 rounded text-left transition-colors ${
                          selectedTank === tank.id
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                        }`}
                      >
                        <div className="font-semibold">{tank.name}</div>
                        <div className="text-sm opacity-75">Fuel: {tank.fuel}% | Position: ({tank.position.x}, {tank.position.y})</div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="text-gray-400">No tanks available</div>
                )}
              </div>
            )}

            {/* Bot Controls */}
            {isLoggedIn && selectedTank && (
              <div className="bg-gray-800 rounded-lg p-6">
                <h2 className="text-xl font-semibold mb-4 text-blue-300">Bot Controls</h2>
                <div className="flex gap-4">
                  <button
                    onClick={handleStartBot}
                    disabled={botStatus.running || isLoading}
                    className="flex-1 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 text-white font-semibold py-3 px-4 rounded transition-colors"
                  >
                    {isLoading ? 'Starting...' : 'Start Bot'}
                  </button>
                  <button
                    onClick={handleStopBot}
                    disabled={!botStatus.running || isLoading}
                    className="flex-1 bg-red-600 hover:bg-red-700 disabled:bg-gray-600 text-white font-semibold py-3 px-4 rounded transition-colors"
                  >
                    {isLoading ? 'Stopping...' : 'Stop Bot'}
                  </button>
                </div>
              </div>
            )}

            {/* Settings */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4 text-blue-300">Bot Settings</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-2">Refuel Threshold: {settings.refuel_threshold}%</label>
                  <input
                    type="range"
                    min="10"
                    max="50"
                    value={settings.refuel_threshold}
                    onChange={(e) => setSettings(prev => ({ ...prev, refuel_threshold: parseInt(e.target.value) }))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Shield Threshold: {settings.shield_threshold}%</label>
                  <input
                    type="range"
                    min="5"
                    max="25"
                    value={settings.shield_threshold}
                    onChange={(e) => setSettings(prev => ({ ...prev, shield_threshold: parseInt(e.target.value) }))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Safe Threshold: {settings.safe_threshold}%</label>
                  <input
                    type="range"
                    min="70"
                    max="100"
                    value={settings.safe_threshold}
                    onChange={(e) => setSettings(prev => ({ ...prev, safe_threshold: parseInt(e.target.value) }))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Target Player (Optional)</label>
                  <input
                    type="text"
                    placeholder="Player name to protect"
                    value={settings.target_player}
                    onChange={(e) => setSettings(prev => ({ ...prev, target_player: e.target.value }))}
                    className="w-full p-3 bg-gray-700 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-2">Preferred Map Type</label>
                  <select
                    value={settings.preferred_map}
                    onChange={(e) => setSettings(prev => ({ ...prev, preferred_map: e.target.value }))}
                    className="w-full p-3 bg-gray-700 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                  >
                    <option value="practice">Practice Map</option>
                    <option value="tournament">Tournament Map</option>
                    <option value="any">Any Available Map</option>
                  </select>
                  <div className="text-xs text-gray-400 mt-1">
                    Bot will prefer this map type when entering the game
                  </div>
                </div>
                <button
                  onClick={handleSettingsUpdate}
                  className="w-full bg-purple-600 hover:bg-purple-700 text-white font-semibold py-3 px-4 rounded transition-colors"
                >
                  Update Settings
                </button>
              </div>
            </div>
          </div>

          {/* Right Panel - Status & Monitoring */}
          <div className="space-y-6">
            {/* Bot Status */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4 text-blue-300">Bot Status</h2>
              <div className="space-y-4">
                <div className="flex justify-between items-center">
                  <span>Status:</span>
                  <span className={`font-semibold ${getStatusColor()}`}>
                    {botStatus.running ? 'RUNNING' : 'STOPPED'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Current Action:</span>
                  <span className="font-mono text-yellow-400">{botStatus.status}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Shields:</span>
                  <span className={botStatus.shields_active ? 'text-blue-400' : 'text-gray-400'}>
                    {botStatus.shields_active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span>Position:</span>
                  <span className="font-mono">({botStatus.position.x}, {botStatus.position.y})</span>
                </div>
              </div>
            </div>

            {/* Fuel Gauge */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4 text-blue-300">Fuel Level</h2>
              <div className="mb-4">
                <div className="flex justify-between text-sm mb-2">
                  <span>Current Fuel</span>
                  <span className="font-semibold">{botStatus.current_fuel}%</span>
                </div>
                <div className="w-full bg-gray-700 rounded-full h-6">
                  <div
                    className={`h-6 rounded-full transition-all duration-500 ${getFuelBarColor()}`}
                    style={{ width: `${Math.max(0, botStatus.current_fuel)}%` }}
                  ></div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="text-center">
                  <div className="text-red-400">Shield: {settings.shield_threshold}%</div>
                </div>
                <div className="text-center">
                  <div className="text-yellow-400">Refuel: {settings.refuel_threshold}%</div>
                </div>
                <div className="text-center">
                  <div className="text-green-400">Safe: {settings.safe_threshold}%</div>
                </div>
              </div>
            </div>

            {/* Activity Log */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4 text-blue-300">Activity Log</h2>
              <div className="bg-black rounded p-4 h-64 overflow-y-auto font-mono text-sm space-y-1">
                {logs.length > 0 ? (
                  logs.map((log, index) => (
                    <div key={index} className="text-green-400">{log}</div>
                  ))
                ) : (
                  <div className="text-gray-500">No activity yet...</div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;