-- Function to create a new folder
on createFolder(folderPath)
	do shell script "mkdir -p " & quoted form of folderPath
end createFolder

-- Function to get Kindle window bounds (works with multiple monitors)
on getKindleWindowBounds()
	tell application "Amazon Kindle"
		activate
		delay 2
	end tell
	
	-- Try to get window position and size using properties
	try
		tell application "System Events"
			tell process "Amazon Kindle"
				log "Checking for Kindle windows..."
				set windowCount to count of windows
				log "Found " & windowCount & " Kindle windows"
				
				if windowCount > 0 then
					-- Get window properties instead of bounds
					repeat with i from 1 to windowCount
						try
							set currentWindow to window i
							set windowName to name of currentWindow
							set windowPos to position of currentWindow
							set windowSize to size of currentWindow
							
							-- Calculate bounds from position and size
							set windowX to item 1 of windowPos
							set windowY to item 2 of windowPos
							set windowWidth to item 1 of windowSize
							set windowHeight to item 2 of windowSize
							set windowBounds to {windowX, windowY, windowX + windowWidth, windowY + windowHeight}
							
							log "Window " & i & ": " & windowName
							log "Position: " & windowX & "," & windowY
							log "Size: " & windowWidth & "x" & windowHeight
							log "Calculated bounds: " & (item 1 of windowBounds) & "," & (item 2 of windowBounds) & "," & (item 3 of windowBounds) & "," & (item 4 of windowBounds)
							
							-- Validate bounds are reasonable
							if windowWidth > 100 and windowHeight > 100 then
								log "Using window: " & windowName
								return windowBounds
							end if
						on error windowError
							log "Could not get properties for window " & i & ": " & windowError
						end try
					end repeat
				end if
			end tell
		end tell
		
		error "Could not get window properties"
		
	on error finalError
		log "Complete failure in window detection: " & finalError
		-- Return fallback coordinates
		log "Using fallback coordinates - please position Kindle window and adjust if needed"
		return {100, 100, 1400, 900} -- Fallback coordinates
	end try
end getKindleWindowBounds

-- Function to automatically calculate capture area
on calculateCaptureRect()
	set windowBounds to getKindleWindowBounds()
	set windowX to item 1 of windowBounds
	set windowY to item 2 of windowBounds
	set windowWidth to (item 3 of windowBounds) - windowX
	set windowHeight to (item 4 of windowBounds) - windowY
	
	log "Window dimensions: " & windowWidth & "x" & windowHeight & " at position " & windowX & "," & windowY
	
	-- Calculate content area margins (excluding UI elements like toolbar)
	set marginX to round (windowWidth * 0.08) -- 8% margin left/right for UI
	set marginY to round (windowHeight * 0.15) -- 15% margin top/bottom for title bar and controls
	set contentWidth to windowWidth - (marginX * 2)
	set contentHeight to windowHeight - (marginY * 2)
	
	set captureX to windowX + marginX
	set captureY to windowY + marginY
	
	set captureRect to (captureX as string) & "," & (captureY as string) & "," & (contentWidth as string) & "," & (contentHeight as string)
	log "Calculated capture area: " & captureRect
	return captureRect
end calculateCaptureRect

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

-- Function to take screenshot
on takeScreenshot(savePath, captureRect)
	-- captureRect is a string in "x,y,width,height" format
	do shell script "screencapture -R " & captureRect & " " & quoted form of savePath
end takeScreenshot

-- Test script - 10 pages only
set currentDate to do shell script "date +%Y%m%d_%H%M%S"
set folderPath to (POSIX path of (path to desktop folder)) & "Kindle_Test_" & currentDate & "/"

log "Starting Kindle screenshot test (10 pages only)"
log "Output folder: " & folderPath

-- Auto-calculate capture area
set captureRect to calculateCaptureRect()

-- Auto-detect page direction
set keychar to detectPageDirection()
set directionText to ""
if keychar = (ASCII character 28) then
	set directionText to "Left direction"
else
	set directionText to "Right direction"
end if
log "Page direction: " & directionText

-- Set test page count
set testPages to 10
log "Will capture " & testPages & " pages"

-- Create new folder
createFolder(folderPath)
log "Created folder: " & folderPath

-- Bring Kindle app to front and ensure it's focused
tell application "Amazon Kindle" to activate
delay 2
log "Kindle app activated"

-- Take screenshots (test version - 10 pages only)
set screenshotPaths to {}
log "Starting screenshot capture..."

repeat with i from 1 to testPages
	set screenshotPath to folderPath & "test_screenshot_" & i & ".png"
	
	log "Capturing page " & i & " of " & testPages
	
	-- Take screenshot
	takeScreenshot(screenshotPath, captureRect)
	
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

log "Test completed! " & testPages & " screenshots saved to: " & folderPath
log "Opening results folder..."

-- Open the folder to show results
do shell script "open " & quoted form of folderPath