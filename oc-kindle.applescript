-- Function to create a new folder
on createFolder(folderPath)
	do shell script "mkdir -p " & quoted form of folderPath
end createFolder


-- Function to auto-detect page direction
on detectPageDirection()
	-- Get system locale
	set systemLocale to do shell script "defaults read -g AppleLocale"
	log "System locale: " & systemLocale
	
	-- Use right arrow for Japanese, Chinese, Arabic, etc.
	if systemLocale contains "ja" or systemLocale contains "zh" or systemLocale contains "ar" then
		log "Using right arrow for page direction"
		return (ASCII character 29) -- Right arrow
	else
		log "Using left arrow for page direction"
		return (ASCII character 28) -- Left arrow (default)
	end if
end detectPageDirection

-- Function to compare two images for similarity (simple file size comparison)
on compareImages(imagePath1, imagePath2)
	try
		set size1 to (do shell script "stat -f%z " & quoted form of imagePath1) as number
		set size2 to (do shell script "stat -f%z " & quoted form of imagePath2) as number
		set sizeDiff to abs(size1 - size2)
		set avgSize to (size1 + size2) / 2
		set similarity to 1 - (sizeDiff / avgSize)
		return similarity > 0.95 -- Consider same page if 95%+ similarity
	on error
		return false
	end try
end compareImages

-- Function to take full screen screenshot
on takeScreenshot(savePath)
	-- Take full screen screenshot (simple approach)
	do shell script "screencapture " & quoted form of savePath
end takeScreenshot

-- Function to delete image files
on deleteFiles(filePaths)
	repeat with f in filePaths
		do shell script "rm " & quoted form of f
	end repeat
end deleteFiles

-- Main script
set currentDate to do shell script "date +%Y%m%d_%H%M%S"
set folderPath to (POSIX path of (path to downloads folder)) & "Kindle_Screenshots_" & currentDate & "/"

log "Starting Kindle OCR automation"
log "Output folder: " & folderPath

-- Auto-detect page direction
set keychar to detectPageDirection()
set directionText to ""
if keychar = (ASCII character 28) then
	set directionText to "Left direction"
else
	set directionText to "Right direction"
end if
log "Page direction: " & directionText

-- Set maximum page count (for safety)
set maxPages to 500
log "Maximum pages set to: " & maxPages

-- Create new folder
createFolder(folderPath)
log "Created folder: " & folderPath

-- Bring Kindle app to front
tell application "Amazon Kindle" to activate
delay 2
log "Kindle app activated"

-- Take screenshots (with auto-end detection)
set screenshotPaths to {}
set pageCount to 0
set consecutiveSimilarPages to 0
set previousScreenshotPath to ""
log "Starting screenshot capture..."

repeat with i from 1 to maxPages
	set screenshotPath to folderPath & "screenshot_" & i & ".png"
	
	log "Capturing page " & i & " of " & maxPages
	
	-- Ensure Kindle is active before screenshot
	tell application "Amazon Kindle" to activate
	delay 0.2
	
	-- Take full screen screenshot
	takeScreenshot(screenshotPath)
	
	-- Compare with previous page to detect end
	if i > 1 then
		if compareImages(previousScreenshotPath, screenshotPath) then
			set consecutiveSimilarPages to consecutiveSimilarPages + 1
			if consecutiveSimilarPages ≥ 3 then
				-- If same page 3 times in a row, consider end of book
				do shell script "rm " & quoted form of screenshotPath
				log "End of book detected. Total " & (i - 1) & " pages"
				exit repeat
			end if
		else
			set consecutiveSimilarPages to 0
		end if
	end if
	
	-- Add screenshot path to list
	copy screenshotPath to end of screenshotPaths
	set previousScreenshotPath to screenshotPath
	set pageCount to i
	
	delay 0.5 -- Screenshot save time
	
	-- Turn page (except on last iteration)
	if i < maxPages then
		log "Turning to next page..."
		tell application "System Events"
			keystroke keychar
			delay 0.5 -- Stabilization time after page turn
		end tell
	end if
	
	-- Show progress (every 50 pages)
	if i mod 50 = 0 then
		log "Progress: " & i & " pages processed"
	end if
end repeat

-- Check final page count
if pageCount = maxPages then
	log "Reached maximum page count " & maxPages & ". Continuing processing."
end if

log "Screenshot capture completed. " & pageCount & " pages captured."

