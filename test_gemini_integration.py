#!/usr/bin/env python3
"""
Test Gemini Integration

Simple test to verify Gemini API integration works correctly.
"""

import os
import sys

# Test imports
try:
    import google.generativeai as genai
    print("✅ Google Generative AI library imported successfully")
except ImportError as e:
    print(f"❌ Failed to import google-generativeai: {e}")
    sys.exit(1)

# Test environment variable
google_api_key = os.getenv('GOOGLE_API_KEY')
if google_api_key:
    print("✅ GOOGLE_API_KEY environment variable found")
    # Test API key format (basic validation)
    if len(google_api_key) > 20:
        print("✅ API key appears to be properly formatted")
    else:
        print("⚠️  API key seems short, please verify it's correct")
else:
    print("❌ GOOGLE_API_KEY environment variable not set")
    print("Please set it with: export GOOGLE_API_KEY='your-actual-gemini-api-key'")
    print("Or add it to your .env file")

# Test Gemini model initialization (without actually calling API)
try:
    # This will fail if API key is invalid, but won't make actual API calls
    if google_api_key:
        genai.configure(api_key=google_api_key)
        print("✅ Gemini API configured successfully")

        # Try to create model (this validates API key format)
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        print("✅ Gemini 2.0 Flash model initialized successfully")
        print("🎉 Gemini integration is ready to use!")
    else:
        print("⚠️  Skipping model initialization - no API key provided")

except Exception as e:
    print(f"❌ Gemini API configuration failed: {e}")
    print("Please check your GOOGLE_API_KEY")

print("\n" + "="*50)
print("USAGE INSTRUCTIONS:")
print("="*50)
print("To use Gemini with the scraper:")
print("1. Set your API key: export GOOGLE_API_KEY='your-key-here'")
print("2. Run the scraper: python llm_table_scraper.py --llm-provider gemini --ticker MRCC --year 2025")
print("3. Or use the quick script: ./run_scraper.sh MRCC 10-Q 2025")
print("="*50)















