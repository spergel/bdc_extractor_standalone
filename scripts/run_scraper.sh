#!/bin/bash
# Quick script to run the LLM table scraper with Gemini 2.0 Flash

# Check if Google Gemini API key is set
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "❌ Error: GOOGLE_API_KEY environment variable not set"
    echo "Please set it with: export GOOGLE_API_KEY='your-gemini-api-key-here'"
    echo "Or add it to your .env file"
    exit 1
fi

# Default values
TICKER=${1:-"MRCC"}
FILING_TYPE=${2:-"10-Q"}
YEAR=${3:-"2025"}

echo "🚀 Running LLM Table Scraper with Gemini 2.0 Flash"
echo "Ticker: $TICKER"
echo "Filing Type: $FILING_TYPE"
echo "Year: $YEAR"
echo ""

python llm_table_scraper.py \
    --ticker "$TICKER" \
    --filing-type "$FILING_TYPE" \
    --year "$YEAR" \
    --llm-provider "gemini"
