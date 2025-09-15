#!/bin/bash

# Kindle OCR Processing Wrapper Script
# Coordinates AppleScript screenshot capture with Python OCR processing

set -e  # Exit on any error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPLESCRIPT="$SCRIPT_DIR/kindle_automation.applescript"
PYTHON_SCRIPT="$SCRIPT_DIR/kindle_ocr.py"
CONFIG_FILE="$SCRIPT_DIR/config.env"

# Default values
SERVICE_ACCOUNT_PATH=""
DRIVE_FOLDER_ID=""
SKIP_DRIVE=false
SKIP_PDF=false
MAX_PAGES=500

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Load configuration if it exists
load_config() {
    if [[ -f "$CONFIG_FILE" ]]; then
        log_info "Loading configuration from $CONFIG_FILE"
        source "$CONFIG_FILE"
    else
        log_warning "No config file found at $CONFIG_FILE"
        log_info "Create config.env with:"
        echo "  SERVICE_ACCOUNT_PATH=/path/to/service-account.json"
        echo "  DRIVE_FOLDER_ID=your_folder_id"
        echo "  MAX_PAGES=500"
    fi
}

# Check dependencies
check_dependencies() {
    log_info "Checking dependencies..."
    
    # Check if AppleScript file exists
    if [[ ! -f "$APPLESCRIPT" ]]; then
        log_error "AppleScript not found: $APPLESCRIPT"
        exit 1
    fi
    
    # Check if Python script exists
    if [[ ! -f "$PYTHON_SCRIPT" ]]; then
        log_error "Python script not found: $PYTHON_SCRIPT"
        exit 1
    fi
    
    # Check Python and required packages
    if ! command -v python3 &> /dev/null; then
        log_error "python3 not found. Please install Python 3."
        exit 1
    fi
    
    # Check if Kindle app is running
    if ! pgrep -f "Amazon Kindle" > /dev/null; then
        log_warning "Amazon Kindle app is not running. Please start it and open a book."
        read -p "Press Enter when ready to continue..."
    fi
    
    log_success "Dependencies check passed"
}

# Run AppleScript to capture screenshots
run_applescript() {
    log_info "Starting screenshot capture with AppleScript..."
    
    # Run AppleScript and capture output
    if output=$(osascript "$APPLESCRIPT" 2>&1); then
        log_success "Screenshot capture completed"
        echo "$output"
        
        # Extract folder path from AppleScript output
        SCREENSHOT_FOLDER=$(echo "$output" | grep "Created folder:" | sed 's/Created folder: //')
        
        if [[ -z "$SCREENSHOT_FOLDER" ]]; then
            log_error "Could not determine screenshot folder path"
            exit 1
        fi
        
        log_info "Screenshots saved to: $SCREENSHOT_FOLDER"
        
    else
        log_error "AppleScript execution failed:"
        echo "$output"
        exit 1
    fi
}

# Run Python OCR processing
run_python_ocr() {
    log_info "Starting OCR processing..."
    
    if [[ -z "$SCREENSHOT_FOLDER" ]]; then
        log_error "Screenshot folder path not set"
        exit 1
    fi
    
    if [[ ! -d "$SCREENSHOT_FOLDER" ]]; then
        log_error "Screenshot folder does not exist: $SCREENSHOT_FOLDER"
        exit 1
    fi
    
    # Count screenshots
    screenshot_count=$(find "$SCREENSHOT_FOLDER" -name "screenshot_*.png" | wc -l)
    log_info "Found $screenshot_count screenshots to process"
    
    if [[ $screenshot_count -eq 0 ]]; then
        log_error "No screenshots found in $SCREENSHOT_FOLDER"
        exit 1
    fi
    
    # Build Python command
    python_cmd=(python3 "$PYTHON_SCRIPT" "$SCREENSHOT_FOLDER")
    
    if [[ -n "$SERVICE_ACCOUNT_PATH" ]]; then
        python_cmd+=(--service-account "$SERVICE_ACCOUNT_PATH")
    else
        log_error "SERVICE_ACCOUNT_PATH not set. Please configure in $CONFIG_FILE"
        exit 1
    fi
    
    if [[ -n "$DRIVE_FOLDER_ID" ]] && [[ "$SKIP_DRIVE" != "true" ]]; then
        python_cmd+=(--drive-folder-id "$DRIVE_FOLDER_ID")
    else
        python_cmd+=(--skip-drive)
        log_warning "Skipping Google Drive upload"
    fi
    
    if [[ "$SKIP_PDF" == "true" ]]; then
        python_cmd+=(--skip-pdf)
        log_warning "Skipping PDF creation"
    fi
    
    # Execute Python script
    log_info "Running: ${python_cmd[*]}"
    
    if "${python_cmd[@]}"; then
        log_success "OCR processing completed successfully"
    else
        log_error "OCR processing failed"
        exit 1
    fi
}

# Cleanup function
cleanup() {
    if [[ -n "$SCREENSHOT_FOLDER" ]] && [[ -d "$SCREENSHOT_FOLDER" ]]; then
        log_info "Cleaning up temporary screenshot files..."
        find "$SCREENSHOT_FOLDER" -name "screenshot_*.png" -delete
        log_success "Cleanup completed"
    fi
}

# Main execution
main() {
    log_info "Starting Kindle OCR automation"
    
    # Load configuration
    load_config
    
    # Check dependencies
    check_dependencies
    
    # Run AppleScript for screenshots
    run_applescript
    
    # Run Python for OCR processing
    run_python_ocr
    
    # Cleanup screenshots (keep processed files)
    cleanup
    
    log_success "Kindle OCR automation completed successfully!"
    log_info "Results saved to: $SCREENSHOT_FOLDER"
    
    # Open results folder
    if command -v open &> /dev/null; then
        open "$SCREENSHOT_FOLDER"
    fi
}

# Handle command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-drive)
            SKIP_DRIVE=true
            shift
            ;;
        --skip-pdf)
            SKIP_PDF=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --skip-drive    Skip Google Drive upload"
            echo "  --skip-pdf      Skip PDF creation"
            echo "  --help, -h      Show this help message"
            echo ""
            echo "Configuration:"
            echo "  Create config.env file with:"
            echo "    SERVICE_ACCOUNT_PATH=/path/to/service-account.json"
            echo "    DRIVE_FOLDER_ID=your_folder_id"
            echo "    MAX_PAGES=500"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Run main function
main