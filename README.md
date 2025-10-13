# Kindle OCR Automation

Automatically capture screenshots from Kindle app, perform OCR using Google Gemini AI, and sync to Google Drive.

## Features

- ✅ **Fully automated** - One command captures entire books
- ✅ **Smart OCR** - Google Gemini 2.5 Flash with content-aware prompting
- ✅ **Content filtering** - Ignores system UI, focuses on book text only
- ✅ **Intelligent formatting** - AI-generated Markdown with proper structure
- ✅ **Fault tolerant** - Individual OCR files for each page, automatic recovery
- ✅ **Smart filenames** - Generated from book content using AI
- ✅ **Google Drive sync** - Simple file copying to synced folder
- ✅ **Multi-language support** - Auto-detects page direction based on locale
- ✅ **Reliable page ordering** - Zero-padded filenames and numeric sorting
- ✅ **Clean modular architecture** - Separate AppleScript, Python components

## Requirements

- macOS with Amazon Kindle app
- Python 3.7+
- Google Gemini API key
- img2pdf (optional, for PDF creation)

## Installation

1. **Install Python dependencies:**
   ```bash
   pip3 install google-generativeai
   ```

2. **Install img2pdf (optional):**
   ```bash
   brew install img2pdf
   ```

3. **Set up Google Gemini API:**
   - Get API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
   - Copy `config.env.example` to `config.env`
   - Add your API key to `config.env`

4. **Configure Google Drive (optional):**
   - The script uses your local Google Drive sync folder
   - Default: `~/Library/CloudStorage/GoogleDrive-*/My Drive/Kindle_md`
   - No API keys or service accounts needed!

## Usage

### Quick Start
```bash
# First, set the number of pages in the AppleScript
# Edit line 6 in kindle2img.applescript: set MAX_PAGES to 50

# Step 1: Capture screenshots
osascript kindle2img.applescript

# Step 2: Extract text with OCR
python3 img2txt.py /path/to/screenshots

# Step 3: Convert to markdown and upload to Drive
python3 txt2md.py /path/to/screenshots/ocr_output.txt

# OR use the combined pipeline:
python3 process_kindle.py /path/to/screenshots
```

### Screenshot Capture Only
```bash
osascript kindle2img.applescript
```

### OCR Processing Only
```bash
# Basic usage (uses config.env for API key)
python3 img2txt.py /path/to/screenshots

# With custom API key
python3 img2txt.py /path/to/screenshots --gemini-api-key YOUR_API_KEY
```

### Markdown Conversion Only
```bash
# Basic usage (uses config.env for API key and Google Drive path)
python3 txt2md.py /path/to/ocr_output.txt

# With custom options
python3 txt2md.py /path/to/ocr_output.txt \
  --gemini-api-key YOUR_API_KEY \
  --drive-folder ~/Google\ Drive/Kindle_md \
  --skip-drive \
  --output-name "My Custom Book Title"
```

## File Structure

```
├── kindle2img.applescript  # Screenshot capture
├── img2txt.py                     # OCR processing with Gemini AI
├── txt2md.py                      # Markdown conversion and Google Drive sync
├── config.env                     # Configuration (create from example)
├── config.env.example             # Configuration template
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

## Configuration

Edit `config.env`:

```bash
# Google Gemini API Key
GEMINI_API_KEY="your_gemini_api_key_here"

# Google Drive sync folder path
GOOGLE_DRIVE_FOLDER="/Users/username/Library/CloudStorage/GoogleDrive-email/My Drive/Kindle_md"

