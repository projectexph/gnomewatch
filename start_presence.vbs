' Shows "Coding Gnomewatch" on your Discord profile. Runs with no window; stop it with:  python gw_presence.py --stop
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
sh.Run """" & py & """ """ & here & "\gw_presence.py""", 0, False
