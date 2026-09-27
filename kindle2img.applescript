-- Captures the Kindle for Mac window page by page until the book ends.
-- Usage: osascript kindle2img.applescript [ltr|rtl] [folder]
--   ltr: horizontal books (English etc.), next page is the right arrow. rtl (default): vertical Japanese.
--   folder: continue an existing capture, e.g. the folder img2txt.py extracted from the extension's ZIP.
--           Numbering resumes after its highest page number. Without it, a new Downloads folder is made.

-- Crop the title bar, running header and reading-progress footer (points).
property TOP_MARGIN : 70
property BOTTOM_MARGIN : 40
property LEFT_MARGIN : 0
property RIGHT_MARGIN : 0
-- Safety cap; capture normally stops when a page turn no longer changes the screen.
property MAX_PAGES : 3000

-- The capture is a screen region, so another window in front would be captured and would take the arrow key.
on focusKindle()
	tell application "System Events" to if frontmost of process "Kindle" then return
	tell application "Amazon Kindle" to activate
	repeat 40 times
		delay 0.25
		tell application "System Events" to if frontmost of process "Kindle" then exit repeat
	end repeat
	tell application "System Events" to if not (frontmost of process "Kindle") then error "Kindle did not come to the front"
	delay 0.5 -- let the window finish drawing over the previous app
end focusKindle

on takeScreenshot(savePath)
	focusKindle()
	tell application "System Events" to tell process "Kindle"
		set {x1, y1} to position of window 1
		set {w, h} to size of window 1
	end tell
	set geo to ((x1 + LEFT_MARGIN) as text) & "," & ((y1 + TOP_MARGIN) as text) & "," & ¬
		((w - LEFT_MARGIN - RIGHT_MARGIN) as text) & "," & ((h - TOP_MARGIN - BOTTOM_MARGIN) as text)
	do shell script "screencapture -x -R " & geo & " " & quoted form of savePath
end takeScreenshot

on run argv
	set direction to "rtl"
	if (count of argv) > 0 then set direction to item 1 of argv
	if direction is "ltr" then
		set keyCodeNext to 124
	else if direction is "rtl" then
		set keyCodeNext to 123
	else
		error "Direction must be ltr or rtl, got " & direction
	end if

	if (count of argv) > 1 then
		set folderPath to item 2 of argv
	else
		set folderPath to (POSIX path of (path to downloads folder)) & "Kindle_Screenshots_" & (do shell script "date +%Y%m%d_%H%M%S")
	end if
	if folderPath does not end with "/" then set folderPath to folderPath & "/"
	do shell script "mkdir -p " & quoted form of folderPath

	-- Any <prefix>_<number>.png counts, so this continues both page_0136.png from the extension and earlier runs.
	set lastNumber to do shell script "ls " & quoted form of folderPath & " | sed -n 's/^.*_0*\\([0-9][0-9]*\\)\\.png$/\\1/p' | sort -n | tail -1"
	if lastNumber is "" then set lastNumber to "0"
	set pageNumber to (lastNumber as integer)

	focusKindle()
	delay 1

	set previousPath to ""
	repeat MAX_PAGES times
		set pageNumber to pageNumber + 1
		set screenshotPath to folderPath & "page_" & text -4 thru -1 of ("0000" & pageNumber) & ".png"
		takeScreenshot(screenshotPath)
		if previousPath is not "" then
			if (do shell script "cmp -s " & quoted form of previousPath & " " & quoted form of screenshotPath & " && echo same || true") is "same" then
				do shell script "rm " & quoted form of screenshotPath
				set pageNumber to pageNumber - 1
				exit repeat
			end if
		end if
		set previousPath to screenshotPath
		focusKindle()
		tell application "System Events" to key code keyCodeNext
		delay 1
	end repeat

	return "Captured up to page_" & text -4 thru -1 of ("0000" & pageNumber) & ".png in " & folderPath
end run
