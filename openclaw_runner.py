#!/usr/bin/env python3
"""
OpenClaw 0DTE Alert Runner
Simplified entry point for OpenClaw cron job execution
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Set up logging for OpenClaw environment
def setup_logging():
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def check_environment():
    """Check if required environment variables are set"""
    required_vars = ['ANTHROPIC_API_KEY']
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {missing_vars}")
        logging.error("Please copy .env.template to .env and fill in your API keys")
        return False
    
    return True

def main():
    """Main entry point for OpenClaw cron execution"""
    # Ensure we're in the correct directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Load environment variables
    load_dotenv()
    setup_logging()
    
    logging.info("🚀 Starting 0DTE Alert for OpenClaw execution...")
    
    # Check environment setup
    if not check_environment():
        logging.error("❌ Environment setup failed")
        sys.exit(1)
    
    try:
        # Import and run the main application
        from main import main as run_main
        
        logging.info("🔍 Executing 0DTE alert analysis...")
        result = run_main()
        
        if result:
            logging.info("✅ 0DTE Alert execution completed successfully")
        else:
            logging.info("ℹ️ 0DTE Alert completed - no alerts generated")
            
    except ImportError as e:
        logging.error(f"❌ Import error: {e}")
        logging.error("Make sure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        logging.error(f"❌ Execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()