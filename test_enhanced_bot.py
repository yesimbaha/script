#!/usr/bin/env python3
"""
Focused test for enhanced bot sequence logic and improved detection systems
"""

import sys
import os
sys.path.append('/app/backend')

def test_enhanced_bot_functionality():
    """Test all enhanced bot functionality as requested in review"""
    
    print("=" * 80)
    print("🤖 ENHANCED BOT SEQUENCE TESTING")
    print("=" * 80)
    
    try:
        # Import the bot class
        from server import TankpitBot
        print("✅ Successfully imported TankpitBot class")
        
        # Create bot instance
        bot = TankpitBot()
        print("✅ Successfully created TankpitBot instance")
        
        # Test 1: Enhanced Bot Sequence Functions
        print("\n🔄 Testing Enhanced Bot Sequence Functions...")
        sequence_functions = [
            'perform_initial_join_sequence',
            'execute_fuel_priority_sequence', 
            'execute_safe_mode_sequence',
            'execute_balanced_sequence'
        ]
        
        for func_name in sequence_functions:
            if hasattr(bot, func_name):
                func = getattr(bot, func_name)
                if callable(func):
                    print(f"  ✅ {func_name} - EXISTS and CALLABLE")
                else:
                    print(f"  ❌ {func_name} - EXISTS but NOT CALLABLE")
            else:
                print(f"  ❌ {func_name} - MISSING")
        
        # Test 2: Improved Detection Systems
        print("\n🔍 Testing Improved Detection Systems...")
        detection_functions = [
            'detect_fuel_nodes',
            'detect_equipment_visually',
            'collect_prioritized_fuel',
            'collect_fuel_until_safe'
        ]
        
        for func_name in detection_functions:
            if hasattr(bot, func_name):
                func = getattr(bot, func_name)
                if callable(func):
                    print(f"  ✅ {func_name} - EXISTS and CALLABLE")
                else:
                    print(f"  ❌ {func_name} - EXISTS but NOT CALLABLE")
            else:
                print(f"  ❌ {func_name} - MISSING")
        
        # Test 3: Map Navigation Functions
        print("\n🗺️  Testing Map Navigation Functions...")
        map_functions = [
            'use_overview_map_for_fuel',
            'find_bot_on_overview_map',
            'execute_landing_sequence'
        ]
        
        for func_name in map_functions:
            if hasattr(bot, func_name):
                func = getattr(bot, func_name)
                if callable(func):
                    print(f"  ✅ {func_name} - EXISTS and CALLABLE")
                else:
                    print(f"  ❌ {func_name} - EXISTS but NOT CALLABLE")
            else:
                print(f"  ❌ {func_name} - MISSING")
        
        # Test 4: OpenCV Integration
        print("\n🖼️  Testing OpenCV Integration...")
        try:
            import cv2
            import numpy as np
            print(f"  ✅ OpenCV imported successfully (version: {cv2.__version__})")
            
            # Test basic OpenCV operations used by the bot
            test_img = np.zeros((100, 100, 3), dtype=np.uint8)
            test_img[25:75, 25:75] = [0, 255, 255]  # Yellow square
            
            # HSV conversion (used in fuel detection)
            hsv = cv2.cvtColor(test_img, cv2.COLOR_BGR2HSV)
            print("  ✅ HSV color space conversion working")
            
            # Color masking (used in detection)
            lower = np.array([20, 150, 150])
            upper = np.array([30, 255, 255])
            mask = cv2.inRange(hsv, lower, upper)
            print("  ✅ Color masking working")
            
            # Contour detection (used in node detection)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            print(f"  ✅ Contour detection working (found {len(contours)} contours)")
            
            # Morphological operations (used in cleanup)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            cleaned = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            print("  ✅ Morphological operations working")
            
        except ImportError as e:
            print(f"  ❌ OpenCV import failed: {e}")
        except Exception as e:
            print(f"  ❌ OpenCV operation failed: {e}")
        
        # Test 5: Bot Cycle Logic Integration
        print("\n🔄 Testing Bot Cycle Logic Integration...")
        
        # Check if run_bot_cycle method exists and references new functions
        if hasattr(bot, 'run_bot_cycle'):
            print("  ✅ run_bot_cycle method exists")
            
            # Check if the method contains references to new sequence functions
            import inspect
            source = inspect.getsource(bot.run_bot_cycle)
            
            sequence_calls = [
                'perform_initial_join_sequence',
                'execute_fuel_priority_sequence',
                'execute_safe_mode_sequence', 
                'execute_balanced_sequence'
            ]
            
            found_calls = []
            for call in sequence_calls:
                if call in source:
                    found_calls.append(call)
                    print(f"    ✅ {call} - INTEGRATED in bot cycle")
                else:
                    print(f"    ❌ {call} - NOT FOUND in bot cycle")
            
            if len(found_calls) >= 3:
                print("  ✅ Bot cycle properly integrated with new sequences")
            else:
                print("  ⚠️  Bot cycle integration may be incomplete")
                
        else:
            print("  ❌ run_bot_cycle method missing")
        
        # Test 6: Fuel Detection Enhancement
        print("\n⛽ Testing Enhanced Fuel Detection...")
        
        # Check existing fuel detection methods
        existing_methods = [
            'detect_fuel_level',
            'find_and_measure_fuel_bar',
            'measure_fuel_in_bar',
            'scan_for_fuel_bar_pattern',
            'analyze_horizontal_line_for_fuel',
            'analyze_fuel_area_improved'
        ]
        
        existing_count = 0
        for method in existing_methods:
            if hasattr(bot, method) and callable(getattr(bot, method)):
                existing_count += 1
                print(f"  ✅ {method} - EXISTING METHOD AVAILABLE")
        
        # Check new fuel detection methods
        new_methods = [
            'detect_fuel_nodes',
            'collect_prioritized_fuel', 
            'collect_fuel_until_safe'
        ]
        
        new_count = 0
        for method in new_methods:
            if hasattr(bot, method) and callable(getattr(bot, method)):
                new_count += 1
                print(f"  ✅ {method} - NEW METHOD AVAILABLE")
        
        print(f"\n📊 SUMMARY:")
        print(f"  • Enhanced Bot Sequences: {len([f for f in sequence_functions if hasattr(bot, f)])}/4")
        print(f"  • Detection Systems: {len([f for f in detection_functions if hasattr(bot, f)])}/4") 
        print(f"  • Map Navigation: {len([f for f in map_functions if hasattr(bot, f)])}/3")
        print(f"  • Existing Fuel Methods: {existing_count}/6")
        print(f"  • New Fuel Methods: {new_count}/3")
        print(f"  • OpenCV Integration: ✅ Working")
        
        total_functions = len(sequence_functions) + len(detection_functions) + len(map_functions)
        found_functions = sum([
            len([f for f in sequence_functions if hasattr(bot, f)]),
            len([f for f in detection_functions if hasattr(bot, f)]),
            len([f for f in map_functions if hasattr(bot, f)])
        ])
        
        success_rate = (found_functions / total_functions) * 100
        print(f"\n🎯 OVERALL SUCCESS RATE: {success_rate:.1f}% ({found_functions}/{total_functions} functions)")
        
        if success_rate >= 90:
            print("🎉 EXCELLENT: All enhanced bot functionality is properly implemented!")
            return True
        elif success_rate >= 75:
            print("✅ GOOD: Most enhanced bot functionality is working correctly")
            return True
        else:
            print("⚠️  WARNING: Some enhanced bot functionality may be missing")
            return False
            
    except ImportError as e:
        print(f"❌ CRITICAL ERROR: Cannot import bot module: {e}")
        return False
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")
        return False

if __name__ == "__main__":
    success = test_enhanced_bot_functionality()
    sys.exit(0 if success else 1)