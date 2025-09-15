-- Kindle Screenshot Automation
-- Handles screenshot capture and page navigation only

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

-- Main screenshot automation script
set currentDate to do shell script "date +%Y%m%d_%H%M%S"
set folderPath to (POSIX path of (path to downloads folder)) & "Kindle_Screenshots_" & currentDate & "/"

log "Starting Kindle screenshot automation"
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
log "Screenshots saved to: " & folderPath

-- Return folder path for bash script to use
return folderPath