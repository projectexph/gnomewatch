' Removes Gnomewatch. Your settings (the "profiles" folder) are kept unless you say otherwise.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If MsgBox("Remove Gnomewatch from this PC?", vbYesNo + vbQuestion, "Gnomewatch") <> vbYes Then WScript.Quit
wipe = (MsgBox("Also delete your saved settings, accounts and logs?" & vbCrLf & "Choose No to keep them in case you reinstall.", vbYesNo + vbQuestion, "Gnomewatch") = vbYes)
On Error Resume Next
If fso.FileExists(here & "\running.flag") Then fso.DeleteFile here & "\running.flag", True
sh.Run "taskkill /F /IM Gnomewatch.exe", 0, True
sh.Run "powershell -NoProfile -WindowStyle Hidden -Command ""Get-CimInstance Win32_Process -Filter ""Name='wscript.exe'"" | Where-Object { $_.CommandLine -like '*Gnomewatch.vbs*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True
fso.DeleteFile sh.SpecialFolders("Desktop") & "\Gnomewatch.lnk", True
fso.DeleteFolder sh.SpecialFolders("Programs") & "\Gnomewatch", True
sh.RegDelete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Gnomewatch\"
' delete the program files (this script and the profiles folder last), then the folder itself from a helper
For Each f In fso.GetFolder(here).Files
    If LCase(f.Name) <> "uninstall gnomewatch.vbs" Then f.Delete True
Next
For Each d In fso.GetFolder(here).SubFolders
    If LCase(d.Name) <> "profiles" Or wipe Then d.Delete True
Next
cmd = "cmd /c ping -n 3 127.0.0.1 >nul & del /f /q """ & WScript.ScriptFullName & """ & rmdir """ & here & """"
sh.Run cmd, 0, False
MsgBox "Gnomewatch was removed." & IIf2(wipe), vbInformation, "Gnomewatch"
Function IIf2(w)
    If w Then IIf2 = "" Else IIf2 = vbCrLf & "Your settings were kept in " & here & "\profiles."
End Function
