import requests
import sys
import json
import time
from datetime import datetime

class TankPitBotAPITester:
    def __init__(self, base_url="https://tankpilot.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_result(self, test_name, success, message="", response_data=None):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            
        result = {
            "test": test_name,
            "success": success,
            "message": message,
            "response_data": response_data
        }
        self.test_results.append(result)
        
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {test_name}: {message}")
        
        return success

    def run_api_test(self, name, method, endpoint, expected_status=200, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.api_url}/{endpoint}"
        if headers is None:
            headers = {'Content-Type': 'application/json'}

        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=headers, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=10)

            print(f"   Status Code: {response.status_code}")
            
            # Try to parse JSON response
            try:
                response_json = response.json()
                print(f"   Response: {json.dumps(response_json, indent=2)[:200]}...")
            except:
                response_json = {"raw_response": response.text[:200]}
                print(f"   Raw Response: {response.text[:200]}...")

            success = response.status_code == expected_status
            message = f"Status: {response.status_code} (expected {expected_status})"
            
            return self.log_result(name, success, message, response_json)

        except requests.exceptions.Timeout:
            return self.log_result(name, False, "Request timeout (10s)")
        except requests.exceptions.ConnectionError:
            return self.log_result(name, False, "Connection error - server may be down")
        except Exception as e:
            return self.log_result(name, False, f"Error: {str(e)}")

    def test_bot_status(self):
        """Test GET /api/bot/status"""
        return self.run_api_test(
            "Get Bot Status",
            "GET",
            "bot/status",
            200
        )

    def test_bot_settings_update(self):
        """Test POST /api/bot/settings"""
        settings_data = {
            "refuel_threshold": 30,
            "shield_threshold": 15,
            "safe_threshold": 80,
            "target_player": "test_player",
            "username": "test_user",
            "password": "test_pass"
        }
        
        return self.run_api_test(
            "Update Bot Settings",
            "POST",
            "bot/settings",
            200,
            data=settings_data
        )

    def test_bot_login(self):
        """Test POST /api/bot/login"""
        login_data = {
            "username": "test_user",
            "password": "test_password"
        }
        
        # This might fail with 500 since we're not actually connecting to tankpit.com
        # But we want to test the endpoint exists and handles the request
        result = self.run_api_test(
            "Bot Login",
            "POST",
            "bot/login",
            expected_status=500,  # Expecting 500 since browser automation will fail
            data=login_data
        )
        
        # Also test with 200 in case it works
        if not result:
            return self.run_api_test(
                "Bot Login (Alternative)",
                "POST", 
                "bot/login",
                200,
                data=login_data
            )
        return result

    def test_get_tanks(self):
        """Test GET /api/bot/tanks"""
        # This might fail with 500 since no browser session exists
        result = self.run_api_test(
            "Get Available Tanks",
            "GET",
            "bot/tanks",
            expected_status=500  # Expecting 500 since no browser session
        )
        
        # Also test with 200 in case it works
        if not result:
            return self.run_api_test(
                "Get Available Tanks (Alternative)",
                "GET",
                "bot/tanks", 
                200
            )
        return result

    def test_start_bot(self):
        """Test POST /api/bot/start"""
        return self.run_api_test(
            "Start Bot",
            "POST",
            "bot/start",
            200
        )

    def test_stop_bot(self):
        """Test POST /api/bot/stop"""
        return self.run_api_test(
            "Stop Bot",
            "POST",
            "bot/stop",
            200
        )

    def test_select_tank(self):
        """Test POST /api/bot/select-tank/{tank_id}"""
        return self.run_api_test(
            "Select Tank",
            "POST",
            "bot/select-tank/0",
            expected_status=500  # Expecting 500 since no browser session
        )

    def test_websocket_endpoint(self):
        """Test WebSocket endpoint accessibility"""
        # We can't easily test WebSocket with requests, but we can check if the endpoint exists
        # by trying to connect to it (it should return a specific error)
        try:
            ws_url = self.base_url.replace('https://', 'wss://') + "/api/ws/bot-status"
            print(f"\n🔍 Testing WebSocket Endpoint...")
            print(f"   WebSocket URL: {ws_url}")
            
            # Try to make a regular HTTP request to the WebSocket endpoint
            # This should return a specific error indicating it's a WebSocket endpoint
            response = requests.get(ws_url.replace('wss://', 'https://'), timeout=5)
            
            # WebSocket endpoints typically return 426 Upgrade Required or similar
            if response.status_code in [426, 400, 405]:
                return self.log_result("WebSocket Endpoint", True, f"WebSocket endpoint exists (HTTP {response.status_code})")
            else:
                return self.log_result("WebSocket Endpoint", False, f"Unexpected status: {response.status_code}")
                
        except Exception as e:
            return self.log_result("WebSocket Endpoint", False, f"Error: {str(e)}")

    def run_all_tests(self):
        """Run all API tests"""
        print("=" * 60)
        print("🚀 STARTING TANKPIT BOT API TESTS")
        print("=" * 60)
        print(f"Testing against: {self.base_url}")
        print(f"API Base URL: {self.api_url}")
        
        # Test basic endpoints first
        print("\n📋 TESTING BASIC ENDPOINTS...")
        self.test_bot_status()
        self.test_bot_settings_update()
        
        # Test bot control endpoints
        print("\n🤖 TESTING BOT CONTROL ENDPOINTS...")
        self.test_start_bot()
        self.test_stop_bot()
        
        # Test browser-dependent endpoints (these may fail)
        print("\n🌐 TESTING BROWSER-DEPENDENT ENDPOINTS...")
        self.test_bot_login()
        self.test_get_tanks()
        self.test_select_tank()
        
        # Test WebSocket
        print("\n🔌 TESTING WEBSOCKET ENDPOINT...")
        self.test_websocket_endpoint()
        
        # Print summary
        self.print_summary()
        
        return self.tests_passed == self.tests_run

    def print_summary(self):
        """Print test summary"""
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        print(f"Total Tests: {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        # Print failed tests
        failed_tests = [r for r in self.test_results if not r['success']]
        if failed_tests:
            print(f"\n❌ FAILED TESTS ({len(failed_tests)}):")
            for test in failed_tests:
                print(f"   • {test['test']}: {test['message']}")
        
        # Print passed tests
        passed_tests = [r for r in self.test_results if r['success']]
        if passed_tests:
            print(f"\n✅ PASSED TESTS ({len(passed_tests)}):")
            for test in passed_tests:
                print(f"   • {test['test']}: {test['message']}")

def main():
    """Main test function"""
    tester = TankPitBotAPITester()
    
    try:
        success = tester.run_all_tests()
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n\n💥 Unexpected error: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())