-- Python script content (sensitive info replaced with placeholders)
set ocr_script to "
# -*- coding: utf-8 -*-
import os
import subprocess
from markdown_it import MarkdownIt
from google.cloud import vision_v1
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Path configuration
folder_path = \"" & folderPath & "\"
ocr_output_file = folder_path + 'ocr_output.txt'

# Set environment variable (specify service account key path)
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/path/to/your/service-account.json'

# Configure Google Cloud Vision API client
vision_client = vision_v1.ImageAnnotatorClient()

# Execute OCR
def perform_ocr(image_path):
    with open(image_path, 'rb') as image_file:
        content = image_file.read()
    image = vision_v1.Image(content=content)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    if len(texts) > 0:
        return texts[0].description
    else:
        return ''

# Get list of image files
image_files = [f for f in os.listdir(folder_path) if f.startswith('screenshot_') and f.endswith('.png')]

# Execute OCR
ocr_text = ''
try:
    with open(ocr_output_file, 'w', encoding='utf-8') as output:
        for image in sorted(image_files):
            image_path = os.path.join(folder_path, image)
            print(f'Processing image: {image_path}')
            text = perform_ocr(image_path)
            ocr_text += text
            output.write(text)
except Exception as e:
    print(f'Error in OCR process: {e}')
    exit(1)

# Get first 10 characters for filename
try:
    file_name_prefix = ocr_text[:10].strip().replace(' ', '_').replace('/', '_')
    if not file_name_prefix:
        file_name_prefix = 'OCR_Output'
    md_output_file = folder_path + file_name_prefix + '.md'
    ocr_output_file = folder_path + file_name_prefix + '.txt'
    pdf_output_file = folder_path + file_name_prefix + '.pdf'
except Exception as e:
    print(f'Error in generating file names: {e}')
    exit(1)

# Markdown conversion
try:
    md = MarkdownIt()
    md_text = md.render(ocr_text)
    with open(md_output_file, 'w', encoding='utf-8') as output:
        output.write(md_text)
except Exception as e:
    print(f'Error in Markdown conversion: {e}')
    exit(1)

# Upload to Google Drive
try:
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    SERVICE_ACCOUNT_FILE = '/path/to/your/service-account.json'

    credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=credentials)

    # Google Drive folder ID (placeholder)
    folder_id = 'YOUR_FOLDER_ID'

    file_metadata = {
        'name': file_name_prefix,
        'parents': [folder_id],
        'mimeType': 'application/vnd.google-apps.document'
    }
    document = drive_service.files().create(body=file_metadata, fields='id').execute()
    doc_id = document['id']

    with open(md_output_file, 'r', encoding='utf-8') as md_file:
        md_content = md_file.read()

    docs_service = build('docs', 'v1', credentials=credentials)
    requests = [{'insertText': {'location': {'index': 1}, 'text': md_content}}]
    docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
    print(f'Document created: {doc_id}')
except Exception as e:
    print(f'Error in uploading to Google Docs: {e}')
    exit(1)

# Create PDF
try:
    img_files = [os.path.join(folder_path, f) for f in image_files]
    subprocess.run(['/opt/homebrew/bin/img2pdf'] + img_files + ['-o', pdf_output_file], check=True)
except Exception as e:
    print(f'Error in PDF creation: {e}')
    exit(1)
"

-- Set temporary Python file path
set tempPythonFile to folderPath & "ocr_script.py"

-- Write out Python script (using printf)
do shell script "printf %s " & quoted form of ocr_script & " > " & quoted form of tempPythonFile

-- Check file contents (comment out if needed)
do shell script "cat " & quoted form of tempPythonFile

-- Execute Python script (using python3 command)
try
	do shell script "python3 " & quoted form of tempPythonFile & " 2>&1"
on error error_message
	-- Save error message to log
	set errorLogFile to folderPath & "error_log.txt"
	set fileDescriptor to open for access (errorLogFile as POSIX file) with write permission
	write error_message to fileDescriptor
	close access fileDescriptor
	log "An error occurred during OCR processing. Check error log at: " & errorLogFile
	return
end try

-- Delete temporary Python file after script execution
do shell script "rm " & quoted form of tempPythonFile

-- Delete screenshot images
deleteFiles(screenshotPaths)

-- Final message
log "Process completed successfully!"
log "Screenshots captured, processed with OCR, and uploaded to Google Docs."
log "Files saved to: " & folderPath