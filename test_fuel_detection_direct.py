#!/usr/bin/env python3
"""
Direct test of the simplified fuel detection system
"""
import sys
import asyncio
import cv2
import numpy as np
sys.path.append('/app/backend')

from server import TankpitBot

async def test_simplified_fuel_detection():
    """Test the simplified fuel detection directly"""
    print("🔥 Testing Simplified Fuel Detection System Directly...")
    
    bot = TankpitBot()
    
    # Test 1: Create a test fuel gauge image with known fuel level
    print("\n1. Testing with 75% fuel gauge...")
    test_gauge_75 = np.zeros((20, 100, 3), dtype=np.uint8)
    test_gauge_75[:, :75] = [100, 150, 200]  # 75% colored (fuel)
    test_gauge_75[:, 75:] = [5, 5, 5]        # 25% black (empty)
    
    result_75 = await bot.measure_fuel_gauge_simple(test_gauge_75)
    print(f"   Result: {result_75}% (expected ~75%)")
    
    # Test 2: Create a test fuel gauge image with 50% fuel
    print("\n2. Testing with 50% fuel gauge...")
    test_gauge_50 = np.zeros((20, 100, 3), dtype=np.uint8)
    test_gauge_50[:, :50] = [120, 180, 220]  # 50% colored (fuel)
    test_gauge_50[:, 50:] = [10, 10, 10]     # 50% black (empty)
    
    result_50 = await bot.measure_fuel_gauge_simple(test_gauge_50)
    print(f"   Result: {result_50}% (expected ~50%)")
    
    # Test 3: Create a test fuel gauge image with 25% fuel
    print("\n3. Testing with 25% fuel gauge...")
    test_gauge_25 = np.zeros((20, 100, 3), dtype=np.uint8)
    test_gauge_25[:, :25] = [80, 120, 160]   # 25% colored (fuel)
    test_gauge_25[:, 25:] = [8, 8, 8]        # 75% black (empty)
    
    result_25 = await bot.measure_fuel_gauge_simple(test_gauge_25)
    print(f"   Result: {result_25}% (expected ~25%)")
    
    # Test 4: Create a test fuel gauge image with 0% fuel (all black)
    print("\n4. Testing with 0% fuel gauge (all black)...")
    test_gauge_0 = np.zeros((20, 100, 3), dtype=np.uint8)
    test_gauge_0[:, :] = [5, 5, 5]           # All black (empty)
    
    result_0 = await bot.measure_fuel_gauge_simple(test_gauge_0)
    print(f"   Result: {result_0}% (expected ~0%)")
    
    # Test 5: Create a test fuel gauge image with 100% fuel (all colored)
    print("\n5. Testing with 100% fuel gauge (all colored)...")
    test_gauge_100 = np.zeros((20, 100, 3), dtype=np.uint8)
    test_gauge_100[:, :] = [150, 200, 250]   # All colored (fuel)
    
    result_100 = await bot.measure_fuel_gauge_simple(test_gauge_100)
    print(f"   Result: {result_100}% (expected ~100%)")
    
    # Test 6: Test detect_fuel_level without page (should return default)
    print("\n6. Testing detect_fuel_level without browser page...")
    result_no_page = await bot.detect_fuel_level()
    print(f"   Result: {result_no_page}% (expected default 50%)")
    
    print("\n✅ Simplified fuel detection tests completed!")
    
    # Summary
    print(f"\n📊 SUMMARY:")
    print(f"   75% fuel test: {result_75}% ({'✅ PASS' if 70 <= result_75 <= 80 else '❌ FAIL'})")
    print(f"   50% fuel test: {result_50}% ({'✅ PASS' if 45 <= result_50 <= 55 else '❌ FAIL'})")
    print(f"   25% fuel test: {result_25}% ({'✅ PASS' if 20 <= result_25 <= 30 else '❌ FAIL'})")
    print(f"   0% fuel test: {result_0}% ({'✅ PASS' if 0 <= result_0 <= 5 else '❌ FAIL'})")
    print(f"   100% fuel test: {result_100}% ({'✅ PASS' if 95 <= result_100 <= 100 else '❌ FAIL'})")
    print(f"   No page test: {result_no_page}% ({'✅ PASS' if result_no_page == 50 else '❌ FAIL'})")

if __name__ == "__main__":
    asyncio.run(test_simplified_fuel_detection())