# Maximum pages (edit in AppleScript)
MAX_PAGES=500
```

## Output Files

After processing, you'll get:

```
Kindle_Screenshots_20250915_155310/
├── screenshot_001.png           # Zero-padded screenshots
├── screenshot_002.png
├── screenshot_003.png
├── ocr_001.txt                  # Individual OCR results
├── ocr_002.txt
├── ocr_003.txt
├── ocr_output.txt               # Merged OCR text
├── Book_Title_Here.txt          # Final clean text
├── Book_Title_Here.md           # AI-formatted Markdown
└── Book_Title_Here.pdf          # Optional PDF
```

## How It Works

1. **AppleScript** (`kindle2img.applescript`):
   - Activates Kindle app
   - Takes screenshots with zero-padded names (001, 002, 003...)
   - Automatically navigates pages using smart page direction detection
   - Uses configurable page count (no infinite loops)

2. **Python OCR** (`img2txt.py`):
   - Processes each screenshot individually with Google Gemini AI
   - Creates individual OCR files for fault tolerance
   - Uses content-aware prompts to ignore system UI
   - Merges individual files into final output

3. **Python Markdown** (`txt2md.py`):
   - Converts OCR text to AI-formatted Markdown
   - Generates intelligent filenames from book content
   - Copies to Google Drive sync folder automatically

## Troubleshooting

### Common Issues

1. **"Amazon Kindle app not running"**
   - Open Kindle app and navigate to the first page of your book
   - Make sure the book is in fullscreen reading mode

2. **"Gemini API key is required"**
   - Check your `config.env` file has the correct API key
   - Get a new key from [Google AI Studio](https://aistudio.google.com/app/apikey)

3. **"No screenshot images found"**
   - Make sure Kindle is visible on screen during AppleScript execution
   - Check that screenshots were created in Downloads folder

4. **"Script takes too long / infinite loop"**
   - Set `MAX_PAGES` to a reasonable number in the AppleScript
   - Don't rely on auto-detection for very long books

5. **"Wrong page order"**
   - The script now uses zero-padded filenames, this should be resolved
   - If using old screenshots, the Python script handles numeric sorting

### Debug Mode

Run components separately for debugging:

```bash
# Test screenshot capture only (check MAX_PAGES setting first)
osascript kindle2img.applescript

# Test OCR processing only
python3 img2txt.py /path/to/screenshots

# Test markdown conversion only
python3 txt2md.py /path/to/screenshots/ocr_output.txt --skip-drive

# Check individual OCR files if processing fails
ls /path/to/screenshots/ocr_*.txt
```

## Advanced Features

### Fault Tolerance

The system now creates individual OCR files for each page:

- If Gemini fails on page 15, you still have pages 1-14 processed
- Individual files can be manually reviewed or corrected
- Automatic merging creates the final output
- Resume processing from where it left off

### Smart Content Processing

- **System UI Filtering**: Ignores toolbars, menus, system status
- **Content-Aware OCR**: Focuses only on actual book text
- **Intelligent Spacing**: Fixes OCR spacing artifacts automatically
- **Structure Preservation**: Maintains paragraphs and flow

### AI-Generated Filenames

Instead of generic names, files are named based on content:
- `screenshot_1.png` → `GAFA Manager Interview Prep 1 Month.md`
- Extracted from book title and content using Gemini AI
- No length limitations, descriptive and meaningful

### Batch Processing

Process multiple screenshot folders:

```bash
for folder in ~/Downloads/Kindle_Screenshots_*/; do
  python3 kindle_ocr.py "$folder"
done
```

### Custom Google Drive Folder

```bash
python3 img2txt.py /path/to/screenshots \
  --drive-folder ~/Different/Drive/Folder
```

## Key Improvements

Recent major enhancements:

- ✅ **Google Gemini AI** - Replaced Google Vision API with more intelligent processing
- ✅ **Individual OCR files** - Fault tolerance and recovery capabilities
- ✅ **Smart filename generation** - AI-generated descriptive names
- ✅ **Zero-padded screenshots** - Proper file ordering (001, 002, 003...)
- ✅ **Content-aware prompts** - Ignores system UI, extracts only book content
- ✅ **Simplified Google Drive** - Uses local sync folder, no API complexity
- ✅ **Better error handling** - Graceful failures with partial results

## License

MIT License - Feel free to modify and distribute.