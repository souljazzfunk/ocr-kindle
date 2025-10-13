-- Kindle Screenshot Automation
-- Handles screenshot capture and page navigation only

-- CONFIGURATION: Set the number of pages to capture
-- Modify this value based on your book length
set MAX_PAGES to 100

-- CONFIGURATION: Set page direction
-- "LEFT" for left-to-right languages (English, etc.)
-- "RIGHT" for right-to-left languages (Japanese, Arabic, etc.)
set PAGE_DIRECTION to "RIGHT"

-- CONFIGURATION: Set margin offsets to crop header/footer (in pixels)
-- Adjust these values to remove unwanted UI elements
set TOP_MARGIN to 70
set BOTTOM_MARGIN to 40
set LEFT_MARGIN to 0
set RIGHT_MARGIN to 0

-- CONFIGURATION: Continue mode - set to true to continue in existing folder
set CONTINUE_MODE to true

-- CONFIGURATION: Folder path for continue mode (only used when CONTINUE_MODE is true)
-- Example: "/Users/username/Downloads/Agile_Data_Warehouse_Design"
set CONTINUE_FOLDER_PATH to ""

-- Function to create a new folder
on createFolder(folderPath)
	do shell script "mkdir -p " & quoted form of folderPath
end createFolder

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


-- Function to take Kindle window screenshot
on takeScreenshot(savePath)
	global TOP_MARGIN, BOTTOM_MARGIN, LEFT_MARGIN, RIGHT_MARGIN
	try
		-- Get the position and size of the Kindle window through System Events
		tell application "System Events"
			tell process "Kindle"
				set winPosition to position of window 1
				set winSize to size of window 1
			end tell
		end tell

		-- Extract coordinates
		set x1 to item 1 of winPosition
		set y1 to item 2 of winPosition
		set w to item 1 of winSize
		set h to item 2 of winSize

		-- Apply margin offsets to crop header/footer
		set x1 to x1 + LEFT_MARGIN
		set y1 to y1 + TOP_MARGIN
		set w to w - LEFT_MARGIN - RIGHT_MARGIN
		set h to h - TOP_MARGIN - BOTTOM_MARGIN

		-- Build the geometry string for screencapture -R
		set geo to (x1 as text) & "," & (y1 as text) & "," & (w as text) & "," & (h as text)

		log "Capturing Kindle window at: " & geo & " (with margins applied)"

		-- Capture the specific window region
		do shell script "screencapture -x -R " & geo & " " & quoted form of savePath
	on error errMsg
		-- Fallback to full screen if window bounds can't be determined
		log "Warning: Could not get window position, falling back to full screen. Error: " & errMsg
		do shell script "screencapture " & quoted form of savePath
	end try
end takeScreenshot

-- Function to delete image files
on deleteFiles(filePaths)
	repeat with f in filePaths
		do shell script "rm " & quoted form of f
	end repeat
end deleteFiles

-- Main screenshot automation script
log "Starting Kindle screenshot automation"

-- Determine folder path and starting number
set startNumber to 1
if CONTINUE_MODE then
	set folderPath to CONTINUE_FOLDER_PATH
	-- Ensure folder path ends with /
	if folderPath does not end with "/" then
		set folderPath to folderPath & "/"
	end if
	log "Continue mode: Using existing folder"
	log "Output folder: " & folderPath

	-- Find the last screenshot number in the folder
	try
		set lastScreenshot to do shell script "ls " & quoted form of folderPath & " | grep '^screenshot_[0-9]\\+\\.png$' | sort -V | tail -1"
		if lastScreenshot is not "" then
			-- Extract number from filename (screenshot_XXX.png)
			set lastNumber to do shell script "echo " & quoted form of lastScreenshot & " | sed 's/screenshot_\\([0-9]*\\)\\.png/\\1/' | sed 's/^0*//' "
			if lastNumber is "" then
				set lastNumber to "0"
			end if
			set startNumber to (lastNumber as number) + 1
			log "Last screenshot found: " & lastScreenshot & ", starting from number: " & startNumber
		else
			log "No existing screenshots found, starting from 1"
		end if
	on error errMsg
		log "Warning: Could not detect last screenshot number. Error: " & errMsg
		log "Starting from 1"
	end try
else
	set currentDate to do shell script "date +%Y%m%d_%H%M%S"
	set folderPath to (POSIX path of (path to downloads folder)) & "Kindle_Screenshots_" & currentDate & "/"
	log "Creating new folder: " & folderPath
	createFolder(folderPath)
end if

-- Set page direction based on configuration
if PAGE_DIRECTION = "RIGHT" then
	set keychar to (ASCII character 29) -- Right arrow
	set directionText to "Right direction"
else
	set keychar to (ASCII character 28) -- Left arrow
	set directionText to "Left direction"
end if
log "Page direction: " & directionText

-- Bring Kindle app to front
tell application "Amazon Kindle" to activate
delay 2
log "Kindle app activated"

-- Use configured page count
set maxPages to MAX_PAGES
set endNumber to startNumber + maxPages - 1
log "Capturing " & maxPages & " pages (from " & startNumber & " to " & endNumber & ")"

-- Take screenshots
set screenshotPaths to {}
log "Starting screenshot capture..."

set pageCount to 0
repeat with i from startNumber to endNumber
	set pageCount to pageCount + 1

	-- Create zero-padded filename (001, 002, etc.)
	set paddedNumber to text -3 thru -1 of ("000" & i)
	set screenshotPath to folderPath & "screenshot_" & paddedNumber & ".png"

	log "Capturing page " & pageCount & " of " & maxPages & " (file: screenshot_" & paddedNumber & ".png)"

	-- Ensure Kindle is active before screenshot
	tell application "Amazon Kindle" to activate
	delay 0.2

	-- Take full screen screenshot
	takeScreenshot(screenshotPath)

	-- Add screenshot path to list
	copy screenshotPath to end of screenshotPaths

	delay 0.5 -- Screenshot save time

	-- Turn page (except on last iteration)
	if pageCount < maxPages then
		log "Turning to next page..."
		tell application "System Events"
			keystroke keychar
			delay 0.5 -- Stabilization time after page turn
		end tell
	end if

	-- Show progress (every 10 pages)
	if pageCount mod 10 = 0 then
		log "Progress: " & pageCount & " pages processed"
	end if
end repeat

log "Screenshot capture completed. " & maxPages & " pages captured (screenshot_" & text -3 thru -1 of ("000" & startNumber) & ".png to screenshot_" & text -3 thru -1 of ("000" & endNumber) & ".png)"
log "Screenshots saved to: " & folderPath

-- Return folder path for bash script to use
return folderPath