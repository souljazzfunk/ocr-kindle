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

-- Function to take full screen screenshot
on takeFullScreenshot(savePath)
	-- Take full screen screenshot (no interactive flags)
	do shell script "screencapture " & quoted form of savePath
end takeFullScreenshot

-- Simple test script - 3 pages only, full screen screenshots
set currentDate to do shell script "date +%Y%m%d_%H%M%S"
set folderPath to (POSIX path of (path to downloads folder)) & "Kindle_FullScreen_" & currentDate & "/"

log "Starting simple Kindle screenshot test (3 pages, full screen)"
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

-- Set test page count (reduced for testing)
set testPages to 3
log "Will capture " & testPages & " full screen screenshots"

-- Create new folder
createFolder(folderPath)
log "Created folder: " & folderPath

-- Bring Kindle app to front and ensure it's focused
tell application "Amazon Kindle" to activate
delay 2
log "Kindle app activated"

-- Take full screen screenshots
set screenshotPaths to {}
log "Starting screenshot capture..."

repeat with i from 1 to testPages
	set screenshotPath to folderPath & "fullscreen_" & i & ".png"
	
	log "Capturing full screen " & i & " of " & testPages
	
	-- Ensure Kindle is active window before screenshot
	tell application "Amazon Kindle" to activate
	delay 0.2
	
	-- Take full screen screenshot
	takeFullScreenshot(screenshotPath)
	
	-- Add screenshot path to list
	copy screenshotPath to end of screenshotPaths
	
	delay 0.5 -- Screenshot save time
	
	-- Turn page (except on last page)
	if i < testPages then
		log "Turning to next page..."
		tell application "System Events"
			keystroke keychar
			delay 0.5 -- Stabilization time after page turn
		end tell
	end if
end repeat

log "Test completed! " & testPages & " full screen screenshots saved to: " & folderPath
log "Opening results folder..."

-- Open the folder to show results
do shell script "open " & quoted form of folderPath