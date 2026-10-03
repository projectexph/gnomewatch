' Starts Gnomewatch with no window, and starts it again (up to 5 times) if it ever crashes.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Environment("Process")("SENTINEL_SUPERVISED") = "1"
exe = """" & here & "\Gnomewatch.exe"""
tries = 0
Do
    rc = sh.Run(exe, 0, True)          ' wait until it exits
    If rc = 0 Then Exit Do             ' a normal quit (tray > Quit): stay stopped
    tries = tries + 1
    If tries > 5 Then Exit Do
    WScript.Sleep 3000                 ' crashed or was stalled: start it again
Loop
